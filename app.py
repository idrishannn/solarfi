"""SolarFi — Streamlit arayüzü.

Bu dosya yalnızca **arayüz** katmanıdır: kullanıcıdan girdi toplar,
`solarfi` paketindeki motoru çağırır ve sonucu çizer. Hiçbir finansal
hesap burada yapılmaz — böylece motor arayüzden bağımsız test edilebilir.

Çalıştırmak için::

    streamlit run app.py
"""

from __future__ import annotations

import io
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from solarfi import ProjectInputs, run_model, run_scenarios, run_tornado, theme
from solarfi.data_loader import available_datasets, curve_to_mapping, load_price_curves
from solarfi.export import workbook_bytes
from solarfi.inputs import CapexMode, DebtSizing, OpexMode, PriceMode, RentMode

LITE, PRO, EXPERT = "Hızlı", "Standart", "Uzman"

st.set_page_config(
    page_title="SolarFi",
    layout="wide",
    page_icon="☀️",
    initial_sidebar_state="expanded",
)
st.markdown(theme.CSS, unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def cached_curves(path_str: str) -> pd.DataFrame:
    return load_price_curves(path_str)


def load_price_curves_from_buffer(buffer) -> pd.DataFrame:
    """Kullanıcının yüklediği CSV'yi diske yazmadan okur."""
    content = buffer.getvalue().decode("utf-8")
    df_raw = pd.read_csv(io.StringIO(content))
    cols = {c.strip().lower(): c for c in df_raw.columns}
    if "year" not in cols:
        raise ValueError("'Year' sütunu bulunamadı.")
    df_raw = df_raw.rename(columns={cols["year"]: "Year"})
    df_raw["Year"] = pd.to_numeric(df_raw["Year"], errors="coerce")
    df_raw = df_raw.dropna(subset=["Year"])
    if df_raw.empty:
        raise ValueError("Geçerli yıl değeri içeren satır yok.")
    df_raw["Year"] = df_raw["Year"].astype(int)
    return df_raw.set_index("Year").sort_index()


# ======================================================================
# KENAR ÇUBUĞU — GİRDİLER
# ======================================================================

with st.sidebar:
    st.title("SolarFi")
    st.caption("Güneş santrali proje finansmanı modeli")
    mode = st.segmented_control(
        "Detay seviyesi",
        [LITE, PRO, EXPERT],
        default=PRO,
        width="stretch",
        help="Lite: hızlı fizibilite. Pro: standart model. Expert: fiyat eğrisi, "
        "ihale, ciro kirası, depolama, sculpting ve inşaat takvimi.",
    )
    if mode is None:  # segmented_control seçim kaldırılabilir
        mode = PRO
    is_pro_plus = mode in (PRO, EXPERT)
    is_expert = mode == EXPERT

    st.divider()

    # ------------------------------------------------------------------
    with st.expander("1 · Proje ve zaman ekseni", expanded=True):
        project_name = st.text_input("Proje adı", value="Yeni Proje")
        currency = st.selectbox("Para birimi", ["EUR", "USD", "TRY", "GBP"], index=0)
        base_year = st.number_input(
            "Baz yıl (finansal kapanış)",
            min_value=2000,
            max_value=2100,
            value=date.today().year,
            step=1,
            help="t=0 dönemi. İlk yatırım harcaması bu yılda yapılır.",
        )
        operating_life = st.number_input(
            "İşletme ömrü (yıl)", min_value=1, max_value=50, value=30, step=1
        )
        if is_pro_plus:
            construction_years = st.number_input(
                "İnşaat süresi (yıl)",
                min_value=1,
                max_value=6,
                value=1,
                step=1,
                help="Üretim bu sürenin sonunda (COD) başlar.",
            )
        else:
            construction_years = 1

        capex_schedule: list[float] = []
        if is_expert and construction_years > 1:
            raw = st.text_input(
                "CAPEX dağılımı",
                value=",".join(["1"] * construction_years),
                help="İnşaat yıllarına ağırlık. Örn. 2 yıl için '30,70'.",
            )
            try:
                capex_schedule = [float(x) for x in raw.replace(" ", "").split(",") if x]
            except ValueError:
                st.warning("CAPEX dağılımı okunamadı, eşit dağıtılacak.")
                capex_schedule = []

    # ------------------------------------------------------------------
    with st.expander("2 · Teknik", expanded=True):
        capacity_mwp = st.number_input(
            "Kurulu güç (MWp)", min_value=0.1, value=50.0, step=1.0, format="%.1f"
        )
        specific_yield = st.number_input(
            "Spesifik verim (kWh/kWp/yıl)",
            min_value=100.0,
            max_value=3000.0,
            value=1650.0,
            step=25.0,
            help="P50, sistem kayıpları sonrası.",
        )
        degradation = (
            st.number_input(
                "Yıllık degradasyon (%)", min_value=0.0, max_value=5.0, value=0.5, step=0.05
            )
            / 100.0
        )
        if is_pro_plus:
            availability = (
                st.number_input(
                    "Kullanılabilirlik (%)", min_value=50.0, max_value=100.0, value=99.0, step=0.5
                )
                / 100.0
            )
            p_scenario = st.selectbox(
                "Üretim senaryosu",
                ["P50 (beklenen)", "P75", "P90 (finansman)"],
                index=0,
                help="Kredi verenler genelde P90 üzerinden borç boyutlar.",
            )
            p_factor = {"P50 (beklenen)": 1.00, "P75": 0.96, "P90 (finansman)": 0.92}[p_scenario]
        else:
            availability, p_factor = 0.99, 1.00

        storage_power_mw = storage_energy_mwh = 0.0
        storage_spread = 0.0
        storage_cycles = 300.0
        storage_rte = 0.85
        if is_expert:
            st.caption("Depolama (opsiyonel)")
            storage_power_mw = st.number_input(
                "Depolama gücü (MW)", min_value=0.0, value=0.0, step=1.0
            )
            if storage_power_mw > 0:
                storage_energy_mwh = st.number_input(
                    "Depolama enerjisi (MWh)", min_value=0.1, value=storage_power_mw * 2, step=1.0
                )
                storage_cycles = st.number_input(
                    "Yıllık tam çevrim", min_value=0.0, max_value=730.0, value=300.0, step=10.0
                )
                storage_rte = (
                    st.number_input(
                        "Gidiş-dönüş verimi (%)",
                        min_value=50.0,
                        max_value=100.0,
                        value=85.0,
                        step=1.0,
                    )
                    / 100.0
                )
                storage_spread = st.number_input(
                    "Arbitraj marjı (/MWh)", min_value=0.0, value=40.0, step=5.0
                )

    # ------------------------------------------------------------------
    with st.expander("3 · Gelir ve fiyat", expanded=True):
        price_mode = PriceMode.FLAT
        price_curve: dict[int, float] = {}
        capture_rate = 1.0
        flat_price = 45.0
        price_escalation = 0.02

        if is_expert:
            source = st.radio(
                "Fiyat kaynağı", ["Sabit fiyat", "Fiyat eğrisi (CSV)"], horizontal=True
            )
            if source == "Fiyat eğrisi (CSV)":
                datasets = available_datasets()
                uploaded = st.file_uploader("Kendi eğrini yükle (CSV)", type="csv")
                df_curve = None
                if uploaded is not None:
                    try:
                        df_curve = load_price_curves_from_buffer(uploaded)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Dosya okunamadı: {exc}")
                elif datasets:
                    choice = st.selectbox("Örnek veri seti", list(datasets))
                    try:
                        df_curve = cached_curves(str(datasets[choice]))
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Veri seti okunamadı: {exc}")
                else:
                    st.warning("`data/` klasöründe CSV bulunamadı.")

                if df_curve is not None and not df_curve.empty:
                    region = st.selectbox("Bölge", list(df_curve.columns))
                    price_curve = curve_to_mapping(df_curve, region)
                    price_mode = PriceMode.CURVE
                    st.caption(
                        f"{min(price_curve)}–{max(price_curve)} arası "
                        f"{len(price_curve)} yıllık eğri yüklendi."
                    )
            if price_mode is PriceMode.FLAT:
                flat_price = st.number_input(
                    "Baz fiyat (/MWh)", min_value=0.0, value=45.0, step=1.0
                )
            capture_rate = (
                st.number_input(
                    "Yakalama oranı (%)",
                    min_value=30.0,
                    max_value=130.0,
                    value=95.0,
                    step=1.0,
                    help="Güneşin ürettiği saatlerdeki fiyatın baz yük fiyatına oranı.",
                )
                / 100.0
            )
        elif mode == PRO:
            base_price = st.number_input("Baz fiyat (/MWh)", min_value=0.0, value=45.0, step=1.0)
            scenario = st.selectbox("Fiyat senaryosu", ["Base Case", "High Case", "Low Case"])
            step_size = st.number_input(
                "Senaryo adımı (±/MWh)", min_value=0.0, value=8.0, step=1.0
            )
            flat_price = {
                "Base Case": base_price,
                "High Case": base_price + step_size,
                "Low Case": max(0.0, base_price - step_size),
            }[scenario]
        else:
            flat_price = st.number_input("Fiyat (/MWh)", min_value=0.0, value=45.0, step=1.0)

        price_escalation = (
            st.number_input(
                "Fiyat eskalasyonu (%/yıl)",
                min_value=-5.0,
                max_value=15.0,
                value=2.0,
                step=0.25,
                help="Sabit fiyata her yıl uygulanır; eğri modunda eğri bittikten sonrası için.",
            )
            / 100.0
        )

        auction_active = False
        auction_price = auction_years = 0
        auction_escalation = 0.0
        agri_active = False
        agri_revenue_per_ha = land_area_ha = 0.0

        if is_expert:
            auction_active = st.checkbox("İhale / PPA sözleşmesi")
            if auction_active:
                auction_price = st.number_input(
                    "Sözleşme fiyatı (/MWh)", min_value=0.0, value=65.0, step=1.0
                )
                auction_years = st.number_input(
                    "Sözleşme süresi (yıl)", min_value=1, max_value=int(operating_life), value=15
                )
                auction_escalation = (
                    st.number_input(
                        "Sözleşme endekslemesi (%/yıl)",
                        min_value=0.0,
                        max_value=10.0,
                        value=0.0,
                        step=0.25,
                    )
                    / 100.0
                )

            agri_active = st.checkbox("Agri-PV yan geliri")
            if agri_active:
                agri_revenue_per_ha = st.number_input(
                    "Tarım geliri (/hektar/yıl)", min_value=0.0, value=2000.0, step=100.0
                )
                land_area_ha = st.number_input(
                    "Arazi alanı (hektar)", min_value=0.1, value=100.0, step=5.0
                )

    # ------------------------------------------------------------------
    with st.expander("4 · Yatırım (CAPEX)"):
        if mode == LITE:
            capex_mode = CapexMode.TOTAL
            capex_total = st.number_input(
                "Toplam yatırım", min_value=0.0, value=25_000_000.0, step=500_000.0
            )
            non_depreciable_share = 0.0
            solar = land = grid = other = storage_capex = 0.0
            contingency = 0.0
        else:
            capex_mode = CapexMode.UNIT
            st.caption("Birim maliyetler (MWp başına)")
            solar = st.number_input("Panel + EPC", min_value=0.0, value=400_000.0, step=10_000.0)
            land = st.number_input(
                "Arazi", min_value=0.0, value=35_000.0, step=5_000.0,
                help="Arazi amortismana tabi değildir; model bunu ayrı tutar.",
            )
            grid = st.number_input("Şebeke bağlantısı", min_value=0.0, value=50_000.0, step=5_000.0)
            other = st.number_input(
                "Diğer (izin, danışman, sigorta)", min_value=0.0, value=20_000.0, step=5_000.0
            )
            storage_capex = st.number_input(
                "Depolama (MW başına)", min_value=0.0, value=150_000.0, step=10_000.0
            )
            contingency = (
                st.number_input(
                    "Beklenmeyen giderler (%)", min_value=0.0, max_value=30.0, value=5.0, step=0.5
                )
                / 100.0
            )
            capex_total, non_depreciable_share = 0.0, 0.0

        # --- İnvertör yenileme (bakım yatırımı) ---
        inverter_years: list[int] = []
        inverter_cost = 60_000.0
        inverter_depr_years = 10
        reserve_years = 5
        if mode == LITE:
            pass  # Lite modunda kapalı; toplam CAPEX'e dahil edildiği varsayılır
        else:
            st.caption("Bakım yatırımı")
            use_inverter = st.checkbox(
                "İnvertör yenileme",
                value=True,
                help="Paneller 30+ yıl dayanır, invertörler tipik olarak 12–15 yılda "
                "değişir. Gerçek bir santralde kaçınılmaz bir kalemdir.",
            )
            if use_inverter:
                inverter_cost = st.number_input(
                    "Yenileme maliyeti (MWp başına)",
                    min_value=0.0,
                    value=60_000.0,
                    step=5_000.0,
                    help="Baz yıl parasıyla; gerçekleşme yılına enflasyonla taşınır.",
                )
                if is_expert:
                    raw_years = st.text_input(
                        "Yenileme yılları",
                        value="13",
                        help="İşletme yılı numaraları, virgülle. Örn. '13' veya '12,24'. "
                        "Ömrü aşanlar yok sayılır.",
                    )
                    try:
                        inverter_years = [
                            int(x) for x in raw_years.replace(" ", "").split(",") if x
                        ]
                    except ValueError:
                        st.warning("Yenileme yılları okunamadı, 13. yıl kullanılacak.")
                        inverter_years = [13]
                    inverter_depr_years = st.number_input(
                        "Yenileme amortisman süresi (yıl)",
                        min_value=1,
                        max_value=30,
                        value=10,
                        step=1,
                    )
                    reserve_years = st.number_input(
                        "Bakım rezervi (yıl önceden birikim)",
                        min_value=0,
                        max_value=15,
                        value=5,
                        step=1,
                        help="0 = rezerv yok. Rezervsiz bırakmak DSCR'ı yenileme "
                        "yılında çökertir; kredi sözleşmeleri bu yüzden rezerv şart koşar.",
                    )
                else:
                    inverter_years = [13]

    # ------------------------------------------------------------------
    with st.expander("5 · İşletme giderleri (OPEX)"):
        if mode == LITE:
            opex_mode = OpexMode.TOTAL
            opex_total = st.number_input(
                "Yıllık toplam OPEX", min_value=0.0, value=600_000.0, step=25_000.0
            )
            opex_fixed = opex_per_mwp = opex_per_mwh = rent_base = 0.0
            rent_mode, rent_royalty = RentMode.SIMPLE, 0.0
            opex_escalation = rent_escalation = None
        else:
            opex_mode = OpexMode.DETAIL
            opex_total = 0.0
            opex_fixed = st.number_input(
                "Sabit gider (yıllık)", min_value=0.0, value=100_000.0, step=10_000.0
            )
            opex_per_mwp = st.number_input(
                "O&M (MWp başına/yıl)", min_value=0.0, value=6_000.0, step=500.0
            )
            opex_per_mwh = st.number_input(
                "Değişken gider (/MWh)", min_value=0.0, value=1.5, step=0.1
            )
            rent_base = st.number_input(
                "Arazi kirası (yıllık)", min_value=0.0, value=50_000.0, step=5_000.0
            )
            rent_mode, rent_royalty = RentMode.SIMPLE, 0.0
            if is_expert:
                use_royalty = st.checkbox(
                    "Ciro payı kirası",
                    help="Kira = max(baz kira, elektrik gelirinin belirli bir yüzdesi)",
                )
                if use_royalty:
                    rent_mode = RentMode.MAX_BASE_OR_ROYALTY
                    rent_royalty = (
                        st.number_input(
                            "Ciro payı (%)", min_value=0.0, max_value=25.0, value=3.0, step=0.25
                        )
                        / 100.0
                    )

            opex_escalation = None
            rent_escalation = None
            if is_expert and st.checkbox(
                "Giderleri enflasyondan ayrı endeksle",
                help="Kapalıyken OPEX ve kira genel enflasyonla artar.",
            ):
                opex_escalation = (
                    st.number_input(
                        "OPEX artışı (%/yıl)", min_value=-5.0, max_value=30.0, value=2.0, step=0.25
                    )
                    / 100.0
                )
                rent_escalation = (
                    st.number_input(
                        "Kira artışı (%/yıl)", min_value=-5.0, max_value=30.0, value=2.0, step=0.25
                    )
                    / 100.0
                )

    # ------------------------------------------------------------------
    with st.expander("6 · Finansman"):
        use_debt = st.checkbox("Kredi kullan", value=(mode != LITE))
        debt_ratio, interest_rate, loan_tenor = 0.0, 0.0, 1
        upfront_fee, capitalize_idc = 0.0, True
        debt_sizing = DebtSizing.GEARING
        target_dscr, max_gearing = 1.30, 0.85
        if use_debt:
            if is_expert:
                sizing_label = st.radio(
                    "Borç boyutlama",
                    ["Sabit kaldıraç", "Hedef DSCR (sculpting)"],
                    help="Sculpting: geri ödeme planı hedef DSCR'ı her yıl tutturacak "
                    "şekilde şekillendirilir ve kredi tutarı bundan ÇIKAR. "
                    "Modern proje finansmanı böyle çalışır.",
                )
                debt_sizing = (
                    DebtSizing.SCULPTED_DSCR
                    if sizing_label.startswith("Hedef DSCR")
                    else DebtSizing.GEARING
                )

            if debt_sizing is DebtSizing.SCULPTED_DSCR:
                target_dscr = st.number_input(
                    "Hedef DSCR",
                    min_value=1.0,
                    max_value=5.0,
                    value=1.35,
                    step=0.05,
                    help="Ticari kredilerde merchant güneş için tipik aralık 1,30–1,45x.",
                )
                max_gearing = (
                    st.slider("Kaldıraç tavanı (%)", min_value=30, max_value=95, value=80, step=5)
                    / 100.0
                )
                debt_ratio = 0.70  # yalnızca çözücünün başlangıç tahmini
                st.caption("Kaldıraç bir çıktıdır; sonuç panoda gösterilir.")
            else:
                debt_ratio = (
                    st.slider("Kredi oranı (%)", min_value=0, max_value=95, value=70, step=5)
                    / 100.0
                )
            interest_rate = (
                st.number_input(
                    "Faiz oranı (%)", min_value=0.0, max_value=30.0, value=6.5, step=0.1
                )
                / 100.0
            )
            loan_tenor = st.number_input(
                "Vade (yıl)",
                min_value=1,
                max_value=int(operating_life),
                value=min(15, int(operating_life)),
                step=1,
            )
            if is_pro_plus:
                upfront_fee = (
                    st.number_input(
                        "Kullandırım komisyonu (%)",
                        min_value=0.0,
                        max_value=5.0,
                        value=1.0,
                        step=0.1,
                    )
                    / 100.0
                )
            if is_expert:
                capitalize_idc = st.checkbox(
                    "İnşaat dönemi faizini anaparaya ekle (IDC)",
                    value=True,
                    help="Kapalıysa faizi özkaynak nakden öder.",
                )

    # ------------------------------------------------------------------
    with st.expander("7 · Vergi, makro ve ömür sonu"):
        tax_rate = (
            st.number_input("Kurumlar vergisi (%)", min_value=0.0, max_value=60.0, value=25.0, step=0.5)
            / 100.0
        )
        depreciation_years = st.number_input(
            "Amortisman süresi (yıl)", min_value=1, max_value=40, value=15, step=1
        )
        loss_carryforward = True
        loss_limit = 0
        if is_expert:
            loss_carryforward = st.checkbox("Zarar mahsubu", value=True)
            if loss_carryforward:
                loss_limit = st.number_input(
                    "Zarar taşıma sınırı (yıl, 0 = sınırsız)",
                    min_value=0,
                    max_value=20,
                    value=5,
                    step=1,
                )

        inflation = (
            st.number_input("Enflasyon (%)", min_value=-5.0, max_value=50.0, value=2.0, step=0.25)
            / 100.0
        )
        cost_of_equity = (
            st.number_input(
                "Özkaynak maliyeti Ke (%)",
                min_value=0.0,
                max_value=40.0,
                value=9.0,
                step=0.25,
                help="FCFE bu oranla iskonto edilir. WACC DEĞİLDİR.",
            )
            / 100.0
        )
        lcoe_discount_rate = (
            st.number_input(
                "LCOE iskonto oranı / WACC (%)",
                min_value=0.0,
                max_value=30.0,
                value=6.0,
                step=0.25,
            )
            / 100.0
        )
        grid_emission_factor = st.number_input(
            "Şebeke emisyon faktörü (tCO₂/MWh)", min_value=0.0, max_value=1.5, value=0.30, step=0.01
        )

        decommissioning_per_mwp = salvage_value = 0.0
        if is_expert:
            decommissioning_per_mwp = st.number_input(
                "Söküm maliyeti (MWp başına)", min_value=0.0, value=0.0, step=1_000.0
            )
            salvage_value = st.number_input("Hurda değeri", min_value=0.0, value=0.0, step=10_000.0)


# ======================================================================
# MODELİ ÇALIŞTIR
# ======================================================================

try:
    inputs = ProjectInputs(
        project_name=project_name,
        currency=currency,
        base_year=int(base_year),
        construction_years=int(construction_years),
        capex_schedule=capex_schedule,
        operating_life=int(operating_life),
        capacity_mwp=capacity_mwp,
        specific_yield=specific_yield,
        degradation=degradation,
        availability=availability,
        p_factor=p_factor,
        storage_power_mw=storage_power_mw,
        storage_energy_mwh=storage_energy_mwh,
        storage_cycles_per_year=storage_cycles,
        storage_round_trip_efficiency=storage_rte,
        storage_spread=storage_spread,
        price_mode=price_mode,
        flat_price=flat_price,
        price_escalation=price_escalation,
        price_curve=price_curve,
        capture_rate=capture_rate,
        auction_active=auction_active,
        auction_price=float(auction_price),
        auction_years=int(auction_years) if auction_active else 15,
        auction_escalation=auction_escalation,
        agri_active=agri_active,
        agri_revenue_per_ha=agri_revenue_per_ha,
        land_area_ha=land_area_ha,
        capex_mode=capex_mode,
        capex_total=capex_total,
        capex_solar_per_mwp=solar,
        capex_land_per_mwp=land,
        capex_grid_per_mwp=grid,
        capex_other_per_mwp=other,
        capex_storage_per_mw=storage_capex,
        contingency=contingency,
        non_depreciable_share=non_depreciable_share,
        inverter_replacement_years=inverter_years,
        inverter_cost_per_mwp=inverter_cost,
        inverter_depreciation_years=int(inverter_depr_years),
        maintenance_reserve_years=int(reserve_years),
        opex_mode=opex_mode,
        opex_total=opex_total,
        opex_fixed=opex_fixed,
        opex_per_mwp=opex_per_mwp,
        opex_per_mwh=opex_per_mwh,
        rent_base=rent_base,
        rent_mode=rent_mode,
        rent_royalty=rent_royalty,
        opex_escalation=opex_escalation,
        rent_escalation=rent_escalation,
        use_debt=use_debt,
        debt_sizing=debt_sizing,
        debt_ratio=debt_ratio,
        target_dscr=target_dscr,
        max_gearing=max_gearing,
        interest_rate=interest_rate,
        loan_tenor=int(loan_tenor),
        upfront_fee=upfront_fee,
        capitalize_idc=capitalize_idc,
        tax_rate=tax_rate,
        depreciation_years=int(depreciation_years),
        loss_carryforward=loss_carryforward,
        loss_carryforward_limit=int(loss_limit),
        decommissioning_per_mwp=decommissioning_per_mwp,
        salvage_value=salvage_value,
        inflation=inflation,
        cost_of_equity=cost_of_equity,
        lcoe_discount_rate=lcoe_discount_rate,
        grid_emission_factor=grid_emission_factor,
    )
except ValueError as exc:
    st.error(str(exc))
    st.stop()

result = run_model(inputs)
cur = inputs.currency
df = result.to_dataframe()

# ======================================================================
# ÜST BİLGİ
# ======================================================================

st.markdown(
    f"""
<div class="sfi-masthead">
  <div class="sfi-eyebrow">Proje finansmanı modeli</div>
  <div class="sfi-name">{inputs.project_name}</div>
  <div class="sfi-meta">
    {theme.tr_number(inputs.capacity_mwp, 0)} MWp <i>/</i>
    COD {inputs.cod_year} <i>/</i>
    {inputs.operating_life} yıl işletme <i>/</i>
    {mode}
  </div>
</div>
""",
    unsafe_allow_html=True,
)

k1, k2, k3, k4, k5 = st.columns(5)

with k1:
    st.metric(
        "Özkaynak IRR",
        "n/a" if result.equity_irr is None else theme.percent(result.equity_irr),
        delta=None
        if result.equity_irr is None
        else f"{theme.tr_number((result.equity_irr - inputs.cost_of_equity) * 100, 1)} puan / Ke",
        help="FCFE serisini sıfırlayan iskonto oranı. Delta, özkaynak maliyetine olan farktır.",
    )

with k2:
    st.metric(
        "NPV",
        theme.compact_money(result.project_npv, cur),
        help="FCFE, özkaynak maliyeti (Ke) ile iskonto edildi. WACC değil.",
    )

with k3:
    st.metric(
        "LCOE",
        f"{theme.tr_number(result.lcoe, 1)} {theme.currency_symbol(cur)}/MWh",
        help="WACC ile iskonto edilmiş maliyet / üretim. Finansman kalemleri hariç.",
    )

with k4:
    st.metric(
        "Minimum DSCR",
        "n/a" if result.min_dscr is None else theme.multiple(result.min_dscr),
        help="Vade boyunca en düşük borç servisi karşılama oranı.",
    )
    # İmza öğesi: rakamın kredi eşiklerine göre nerede durduğunu gösterir.
    st.markdown(theme.dscr_band(result.min_dscr), unsafe_allow_html=True)

with k5:
    st.metric(
        "Geri ödeme",
        "n/a" if result.payback_years is None else theme.years(result.payback_years),
        help="Kümülatif nakit akışının sıfırı geçtiği an.",
    )

if inputs.debt_sizing is DebtSizing.SCULPTED_DSCR and use_debt:
    spread = (
        0.0 if result.min_dscr is None else result.min_dscr - inputs.target_dscr
    )
    if result.min_dscr is not None and spread > 0.01:
        st.info(
            f"Kaldıraç tavanı ({inputs.max_gearing:.0%}) bağlayıcı: kredi "
            f"{result.effective_gearing:.1%} kaldıraçta durdu ve DSCR hedefin üstünde "
            f"({result.min_dscr:.2f}x) kaldı. Tavanı yükseltirsen borç artar."
        )
    else:
        st.success(
            f"DSCR {inputs.target_dscr:.2f}x hedefinde şekillendirildi; buna karşılık "
            f"gelen kaldıraç **{result.effective_gearing:.1%}** "
            f"({result.debt_at_cod / 1e6:,.1f}M {cur} kredi)."
        )

if result.min_dscr is not None and result.min_dscr < 1.2:
    st.warning(
        f"Minimum DSCR {result.min_dscr:.2f}x. Ticari kredilerde genelde 1,20–1,35x "
        "alt sınırı aranır; bu borç seviyesi finanse edilemeyebilir."
    )
if result.equity_irr is None:
    st.info("Nakit akışı hiç pozitife dönmediği için IRR hesaplanamadı.")

st.divider()

# ======================================================================
# SEKMELER
# ======================================================================

tabs = st.tabs(
    ["Nakit akışı", "Nakit köprüsü", "Duyarlılık", "Senaryolar", "Tablo", "Varsayımlar"]
)

# ---------------------------------------------------------------- Nakit akışı
with tabs[0]:
    ops = df[df["İşletmede"]]
    stacked = (
        ops.melt(
            id_vars="Yıl",
            value_vars=["Toplam Gelir", "OPEX", "Vergi", "Borç Servisi"],
            var_name="Kalem",
            value_name="Tutar",
        )
    )
    bars = (
        alt.Chart(stacked)
        .mark_bar()
        .encode(
            x=alt.X("Yıl:O", title=None),
            y=alt.Y("Tutar:Q", title=f"Tutar ({cur})", stack="zero"),
            color=alt.Color(
                "Kalem:N",
                scale=alt.Scale(
                    domain=["Toplam Gelir", "OPEX", "Vergi", "Borç Servisi"],
                    range=[theme.POSITIVE, theme.ACCENT, theme.NEUTRAL, theme.NEGATIVE],
                ),
                legend=alt.Legend(orient="top", title=None),
            ),
            tooltip=["Yıl", "Kalem", alt.Tooltip("Tutar:Q", format=",.0f")],
        )
        .properties(height=380)
    )
    line = (
        alt.Chart(ops)
        .mark_line(point=True, color=theme.INK, strokeWidth=2)
        .encode(
            x=alt.X("Yıl:O"),
            y=alt.Y("FCFE:Q"),
            tooltip=["Yıl", alt.Tooltip("FCFE:Q", format=",.0f")],
        )
    )
    st.altair_chart(theme.style_chart(bars + line), width="stretch")
    st.caption("Mavi çizgi: özkaynağa kalan serbest nakit akışı (FCFE).")

    cum = (
        alt.Chart(df)
        .mark_area(opacity=0.18, color=theme.INK, line={"color": theme.INK, "strokeWidth": 2})
        .encode(
            x=alt.X("Yıl:O", title=None),
            y=alt.Y("Kümülatif FCFE:Q", title=f"Kümülatif FCFE ({cur})"),
            tooltip=["Yıl", alt.Tooltip("Kümülatif FCFE:Q", format=",.0f")],
        )
        .properties(height=260)
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=theme.NEGATIVE, strokeDash=[4, 4]).encode(y="y:Q")
    st.altair_chart(theme.style_chart(cum + zero), width="stretch")

# ---------------------------------------------------------------- Waterfall
with tabs[1]:
    op_years = df[df["İşletmede"]]["Yıl"].tolist()
    if op_years:
        chosen = st.select_slider("Yıl seç", options=op_years, value=op_years[0])
        row = df[df["Yıl"] == chosen].iloc[0]
        steps = [
            ("Elektrik geliri", row["Elektrik Geliri"]),
            ("Depolama geliri", row["Depolama Geliri"]),
            ("Tarım geliri", row["Tarım Geliri"]),
            ("İşletme gideri", row["OPEX"] - row["  - Kira"]),
            ("Arazi kirası", row["  - Kira"]),
            ("Bakım rezervi", row["Bakım Rezervi"]),
            ("Vergi", row["Vergi"]),
            ("Borç servisi", row["Borç Servisi"]),
            ("Ömür sonu", row["Ömür Sonu"]),
        ]
        steps = [(label, value) for label, value in steps if abs(value) > 1e-9]

        records, running = [], 0.0
        for label, value in steps:
            records.append(
                {
                    "Kalem": label,
                    "Başlangıç": running,
                    "Bitiş": running + value,
                    "Tutar": value,
                    "Tip": "Gelir" if value > 0 else "Gider",
                }
            )
            running += value
        records.append(
            {"Kalem": "FCFE", "Başlangıç": 0.0, "Bitiş": running, "Tutar": running, "Tip": "Net"}
        )
        wf = pd.DataFrame(records)

        chart = (
            alt.Chart(wf)
            .mark_bar(size=34)
            .encode(
                x=alt.X("Kalem:N", sort=None, title=None, axis=alt.Axis(labelAngle=-35)),
                y=alt.Y("Başlangıç:Q", title=f"Tutar ({cur})"),
                y2="Bitiş:Q",
                color=alt.Color(
                    "Tip:N",
                    scale=alt.Scale(
                        domain=["Gelir", "Gider", "Net"],
                        range=[theme.POSITIVE, theme.NEGATIVE, theme.INK],
                    ),
                    legend=alt.Legend(orient="top", title=None),
                ),
                tooltip=["Kalem", alt.Tooltip("Tutar:Q", format=",.0f")],
            )
            .properties(height=400)
        )
        st.altair_chart(theme.style_chart(chart), width="stretch")
        st.caption(f"{chosen} yılı: gelirden özkaynağa kalan nakde kadar köprü.")
    else:
        st.info("İşletme yılı yok.")

# ---------------------------------------------------------------- Tornado
with tabs[2]:
    metric_label = st.selectbox(
        "Gösterge", ["NPV", "Özkaynak IRR", "LCOE", "Min. DSCR"], index=0
    )
    metric_key = {"NPV": "npv", "Özkaynak IRR": "irr", "LCOE": "lcoe", "Min. DSCR": "min_dscr"}[
        metric_label
    ]
    tornado = run_tornado(inputs, metric=metric_key)

    if tornado.empty or tornado["Etki"].isna().all():
        st.info("Bu gösterge için duyarlılık hesaplanamadı.")
    else:
        order = tornado.attrs.get("order", [])
        chart = (
            alt.Chart(tornado)
            .mark_bar()
            .encode(
                x=alt.X("Etki:Q", title=f"{metric_label} değişimi"),
                y=alt.Y("Parametre:N", sort=order, title=None),
                yOffset=alt.YOffset("Yön:N"),
                color=alt.Color(
                    "Yön:N",
                    scale=alt.Scale(
                        domain=["Yukarı", "Aşağı"], range=[theme.POSITIVE, theme.NEGATIVE]
                    ),
                    legend=alt.Legend(orient="top", title=None),
                ),
                tooltip=[
                    "Parametre",
                    "Yön",
                    "Açıklama",
                    alt.Tooltip("Değer:Q", format=",.4f"),
                    alt.Tooltip("Etki:Q", format=",.4f"),
                ],
            )
            .properties(height=40 * max(1, len(order)))
        )
        st.altair_chart(theme.style_chart(chart), width="stretch")
        st.caption(
            "Parametreler etkilerine göre sıralı. En üstteki değişken projenin "
            "en büyük risk kaynağıdır."
        )

# ---------------------------------------------------------------- Senaryolar
with tabs[3]:
    scen = run_scenarios(inputs)
    st.dataframe(
        scen.style.format(
            {
                "NPV": "{:,.0f}",
                "IRR": lambda v: "n/a" if pd.isna(v) else f"{v:.1%}",
                "LCOE": "{:,.1f}",
                "Min DSCR": lambda v: "n/a" if pd.isna(v) else f"{v:.2f}x",
                "Geri Ödeme (yıl)": lambda v: "n/a" if pd.isna(v) else f"{v:.1f}",
            }
        ),
        width="stretch",
        hide_index=True,
    )
    st.altair_chart(
        theme.style_chart(
            alt.Chart(scen)
        .mark_bar()
        .encode(
            x=alt.X("Senaryo:N", sort=None, title=None, axis=alt.Axis(labelAngle=-20)),
            y=alt.Y("NPV:Q", title=f"NPV ({cur})"),
            color=alt.condition(
                alt.datum.NPV > 0, alt.value(theme.POSITIVE), alt.value(theme.NEGATIVE)
            ),
            tooltip=["Senaryo", alt.Tooltip("NPV:Q", format=",.0f")],
        )
            .properties(height=300)
        ),
        width="stretch",
    )

# ---------------------------------------------------------------- Tablo
with tabs[4]:
    st.dataframe(df, width="stretch", hide_index=True, height=520)

# ---------------------------------------------------------------- Varsayımlar
with tabs[5]:
    st.subheader("Özet göstergeler")
    kpi = pd.DataFrame(result.kpi_dict().items(), columns=["Gösterge", "Değer"])
    st.dataframe(kpi, width="stretch", hide_index=True)

    st.subheader("Kullanılan varsayımlar")
    assumptions = pd.DataFrame(inputs.to_dict().items(), columns=["Parametre", "Değer"])
    assumptions["Değer"] = assumptions["Değer"].astype(str)
    st.dataframe(assumptions, width="stretch", hide_index=True, height=400)

# ======================================================================
# İNDİRME
# ======================================================================

st.divider()


def _safe_name(name: str) -> str:
    """Proje adını dosya adı olarak güvenli hale getirir."""
    cleaned = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in name)
    return cleaned.strip().replace(" ", "_").lower() or "proje"


def _clean_csv(frame: pd.DataFrame) -> bytes:
    """Türkçe Excel'de düzgün açılan tek tablolu CSV.

    Noktalı virgül ayracı ve virgüllü ondalık kullanılır; sayılar iki
    basamağa yuvarlanır, yoksa `3.880819891e+06` gibi değerler dosyayı
    okunamaz hale getirir.
    """
    rounded = frame.copy()
    for column in rounded.select_dtypes(include="number").columns:
        rounded[column] = rounded[column].round(2)
    return rounded.to_csv(index=False, sep=";", decimal=",").encode("utf-8-sig")


col_a, col_b = st.columns(2)
col_a.download_button(
    "Excel raporunu indir",
    data=workbook_bytes(result),
    file_name=f"solarfi_{_safe_name(inputs.project_name)}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    type="primary",
    width="stretch",
    help="Üç sekme: Özet, Nakit Akışı, Varsayımlar. Türetilmiş satırlar ve "
    "göstergeler canlı formüldür — Özet'teki iskonto oranlarını değiştirince "
    "NPV ve LCOE yeniden hesaplanır.",
)
col_b.download_button(
    "CSV olarak indir",
    data=_clean_csv(df),
    file_name=f"solarfi_{_safe_name(inputs.project_name)}_nakit_akisi.csv",
    mime="text/csv",
    width="stretch",
    help="Tek tablo, noktalı virgül ayraçlı (Türkçe Excel uyumlu).",
)

st.caption(
    "Bu araç eğitim ve ön fizibilite amaçlıdır; yatırım tavsiyesi değildir. "
    "Örnek fiyat eğrileri sentetiktir ve hiçbir gerçek piyasa görüşünü temsil etmez."
)
