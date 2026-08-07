"""Transparante, weegbare scorekaart (spec §4/§8) — geen black box.

Drie componenten, elk 0-100, elk met een `basis`-string die exact zegt
waaruit de score is opgebouwd. Het totaal is de gewogen som met de
gewichten uit thesis.yaml; ontbreekt een component (geen data), dan worden
de gewichten over de beschikbare componenten hernormaliseerd en staat dat
in de basis. Alle constanten staan hier bovenaan en in METHODOLOGY.
"""

from dataclasses import dataclass

DEFAULT_WEIGHTS = {"financial": 0.5, "growth": 0.2, "signals": 0.3}

# financieel: percentielposities in de eigen peer set (hoger = beter,
# behalve nettoschuld: lager = beter, dus geïnverteerd)
FINANCIAL_METRICS = {
    "ebitda_proxy": False,        # False = niet inverteren
    "revenue": False,
    "net_financial_debt": True,   # inverteren
}

GROWTH_FULL_SCALE = 0.20          # 20%/jaar of meer = 100 punten; <= 0% = 0

# distress-penalty's per signaaltype (handmatige signalen hebben onbekende
# polariteit en krijgen bewust GEEN automatische penalty)
SIGNAL_PENALTIES = {
    "chronic_late_filing": 25,
    "late_filing": 10,            # per boekjaar, geplafonneerd
    "model_switch_full_to_abbreviated": 15,
    "correction_filing": 5,
    "irregular_fiscal_year_length": 5,
    "fiscal_year_end_changed": 5,
}
LATE_FILING_CAP = 20


@dataclass
class ComponentScore:
    value: float | None
    basis: str


def financial_score(percentiles: dict[str, float | None]) -> ComponentScore:
    """Gemiddelde van de beschikbare peer-percentielen × 100."""
    parts = []
    used = []
    for metric, invert in FINANCIAL_METRICS.items():
        p = percentiles.get(metric)
        if p is None:
            continue
        eff = 1.0 - p if invert else p
        parts.append(eff)
        used.append(f"{metric}={'1-' if invert else ''}p{p:.2f}")
    if not parts:
        return ComponentScore(None, "geen peer-percentielen beschikbaar")
    value = sum(parts) / len(parts) * 100
    return ComponentScore(round(value, 1), "gem. van " + ", ".join(used))


def growth_score(series: list[tuple[int, float]], metric_name: str = "") -> ComponentScore:
    """CAGR over de beschikbare jaren, lineair geschaald: 0% -> 0,
    >= 20%/jaar -> 100. Vereist >= 2 jaren met positieve waarden."""
    series = sorted((y, v) for y, v in series if v is not None)
    if len(series) < 2:
        return ComponentScore(None, f"minder dan 2 boekjaren met {metric_name or 'waarde'}")
    (y0, v0), (y1, v1) = series[0], series[-1]
    if v0 <= 0 or v1 <= 0:
        return ComponentScore(None, f"{metric_name}: niet-positieve waarden ({v0}, {v1}) — CAGR onbepaald")
    years = y1 - y0
    cagr = (v1 / v0) ** (1 / years) - 1
    value = max(0.0, min(1.0, cagr / GROWTH_FULL_SCALE)) * 100
    return ComponentScore(
        round(value, 1),
        f"{metric_name} CAGR {cagr:+.1%} over {years} jaar ({y0}: {v0:,.0f} -> {y1}: {v1:,.0f})",
    )


def signals_score(signal_counts: dict[str, int]) -> ComponentScore:
    """Start op 100; trek per distress-signaal de penalty af (vloer 0)."""
    penalties = []
    total = 0
    for signal, count in sorted(signal_counts.items()):
        if signal not in SIGNAL_PENALTIES or count == 0:
            continue
        p = SIGNAL_PENALTIES[signal] * count
        if signal == "late_filing":
            p = min(p, LATE_FILING_CAP)
        penalties.append(f"{signal}×{count}=-{p}")
        total += p
    value = max(0.0, 100.0 - total)
    basis = "100 - " + " - ".join(p.split("=-")[1] for p in penalties) + \
            f" ({'; '.join(penalties)})" if penalties else "100 (geen distress-signalen)"
    return ComponentScore(round(value, 1), basis)


def total_score(components: dict[str, ComponentScore],
                weights: dict[str, float] | None = None) -> ComponentScore:
    """Gewogen som; bij ontbrekende componenten hernormaliseren.

    Uitzondering: zonder financiële component is er GEEN totaalscore —
    anders zou een bedrijf met minder data (alleen een signaalscore van
    100) boven volledig gescoorde bedrijven ranken. Onvoldoende data mag
    nooit een hogere rang opleveren.
    """
    weights = dict(weights) if weights else dict(DEFAULT_WEIGHTS)
    available = {k: c for k, c in components.items()
                 if c.value is not None and weights.get(k, 0) > 0}
    if not available:
        return ComponentScore(None, "geen enkele scorecomponent berekenbaar")
    if weights.get("financial", 0) > 0 and "financial" not in available:
        return ComponentScore(
            None,
            "financiële component ontbreekt (geen peer-percentielen) — "
            "onvoldoende data om te ranken",
        )
    weight_sum = sum(weights[k] for k in available)
    value = sum(weights[k] / weight_sum * c.value for k, c in available.items())
    parts = [f"{k} {c.value} × {weights[k] / weight_sum:.2f}" for k, c in available.items()]
    missing = [k for k in weights if k not in available and weights.get(k, 0) > 0]
    basis = " + ".join(parts)
    if missing:
        basis += f" (hernormaliseerd; ontbreekt: {missing})"
    return ComponentScore(round(value, 1), basis)


def classify_target(fte: float | None, platform_criteria: dict,
                    bolt_on_criteria: dict) -> str | None:
    """Platform- vs bolt-on-classificatie op de thesis-criteria (nu: VTE)."""
    if fte is None:
        return None
    platform_min = platform_criteria.get("fte_min")
    bolt_on_max = bolt_on_criteria.get("fte_max")
    if platform_min is not None and fte >= platform_min:
        return "platform"
    if bolt_on_max is not None and fte <= bolt_on_max:
        return "bolt_on"
    return "tussenmaat"
