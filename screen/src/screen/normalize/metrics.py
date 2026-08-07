"""Financiële normalisatie — het inhoudelijke hart (spec §5).

Pure functies: input is een mapping rubriekcode -> waarde voor één
(onderneming, boekjaar) op basis van de laatste neerlegging (is_latest,
periode N). Output is altijd een Metric met expliciete herkomst:

  source     'nbb'      rechtstreeks gerapporteerde waarde
             'derived'  rekenkundig afgeleid uit gerapporteerde waarden
             'estimate' schatting (bv. omzetproxy) — NOOIT als feit tonen
  confidence 'high' | 'medium' | 'low'
  note       welke inputs ontbraken / welke aanname gebruikt is

Ontbrekende inputs maken de waarde None met uitleg, of verlagen de
confidence met een note — nooit een stille 0 zonder vermelding.
Formules en aannames: docs/METHODOLOGY.md.
"""

from dataclasses import dataclass, field

from . import rubrics as R

DAYS_PER_YEAR = 365.0


@dataclass
class Metric:
    value: float | None
    source: str | None = None          # 'nbb' | 'derived' | 'estimate'
    confidence: str | None = None      # 'high' | 'medium' | 'low'
    note: str = ""
    inputs: dict = field(default_factory=dict)

    @classmethod
    def missing(cls, note: str) -> "Metric":
        return cls(value=None, note=note)


def _collect(rub: dict, codes: list[str]) -> tuple[dict, list[str]]:
    present = {c: rub[c] for c in codes if c in rub and rub[c] is not None}
    absent = [c for c in codes if c not in present]
    return present, absent


def ebitda_proxy(rub: dict) -> Metric:
    """EBITDA-proxy = EBIT (9901) + afschrijvingen (630)
    + waardeverminderingen (631/4) + voorzieningen (635/8).

    Kostenrubrieken worden teruggeteld zoals gerapporteerd; 631/4 en 635/8
    kunnen negatief zijn (terugnemingen) en tellen dan ook negatief mee.
    EBIT is verplicht; ontbrekende terugtel-rubrieken tellen als 0 mét note.
    """
    if R.EBIT not in rub:
        return Metric.missing(f"EBIT ({R.EBIT}) ontbreekt — geen EBITDA-proxy")
    addbacks, absent = _collect(rub, [R.AFSCHRIJVINGEN, R.WAARDEVERMINDERINGEN, R.VOORZIENINGEN])
    value = rub[R.EBIT] + sum(addbacks.values())
    return Metric(
        value=value,
        source="derived",
        confidence="high" if not absent else "medium",
        note="" if not absent else f"terugtel-rubrieken ontbreken (als 0 geteld): {absent}",
        inputs={R.EBIT: rub[R.EBIT], **addbacks},
    )


def net_financial_debt(rub: dict) -> Metric:
    """Netto financiële schuld = 170/4 + 42 + 43 − 50/53 − 54/58.

    None als geen enkele component gerapporteerd is; anders tellen
    ontbrekende componenten als 0 met note en lagere confidence.
    """
    debt, debt_absent = _collect(rub, [R.LT_FIN_SCHULD, R.KT_DEEL_LT, R.KT_FIN_SCHULD])
    cash, cash_absent = _collect(rub, [R.GELDBELEGGINGEN, R.LIQUIDE_MIDDELEN])
    if not debt and not cash:
        return Metric.missing("geen schuld- of cashrubrieken gerapporteerd")
    absent = debt_absent + cash_absent
    value = sum(debt.values()) - sum(cash.values())
    confidence = "high" if not absent else ("medium" if len(absent) <= 2 else "low")
    return Metric(
        value=value,
        source="derived",
        confidence=confidence,
        note="" if not absent else f"ontbrekend (als 0 geteld): {absent}",
        inputs={**debt, **cash},
    )


def revenue(rub: dict, gross_margin_ratio: float | None = None,
            ratio_note: str = "") -> Metric:
    """Omzet.

    - Volledig schema: rubriek 70 = gerapporteerd feit (source 'nbb').
    - Verkort/micro: omzet wordt niet gepubliceerd. Als er een expliciete
      omzet/brutomarge-ratio is aangeleverd (peer-mediaan uit fase 4, of
      een bewuste gebruikersaanname), dan: omzet ≈ 9900 × ratio, source
      'estimate', confidence 'low' — NOOIT als feit presenteren.
    - Zonder ratio: None met uitleg. Er wordt niet stilzwijgend geschat.
    """
    if R.OMZET in rub:
        return Metric(value=rub[R.OMZET], source="nbb", confidence="high",
                      inputs={R.OMZET: rub[R.OMZET]})
    if R.BRUTOMARGE in rub:
        if gross_margin_ratio is None:
            return Metric.missing(
                "omzet niet gepubliceerd (verkort/micro); geen omzet/brutomarge-"
                "ratio beschikbaar — schatting vereist peer-ratio (fase 4) of "
                "expliciete aanname (--revenue-ratio)"
            )
        if gross_margin_ratio <= 0:
            return Metric.missing(f"ongeldige omzet/brutomarge-ratio: {gross_margin_ratio}")
        return Metric(
            value=rub[R.BRUTOMARGE] * gross_margin_ratio,
            source="estimate",
            confidence="low",
            note=(f"SCHATTING: brutomarge ({R.BRUTOMARGE}) x ratio "
                  f"{gross_margin_ratio}" + (f" — {ratio_note}" if ratio_note else "")),
            inputs={R.BRUTOMARGE: rub[R.BRUTOMARGE], "ratio": gross_margin_ratio},
        )
    return Metric.missing("noch omzet (70) noch brutomarge (9900) gerapporteerd")


def value_added_proxy(rub: dict) -> Metric:
    """Toegevoegde-waardeproxy.

    Volledig schema: TW ≈ 70 − 60 − 61 (benadering: andere bedrijfs-
    opbrengsten buiten beschouwing — zie METHODOLOGY). Verkort/micro:
    TW ≈ brutomarge (9900). Beide zijn afleidingen met een aanname,
    dus source 'derived' met confidence 'medium'.
    """
    if all(c in rub for c in (R.OMZET, R.HANDELSGOEDEREN, R.DIENSTEN)):
        value = rub[R.OMZET] - rub[R.HANDELSGOEDEREN] - rub[R.DIENSTEN]
        return Metric(value=value, source="derived", confidence="medium",
                      note="TW ≈ 70 − 60 − 61 (andere bedrijfsopbrengsten niet meegeteld)",
                      inputs={c: rub[c] for c in (R.OMZET, R.HANDELSGOEDEREN, R.DIENSTEN)})
    if R.BRUTOMARGE in rub:
        return Metric(value=rub[R.BRUTOMARGE], source="derived", confidence="medium",
                      note="TW ≈ brutomarge (9900) — verkort/micro-benadering",
                      inputs={R.BRUTOMARGE: rub[R.BRUTOMARGE]})
    return Metric.missing("onvoldoende rubrieken voor toegevoegde-waardeproxy")


def dso_days(rub: dict, revenue_metric: Metric) -> Metric:
    """Klantenkredietdagen = handelsvorderingen (40) / omzet × 365.
    Op een geschatte omzet wordt de uitkomst zelf ook een schatting."""
    if R.HANDELSVORDERINGEN not in rub:
        return Metric.missing(f"handelsvorderingen ({R.HANDELSVORDERINGEN}) niet gerapporteerd")
    if revenue_metric.value is None or revenue_metric.value <= 0:
        return Metric.missing("geen (positieve) omzet beschikbaar als noemer")
    value = rub[R.HANDELSVORDERINGEN] / revenue_metric.value * DAYS_PER_YEAR
    estimated = revenue_metric.source == "estimate"
    return Metric(
        value=value,
        source="estimate" if estimated else "derived",
        confidence="low" if estimated else "medium",
        note="op basis van GESCHATTE omzet" if estimated else "",
        inputs={R.HANDELSVORDERINGEN: rub[R.HANDELSVORDERINGEN], "omzet": revenue_metric.value},
    )


def dpo_days(rub: dict) -> Metric:
    """Leverancierskredietdagen = handelsschulden (44) / (60 + 61) × 365."""
    purchases, absent = _collect(rub, [R.HANDELSGOEDEREN, R.DIENSTEN])
    if R.HANDELSSCHULDEN not in rub:
        return Metric.missing(f"handelsschulden ({R.HANDELSSCHULDEN}) niet gerapporteerd")
    if not purchases:
        return Metric.missing("aankopen (60) en diensten (61) niet gerapporteerd (verkort schema)")
    denominator = sum(purchases.values())
    if denominator <= 0:
        return Metric.missing("aankopen + diensten niet positief")
    return Metric(
        value=rub[R.HANDELSSCHULDEN] / denominator * DAYS_PER_YEAR,
        source="derived",
        confidence="medium" if not absent else "low",
        note="" if not absent else f"noemer onvolledig, ontbreekt: {absent}",
        inputs={R.HANDELSSCHULDEN: rub[R.HANDELSSCHULDEN], **purchases},
    )


def inventory_days(rub: dict) -> Metric:
    """Voorraaddagen = voorraden (3) / aankopen handelsgoederen (60) × 365."""
    if R.VOORRADEN not in rub:
        return Metric.missing(f"voorraden ({R.VOORRADEN}) niet gerapporteerd")
    if R.HANDELSGOEDEREN not in rub or rub[R.HANDELSGOEDEREN] <= 0:
        return Metric.missing("aankopen handelsgoederen (60) niet (positief) gerapporteerd")
    return Metric(
        value=rub[R.VOORRADEN] / rub[R.HANDELSGOEDEREN] * DAYS_PER_YEAR,
        source="derived",
        confidence="medium",
        inputs={R.VOORRADEN: rub[R.VOORRADEN], R.HANDELSGOEDEREN: rub[R.HANDELSGOEDEREN]},
    )


def capex_intensity(rub: dict) -> Metric:
    """Capex-intensiteit = aanschaffingen MVA (8169) / toegevoegde waarde."""
    if R.AANSCHAF_MVA not in rub:
        return Metric.missing(f"aanschaffingen MVA ({R.AANSCHAF_MVA}, toelichting) niet gerapporteerd")
    tw = value_added_proxy(rub)
    if tw.value is None or tw.value <= 0:
        return Metric.missing(f"geen (positieve) toegevoegde waarde: {tw.note}")
    return Metric(
        value=rub[R.AANSCHAF_MVA] / tw.value,
        source="derived",
        confidence="low" if tw.confidence == "medium" else tw.confidence,
        note=f"noemer is proxy: {tw.note}",
        inputs={R.AANSCHAF_MVA: rub[R.AANSCHAF_MVA], "toegevoegde_waarde": tw.value},
    )


def cost_per_fte(rub: dict) -> Metric:
    """Personeelskost per VTE = bezoldigingen (62) / gemiddeld VTE (9087)."""
    if R.VTE_GEMIDDELD not in rub or rub[R.VTE_GEMIDDELD] <= 0:
        return Metric.missing(f"gemiddeld VTE ({R.VTE_GEMIDDELD}, sociale balans) niet (positief) gerapporteerd")
    if R.BEZOLDIGINGEN not in rub:
        return Metric.missing(f"bezoldigingen ({R.BEZOLDIGINGEN}) niet gerapporteerd")
    return Metric(
        value=rub[R.BEZOLDIGINGEN] / rub[R.VTE_GEMIDDELD],
        source="derived",
        confidence="high",
        inputs={R.BEZOLDIGINGEN: rub[R.BEZOLDIGINGEN], R.VTE_GEMIDDELD: rub[R.VTE_GEMIDDELD]},
    )


def reported(rub: dict, code: str) -> Metric:
    """Passthrough van een gerapporteerde rubriek als feit."""
    if code not in rub:
        return Metric.missing(f"rubriek {code} niet gerapporteerd")
    return Metric(value=rub[code], source="nbb", confidence="high", inputs={code: rub[code]})
