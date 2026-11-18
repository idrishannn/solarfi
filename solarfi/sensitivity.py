"""Duyarlılık analizi: tornado, senaryolar ve iki boyutlu tablo.

Orijinal prototipteki iki sorun burada düzeltildi:

* "CAPEX +%10" aslında yalnızca güneş kalemini oynatıyordu, yani toplam
  yatırımın ~%80'ini. Artık toplam CAPEX ölçekleniyor.
* Tornado grafiği aynı parametrenin iki yönünü aynı satıra çizdiği için
  çubuklar üst üste biniyordu. Artık her parametre tek satır, iki yön
  ayrı çubuk ve satırlar etkiye göre sıralı.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .engine import ModelResult, run_model
from .inputs import CapexMode, DebtSizing, OpexMode, ProjectInputs

__all__ = ["SensitivityVariable", "DEFAULT_VARIABLES", "run_tornado", "run_scenarios", "two_way_table"]


@dataclass(frozen=True)
class SensitivityVariable:
    """Tornado analizinde oynatılacak bir parametre.

    `apply` fonksiyonu girdileri ve yönü (+1 / -1) alıp değiştirilmiş bir
    `ProjectInputs` döner. Fonksiyon olarak tanımlanmasının sebebi, bazı
    parametrelerin oransal (CAPEX %10), bazılarının mutlak (faiz +100bp)
    oynatılması gerekmesi.
    """

    label: str
    apply: Callable[[ProjectInputs, int], ProjectInputs]
    description: str = ""


def _scale_capex(inp: ProjectInputs, direction: int, pct: float = 0.10) -> ProjectInputs:
    factor = 1.0 + direction * pct
    if inp.capex_mode is CapexMode.TOTAL:
        return inp.replace(capex_total=inp.capex_total * factor)
    return inp.replace(
        capex_solar_per_mwp=inp.capex_solar_per_mwp * factor,
        capex_land_per_mwp=inp.capex_land_per_mwp * factor,
        capex_grid_per_mwp=inp.capex_grid_per_mwp * factor,
        capex_other_per_mwp=inp.capex_other_per_mwp * factor,
        capex_storage_per_mw=inp.capex_storage_per_mw * factor,
    )


def _scale_opex(inp: ProjectInputs, direction: int, pct: float = 0.10) -> ProjectInputs:
    factor = 1.0 + direction * pct
    if inp.opex_mode is OpexMode.TOTAL:
        return inp.replace(opex_total=inp.opex_total * factor)
    return inp.replace(
        opex_fixed=inp.opex_fixed * factor,
        opex_per_mwp=inp.opex_per_mwp * factor,
        opex_per_mwh=inp.opex_per_mwh * factor,
        rent_base=inp.rent_base * factor,
    )


def _flex_leverage(inp: ProjectInputs, direction: int) -> ProjectInputs:
    """Kaldıraç kolunu modele göre uyarlar.

    GEARING modunda borç oranını doğrudan oynatmak anlamlıdır. Ama
    SCULPTED_DSCR modunda borç oranı bir çıktıdır — onu oynatmak hiçbir
    şeyi değiştirmez. Orada asıl kaldıraç kolu hedef DSCR'dır ve yönü
    terstir: DSCR hedefini düşürmek kaldıracı artırır.
    """
    if inp.debt_sizing is DebtSizing.SCULPTED_DSCR:
        return inp.replace(target_dscr=max(1.0, inp.target_dscr - direction * 0.15))
    return inp.replace(debt_ratio=min(0.95, max(0.0, inp.debt_ratio + direction * 0.10)))


def _scale_price(inp: ProjectInputs, direction: int, pct: float = 0.10) -> ProjectInputs:
    factor = 1.0 + direction * pct
    changes: dict = {
        "flat_price": inp.flat_price * factor,
        "auction_price": inp.auction_price * factor,
    }
    if inp.price_curve:
        changes["price_curve"] = {y: p * factor for y, p in inp.price_curve.items()}
    return inp.replace(**changes)


DEFAULT_VARIABLES: tuple[SensitivityVariable, ...] = (
    SensitivityVariable(
        "Elektrik Fiyatı",
        lambda i, d: _scale_price(i, d),
        "Bütün fiyat serisi ±%10",
    ),
    SensitivityVariable(
        "Üretim (Yield)",
        lambda i, d: i.replace(specific_yield=i.specific_yield * (1 + d * 0.10)),
        "Spesifik verim ±%10 (kabaca P90 ↔ P10 aralığı)",
    ),
    SensitivityVariable(
        "CAPEX",
        lambda i, d: _scale_capex(i, d),
        "Toplam yatırım ±%10",
    ),
    SensitivityVariable(
        "OPEX",
        lambda i, d: _scale_opex(i, d),
        "Bütün işletme giderleri ±%10",
    ),
    SensitivityVariable(
        "Faiz Oranı",
        lambda i, d: i.replace(interest_rate=max(0.0, i.interest_rate + d * 0.01)),
        "Kredi faizi ±100 baz puan",
    ),
    SensitivityVariable(
        "Kaldıraç / DSCR",
        _flex_leverage,
        "Kaldıraç ±10 puan; sculpting modunda hedef DSCR ∓0,15x",
    ),
    SensitivityVariable(
        "Degradasyon",
        lambda i, d: i.replace(degradation=max(0.0, i.degradation + d * 0.002)),
        "Yıllık bozunma ±20 baz puan",
    ),
    SensitivityVariable(
        "Enflasyon",
        lambda i, d: i.replace(inflation=i.inflation + d * 0.01),
        "Enflasyon ±100 baz puan (OPEX ve kirayı etkiler)",
    ),
)


def _metric(result: ModelResult, metric: str) -> float:
    if metric == "npv":
        return result.project_npv
    if metric == "irr":
        return result.equity_irr if result.equity_irr is not None else float("nan")
    if metric == "lcoe":
        return result.lcoe
    if metric == "min_dscr":
        return result.min_dscr if result.min_dscr is not None else float("nan")
    raise ValueError(f"Bilinmeyen gösterge: {metric}")


def run_tornado(
    base: ProjectInputs,
    variables: tuple[SensitivityVariable, ...] = DEFAULT_VARIABLES,
    metric: str = "npv",
) -> pd.DataFrame:
    """Her parametre için yukarı/aşağı senaryo etkisini hesaplar.

    Returns
    -------
    pd.DataFrame
        Sütunlar: Parametre, Yön, Değer, Etki, MutlakEtki, Açıklama.
        Satırlar toplam mutlak etkiye göre büyükten küçüğe sıralıdır.
    """
    base_value = _metric(run_model(base), metric)
    rows: list[dict] = []

    for var in variables:
        for direction, label in ((1, "Yukarı"), (-1, "Aşağı")):
            try:
                modified = var.apply(base, direction)
                value = _metric(run_model(modified), metric)
            except (ValueError, ZeroDivisionError):
                # Örn. kredi vadesi/oranı sınırı ihlal edilirse bu yönü atla.
                continue
            rows.append(
                {
                    "Parametre": var.label,
                    "Yön": label,
                    "Değer": value,
                    "Etki": value - base_value,
                    "Açıklama": var.description,
                }
            )

    if not rows:
        return pd.DataFrame(columns=["Parametre", "Yön", "Değer", "Etki", "Açıklama"])

    df = pd.DataFrame(rows)
    df["MutlakEtki"] = df["Etki"].abs()
    order = (
        df.groupby("Parametre")["MutlakEtki"].max().sort_values(ascending=False).index.tolist()
    )
    df["Parametre"] = pd.Categorical(df["Parametre"], categories=order, ordered=True)
    df = df.sort_values(["Parametre", "Yön"]).reset_index(drop=True)
    df.attrs["base_value"] = base_value
    df.attrs["metric"] = metric
    df.attrs["order"] = order
    return df


def run_scenarios(base: ProjectInputs, price_step: float = 8.0) -> pd.DataFrame:
    """Base / High / Low fiyat senaryolarını ve P90 üretim senaryosunu karşılaştırır."""
    scenarios: dict[str, ProjectInputs] = {"Base Case": base}

    def shift_price(inp: ProjectInputs, delta: float) -> ProjectInputs:
        changes: dict = {
            "flat_price": max(0.0, inp.flat_price + delta),
            "auction_price": max(0.0, inp.auction_price + delta),
        }
        if inp.price_curve:
            changes["price_curve"] = {y: max(0.0, p + delta) for y, p in inp.price_curve.items()}
        return inp.replace(**changes)

    scenarios["High Case"] = shift_price(base, price_step)
    scenarios["Low Case"] = shift_price(base, -price_step)
    scenarios["P90 Üretim"] = base.replace(p_factor=base.p_factor * 0.92)
    scenarios["Stres (Düşük fiyat + P90)"] = shift_price(base, -price_step).replace(
        p_factor=base.p_factor * 0.92
    )

    rows = []
    for name, inp in scenarios.items():
        r = run_model(inp)
        rows.append(
            {
                "Senaryo": name,
                "NPV": r.project_npv,
                "IRR": r.equity_irr,
                "LCOE": r.lcoe,
                "Min DSCR": r.min_dscr,
                "Geri Ödeme (yıl)": r.payback_years,
            }
        )
    return pd.DataFrame(rows)


def two_way_table(
    base: ProjectInputs,
    row_var: SensitivityVariable,
    col_var: SensitivityVariable,
    steps: tuple[float, ...] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    metric: str = "npv",
) -> pd.DataFrame:
    """İki parametrenin birlikte oynatıldığı klasik duyarlılık matrisi."""
    table: dict[str, dict[str, float]] = {}
    for r_step in steps:
        row_inp = row_var.apply(base, r_step / 0.10) if r_step else base
        row_key = f"{row_var.label} {r_step:+.0%}"
        table[row_key] = {}
        for c_step in steps:
            inp = col_var.apply(row_inp, c_step / 0.10) if c_step else row_inp
            col_key = f"{col_var.label} {c_step:+.0%}"
            try:
                table[row_key][col_key] = _metric(run_model(inp), metric)
            except ValueError:
                table[row_key][col_key] = float("nan")
    return pd.DataFrame(table).T
