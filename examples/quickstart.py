"""SolarFi motorunun arayüzsüz kullanımı.

Çalıştırmak için proje kök dizininden::

    python examples/quickstart.py

Bu betik arayüzün yaptığı her şeyi kodla yapar: model kurar, çalıştırır,
senaryoları karşılaştırır ve sonucu CSV'ye yazar.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Depo kökünü içe aktarma yoluna ekle (paket kurulmamış olsa da çalışsın).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from solarfi import ProjectInputs, run_model, run_scenarios, run_tornado  # noqa: E402
from solarfi.data_loader import (  # noqa: E402
    available_datasets,
    curve_to_mapping,
    load_price_curves,
)
from solarfi.inputs import DebtSizing, PriceMode, RentMode  # noqa: E402


def basic_example() -> None:
    """En sade kullanım: sabit fiyat, standart kredi."""
    print("=" * 68)
    print("1. TEMEL ÖRNEK — 50 MWp, sabit fiyat")
    print("=" * 68)

    inputs = ProjectInputs(
        project_name="Puglia 50 MWp",
        currency="EUR",
        capacity_mwp=50.0,
        specific_yield=1_650.0,
        flat_price=45.0,
        price_escalation=0.02,
        debt_ratio=0.70,
        interest_rate=0.065,
        loan_tenor=15,
        cost_of_equity=0.09,
    )

    result = run_model(inputs)
    for name, value in result.kpi_dict().items():
        print(f"  {name:<32} {value}")


def curve_example() -> None:
    """Fiyat eğrisi + ihale + ciro payı kirası içeren gelişmiş kurulum."""
    print()
    print("=" * 68)
    print("2. GELİŞMİŞ ÖRNEK — fiyat eğrisi, PPA, ciro payı kirası")
    print("=" * 68)

    datasets = available_datasets()
    if not datasets:
        print("  data/ klasöründe CSV yok, bu örnek atlandı.")
        return

    name, path = next(iter(datasets.items()))
    curves = load_price_curves(path)
    region = curves.columns[0]
    print(f"  Veri seti: {name} · bölge: {region}")

    inputs = ProjectInputs(
        project_name="Sicilya 80 MWp + PPA",
        capacity_mwp=80.0,
        specific_yield=1_750.0,
        construction_years=2,
        capex_schedule=[0.35, 0.65],
        price_mode=PriceMode.CURVE,
        price_curve=curve_to_mapping(curves, region),
        capture_rate=0.93,
        auction_active=True,
        auction_price=62.0,
        auction_years=12,
        rent_mode=RentMode.MAX_BASE_OR_ROYALTY,
        rent_royalty=0.03,
        rent_base=60_000.0,
        loss_carryforward=True,
        loss_carryforward_limit=5,
        decommissioning_per_mwp=8_000.0,
        debt_ratio=0.65,
        interest_rate=0.06,
        loan_tenor=18,
    )

    result = run_model(inputs)
    for key in ("Özkaynak IRR", "NPV (Ke ile)", "LCOE", "Minimum DSCR"):
        print(f"  {key:<32} {result.kpi_dict()[key]}")

    df = result.to_dataframe()
    output = Path(__file__).parent / "ornek_nakit_akisi.csv"
    df.to_csv(output, index=False, encoding="utf-8-sig")
    print(f"  Nakit akışı yazıldı: {output.name}")


def risk_example() -> None:
    """Tornado ve senaryo analizi."""
    print()
    print("=" * 68)
    print("3. RİSK ANALİZİ")
    print("=" * 68)

    base = ProjectInputs(capacity_mwp=50.0, flat_price=45.0)

    print("\n  Tornado (NPV üzerindeki etki, milyon):")
    tornado = run_tornado(base)
    for parameter in tornado.attrs["order"]:
        rows = tornado[tornado["Parametre"] == parameter]
        up = rows[rows["Yön"] == "Yukarı"]["Etki"]
        down = rows[rows["Yön"] == "Aşağı"]["Etki"]
        up_v = up.iloc[0] / 1e6 if len(up) else 0.0
        down_v = down.iloc[0] / 1e6 if len(down) else 0.0
        print(f"    {parameter:<18} yukarı {up_v:+7.2f}   aşağı {down_v:+7.2f}")

    print("\n  Senaryolar:")
    scenarios = run_scenarios(base)
    for _, row in scenarios.iterrows():
        irr_text = "n/a" if row["IRR"] is None else f"{row['IRR']:.1%}"
        dscr_text = "n/a" if row["Min DSCR"] is None else f"{row['Min DSCR']:.2f}x"
        print(
            f"    {row['Senaryo']:<28} NPV {row['NPV'] / 1e6:+7.1f}M "
            f"IRR {irr_text:>7} DSCR {dscr_text:>7}"
        )


def sculpting_example() -> None:
    """Sabit kaldıraç ile DSCR'a göre şekillendirmeyi karşılaştırır."""
    print()
    print("=" * 68)
    print("4. BORÇ ŞEKİLLENDİRME (SCULPTING)")
    print("=" * 68)

    common = dict(
        capacity_mwp=50.0,
        specific_yield=1_650.0,
        flat_price=45.0,
        operating_life=25,
        interest_rate=0.065,
        loan_tenor=15,
    )

    annuity = run_model(ProjectInputs(**common, debt_ratio=0.70))
    # Aynı riski (aynı minimum DSCR'ı) hedefleyerek adil karşılaştırma yap.
    sculpted = run_model(
        ProjectInputs(
            **common,
            debt_sizing=DebtSizing.SCULPTED_DSCR,
            target_dscr=annuity.min_dscr,
            max_gearing=0.99,
        )
    )

    print(f"\n  Aynı minimum DSCR ({annuity.min_dscr:.3f}x) altında:")
    print(f"    {'':12s} {'anüite':>12s} {'sculpted':>12s}")
    rows = [
        ("kaldıraç", f"{annuity.effective_gearing:.1%}", f"{sculpted.effective_gearing:.1%}"),
        ("kredi", f"{annuity.debt_at_cod / 1e6:.2f}M", f"{sculpted.debt_at_cod / 1e6:.2f}M"),
        ("özkaynak", f"{annuity.total_equity / 1e6:.2f}M", f"{sculpted.total_equity / 1e6:.2f}M"),
        ("IRR", f"{annuity.equity_irr:.2%}", f"{sculpted.equity_irr:.2%}"),
    ]
    for label, a, b in rows:
        print(f"    {label:12s} {a:>12s} {b:>12s}")

    print("\n  DSCR profilleri (vade boyunca):")
    for name, result in (("anüite  ", annuity), ("sculpted", sculpted)):
        ratios = [d for d in result.dscr if d is not None]
        print(f"    {name}: " + " ".join(f"{d:.2f}" for d in ratios))
    print("\n  Anüitede DSCR yükselerek boşa giden borç kapasitesi yaratır;")
    print("  sculpting onu düz tutup aynı riskle daha fazla borç taşır.")

    print("\n  Hedef DSCR'a göre kaldıraç (kaldıraç bir ÇIKTIDIR):")
    for target in (1.30, 1.45, 1.60, 2.00):
        r = run_model(
            ProjectInputs(
                **common,
                debt_sizing=DebtSizing.SCULPTED_DSCR,
                target_dscr=target,
                max_gearing=0.95,
            )
        )
        print(
            f"    hedef {target:.2f}x -> kaldıraç {r.effective_gearing:5.1%}, "
            f"IRR {r.equity_irr:6.2%}, {r.sculpting_iterations} iterasyonda çözüldü"
        )


def maintenance_example() -> None:
    """İnvertör yenileme ve bakım rezerv hesabının etkisi."""
    print()
    print("=" * 68)
    print("5. BAKIM YATIRIMI VE REZERV HESABI")
    print("=" * 68)

    common = dict(
        capacity_mwp=50.0,
        specific_yield=1_650.0,
        flat_price=45.0,
        operating_life=30,
        loan_tenor=15,
        interest_rate=0.065,
        inverter_replacement_years=[13],
        inverter_cost_per_mwp=60_000.0,
    )

    print(f"\n  {'senaryo':<24} {'min DSCR':>10} {'NPV':>10} {'IRR':>8} {'LCOE':>8}")
    scenarios = [
        ("yenileme yok", ProjectInputs(**{**common, "inverter_replacement_years": []})),
        ("yenileme, rezerv yok", ProjectInputs(**common, maintenance_reserve_years=0)),
        ("yenileme + 5 yıl rezerv", ProjectInputs(**common, maintenance_reserve_years=5)),
    ]
    for label, inputs in scenarios:
        r = run_model(inputs)
        print(
            f"  {label:<24} {r.min_dscr:>10.2f} {r.project_npv / 1e6:>9.2f}M "
            f"{r.equity_irr:>8.2%} {r.lcoe:>8.2f}"
        )

    print("\n  Rezervsiz senaryoda DSCR yenileme yılında çöker; rezerv onu")
    print("  düzler. LCOE değişmez — rezerv finansman mekaniğidir, maliyet değil.")

    r = run_model(ProjectInputs(**common, maintenance_reserve_years=5))
    df = r.to_dataframe()
    window = df[(df["Yıl"] >= r.inputs.cod_year + 6) & (df["Yıl"] <= r.inputs.cod_year + 13)]
    print("\n  Rezerv birikimi ve harcama:")
    for _, row in window.iterrows():
        marker = "  <- harcama" if row["Bakım Yatırımı"] < 0 else ""
        print(
            f"    {int(row['Yıl'])}  rezerv {row['Bakım Rezervi'] / 1e3:>8.0f}k  "
            f"CFADS {row['CFADS'] / 1e6:>5.2f}M{marker}"
        )


def sensitivity_grid() -> None:
    """İki boyutlu duyarlılık matrisi."""
    print()
    print("=" * 68)
    print("6. İKİ BOYUTLU DUYARLILIK (NPV, milyon)")
    print("=" * 68)

    from solarfi.sensitivity import DEFAULT_VARIABLES, two_way_table

    price = DEFAULT_VARIABLES[0]
    capex = DEFAULT_VARIABLES[2]
    table = two_way_table(ProjectInputs(), price, capex, steps=(-0.10, 0.0, 0.10))
    print((table / 1e6).round(2).to_string())


if __name__ == "__main__":
    basic_example()
    curve_example()
    risk_example()
    sculpting_example()
    maintenance_example()
    sensitivity_grid()
    print("\nBitti.")
