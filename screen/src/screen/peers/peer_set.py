"""Peer-set-opbouw (spec §6).

Twee stappen, beide met expliciete criteria in de output:

1. universe  — KBO-selectie op de thesis: hoofdactiviteit (MAIN) in de
   gekozen NACE-versie met prefixmatch, geografie (provincie uit postcode),
   status actief, vervuilingsfilter (holdings 64.20 / managementvennoot-
   schappen 70.10 eruit), plus overrides.yaml (include/exclude en
   nace_override). Elke onderneming draagt de 1-op-veel-conversievlag
   (null zolang de Statbel-brug niet geladen is).

2. peers     — universe ∩ beschikbare financials (metrics), met de
   financiële vervuilingsfilters: personeelskost > 0 en materiële vaste
   activa > drempel. Streefgrootte 20-40 (spec); daarbuiten volgt een
   expliciete waarschuwing, geen stille voortzetting.

De selectiecriteria en tellingen worden als JSON naast de parquet gezet
(marts/peer_criteria.json) — criteria expliciet in de output.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from ..config import INTERIM_DIR, MARTS_DIR, Thesis
from .geo import province_for_zipcode
from .nace_bridge import NaceBridge

UNIVERSE_PATH = INTERIM_DIR / "universe.parquet"
PEERS_PATH = MARTS_DIR / "peers.parquet"
CRITERIA_PATH = MARTS_DIR / "peer_criteria.json"

HOLDING_PREFIXES = ("6420", "7010")  # holdings & managementvennootschappen
PEER_TARGET_RANGE = (20, 40)


class PeerSetError(Exception):
    pass


@dataclass
class PeerSetResult:
    universe_path: Path | None = None
    peers_path: Path | None = None
    universe_count: int = 0
    with_financials_count: int = 0
    peer_count: int = 0
    warnings: list[str] = field(default_factory=list)


def _norm_number(value: str) -> str:
    return str(value).replace(".", "").replace(" ", "").strip()


def _norm_nace(value: str) -> str:
    return str(value).replace(".", "").strip()


def build_universe(thesis: Thesis, overrides: dict | None = None,
                   bridge: NaceBridge | None = None,
                   kbo_dir: Path | None = None,
                   progress=print) -> pl.DataFrame:
    """KBO-parquets -> universe-DataFrame (en universe.parquet)."""
    kbo_dir = kbo_dir or (INTERIM_DIR / "kbo")
    required = {name: kbo_dir / f"{name}.parquet"
                for name in ("enterprise", "activity", "address")}
    missing = [n for n, p in required.items() if not p.exists()]
    if missing:
        raise PeerSetError(
            f"KBO-parquets ontbreken ({missing}) — draai eerst screen ingest kbo && screen build"
        )

    enterprise = pl.read_parquet(required["enterprise"])
    activity = pl.read_parquet(required["activity"])
    address = pl.read_parquet(required["address"])
    denom_path = kbo_dir / "denomination.parquet"
    denomination = pl.read_parquet(denom_path) if denom_path.exists() else None

    overrides = overrides or {}
    version = str(thesis.nace_version)

    main = (
        activity.filter(
            (pl.col("Classification") == "MAIN") & (pl.col("NaceVersion") == version)
        )
        .with_columns(
            pl.col("EntityNumber").cast(pl.Utf8).str.replace_all(".", "", literal=True)
            .str.replace_all(" ", "", literal=True).str.strip_chars()
            .alias("enterprise_number"),
            pl.col("NaceCode").cast(pl.Utf8).str.replace_all(".", "", literal=True)
            .str.strip_chars().alias("nace"),
        )
        .group_by("enterprise_number")
        .agg(pl.col("nace").first())
    )

    # 2008-hoofdcode apart, voor de conversievlag
    main_2008 = (
        activity.filter(
            (pl.col("Classification") == "MAIN") & (pl.col("NaceVersion") == "2008")
        )
        .with_columns(
            pl.col("EntityNumber").cast(pl.Utf8).str.replace_all(".", "", literal=True)
            .str.replace_all(" ", "", literal=True).str.strip_chars()
            .alias("enterprise_number"),
            pl.col("NaceCode").cast(pl.Utf8).str.replace_all(".", "", literal=True)
            .str.strip_chars().alias("nace_2008"),
        )
        .group_by("enterprise_number")
        .agg(pl.col("nace_2008").first())
    )

    ent = enterprise.with_columns(
        pl.col("EnterpriseNumber").map_elements(_norm_number, return_dtype=pl.Utf8)
        .alias("enterprise_number")
    ).select("enterprise_number", pl.col("Status").alias("status"))

    addr = (
        address.with_columns(
            pl.col("EntityNumber").cast(pl.Utf8).str.replace_all(".", "", literal=True)
            .str.replace_all(" ", "", literal=True).str.strip_chars()
            .alias("enterprise_number")
        )
        .group_by("enterprise_number")
        .agg(pl.col("Zipcode").first().alias("zipcode"))
        .with_columns(
            pl.col("zipcode").map_elements(province_for_zipcode, return_dtype=pl.Utf8)
            .alias("province")
        )
    )

    df = (
        main.join(ent, on="enterprise_number", how="left")
        .join(addr, on="enterprise_number", how="left")
        .join(main_2008, on="enterprise_number", how="left")
    )
    if denomination is not None:
        names = (
            denomination.with_columns(
                pl.col("EntityNumber").map_elements(_norm_number, return_dtype=pl.Utf8)
                .alias("enterprise_number")
            )
            .group_by("enterprise_number")
            .agg(pl.col("Denomination").first().alias("name"))
        )
        df = df.join(names, on="enterprise_number", how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("name"))

    # overrides: nace_override past de effectieve code aan
    def effective_nace(row: dict) -> str:
        ov = overrides.get(row["enterprise_number"]) or {}
        return _norm_nace(ov.get("nace_override", "")) or row["nace"]

    df = df.with_columns(
        pl.struct(["enterprise_number", "nace"])
        .map_elements(effective_nace, return_dtype=pl.Utf8)
        .alias("nace_effective")
    )

    prefixes = tuple(thesis.nace_prefixes)
    df = df.with_columns(
        pl.col("nace_effective").str.starts_with(prefixes[0]).alias("_m0")
        if len(prefixes) == 1 else
        pl.any_horizontal([pl.col("nace_effective").str.starts_with(p) for p in prefixes])
        .alias("_m0"),
        (pl.col("status") == "AC").alias("crit_active"),
        (
            pl.any_horizontal([pl.col("nace_effective").str.starts_with(p)
                               for p in HOLDING_PREFIXES]).not_()
        ).alias("crit_not_holding"),
    ).rename({"_m0": "crit_nace_match"})

    if thesis.provinces:
        df = df.with_columns(pl.col("province").is_in(thesis.provinces).alias("crit_geo"))
    else:
        df = df.with_columns(pl.lit(True).alias("crit_geo"))

    # conversievlag (null zonder brug of zonder 2008-code)
    if bridge:
        df = df.with_columns(
            pl.col("nace_2008")
            .map_elements(lambda c: bridge.is_one_to_many(c) if c else None,
                          return_dtype=pl.Boolean)
            .alias("nace_conversion_ambiguous")
        )
    else:
        df = df.with_columns(
            pl.lit(None, dtype=pl.Boolean).alias("nace_conversion_ambiguous")
        )

    ov_include = {n for n, o in overrides.items() if (o or {}).get("include") is True}
    ov_exclude = {n for n, o in overrides.items() if (o or {}).get("include") is False}
    df = df.with_columns(
        pl.col("enterprise_number").is_in(list(ov_include) or [""]).alias("override_included"),
        pl.col("enterprise_number").is_in(list(ov_exclude) or [""]).alias("override_excluded"),
    )

    selected = df.filter(
        (
            pl.col("crit_nace_match") & pl.col("crit_active")
            & pl.col("crit_not_holding") & pl.col("crit_geo")
            & ~pl.col("override_excluded")
        )
        | pl.col("override_included")
    ).drop("nace")

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    selected.write_parquet(UNIVERSE_PATH)
    progress(f"  → universe.parquet: {selected.height} ondernemingen")
    return selected


def compute_revenue_ratio(universe: pl.DataFrame, facts_path: Path | None = None,
                          min_n: int = 5) -> tuple[float | None, str]:
    """Peer-mediaan van omzet/brutomarge uit volledig-schema-peers.

    Brutomarge bestaat niet als rubriek in het volledig schema; we gebruiken
    de proxy 70 − 60 − 61 (zie METHODOLOGY). Retourneert (ratio, note);
    (None, uitleg) bij te weinig peers — er wordt dan niet geschat.
    """
    facts_path = facts_path or (INTERIM_DIR / "facts.parquet")
    if not Path(facts_path).exists():
        return None, "geen facts.parquet — ratio niet berekenbaar"
    facts = pl.read_parquet(facts_path)
    peers = set(universe["enterprise_number"].to_list())

    wide = (
        facts.filter(
            pl.col("is_latest") & (pl.col("period") == "N")
            & (pl.col("schema_type") == "volledig")
            & pl.col("enterprise_number").is_in(list(peers) or [""])
            & pl.col("rubric_code").is_in(["70", "60", "61"])
        )
        .pivot(values="value", index=["enterprise_number", "fiscal_year"],
               on="rubric_code")
    )
    for col in ("70", "60", "61"):
        if col not in wide.columns:
            return None, f"rubriek {col} ontbreekt bij alle volledig-schema-peers"
    wide = wide.drop_nulls(["70", "60", "61"]).with_columns(
        (pl.col("70") - pl.col("60") - pl.col("61")).alias("brutomarge_proxy")
    ).filter(pl.col("brutomarge_proxy") > 0).with_columns(
        (pl.col("70") / pl.col("brutomarge_proxy")).alias("ratio")
    )
    n = wide.height
    if n < min_n:
        return None, (f"slechts {n} volledig-schema-peer(s) met bruikbare cijfers "
                      f"(minimum {min_n}) — omzet wordt niet geschat")
    ratio = float(wide["ratio"].median())
    q1, q3 = float(wide["ratio"].quantile(0.25)), float(wide["ratio"].quantile(0.75))
    return ratio, f"peer-mediaan omzet/brutomarge (n={n}, IQR {q1:.2f}-{q3:.2f})"


def build_peers(universe: pl.DataFrame, thesis: Thesis,
                metrics_path: Path | None = None, mva_min: float = 0.0,
                progress=print) -> PeerSetResult:
    """universe ∩ financials, met vervuilingsfilters -> marts/peers.parquet."""
    result = PeerSetResult(universe_path=UNIVERSE_PATH, universe_count=universe.height)
    metrics_path = metrics_path or (INTERIM_DIR / "metrics.parquet")
    if not Path(metrics_path).exists():
        result.warnings.append("geen metrics.parquet — peers niet bepaald")
        progress(f"  ! {result.warnings[-1]}")
        return result

    metrics = pl.read_parquet(metrics_path)
    latest = (
        metrics.sort("fiscal_year")
        .group_by("enterprise_number", maintain_order=True)
        .agg(pl.all().last())
    )
    joined = universe.join(latest, on="enterprise_number", how="inner")
    result.with_financials_count = joined.height

    peers = joined.filter(
        (pl.col("personnel_cost") > 0)
        & pl.col("tangible_fixed_assets").is_not_null()
        & (pl.col("tangible_fixed_assets") > mva_min)
    )
    result.peer_count = peers.height

    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    peers.write_parquet(PEERS_PATH)
    result.peers_path = PEERS_PATH

    lo, hi = PEER_TARGET_RANGE
    if result.peer_count < lo:
        result.warnings.append(
            f"peer set telt {result.peer_count} bedrijven (< {lo}) — kwartielen "
            "zijn dan weinig robuust; verbreed de NACE-codes of geografie"
        )
    elif result.peer_count > hi:
        result.warnings.append(
            f"peer set telt {result.peer_count} bedrijven (> {hi}) — overweeg "
            "strakkere criteria voor een homogenere vergelijking"
        )

    criteria = {
        "thesis": thesis.name,
        "nace_version": thesis.nace_version,
        "nace_codes": thesis.nace_codes,
        "provinces": thesis.provinces,
        "filters": {
            "hoofdactiviteit": "Classification == MAIN",
            "status": "AC",
            "vervuilingsfilter_nace": list(HOLDING_PREFIXES),
            "personeelskost": "> 0",
            "materiele_vaste_activa": f"> {mva_min}",
        },
        "counts": {
            "universe": result.universe_count,
            "met_financials": result.with_financials_count,
            "peers": result.peer_count,
        },
        "warnings": result.warnings,
    }
    CRITERIA_PATH.write_text(json.dumps(criteria, ensure_ascii=False, indent=1))
    progress(f"  → peers.parquet: {result.peer_count} peers "
             f"(universe {result.universe_count}, met financials {result.with_financials_count})")
    for w in result.warnings:
        progress(f"  ! {w}")
    return result
