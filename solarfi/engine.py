"""Güneş santrali finansal modelleme motoru.

Bu modül saf Python + NumPy'dir; Streamlit'e veya herhangi bir arayüze
bağımlı değildir. `run_model(inputs)` deterministik bir fonksiyondur:
aynı `ProjectInputs` her zaman aynı `ModelResult`'ı üretir. Testler ve
duyarlılık analizi bu özelliğe dayanır.

Zaman ekseni
------------
::

    t = 0 .. C-1        inşaat dönemi (CAPEX harcanır, üretim yok)
    t = C .. C+L-1      işletme dönemi (COD sonrası)

``C`` = ``construction_years``, ``L`` = ``operating_life``.
``t`` dönemi ``base_year + t`` takvim yılına karşılık gelir ve iskonto
edilirken ``(1 + r) ** t`` kullanılır (bkz. `solarfi.finance`).

Nakit akışı tanımı
------------------
Modelin çıktısı **FCFE** (Free Cash Flow to Equity), yani özkaynağa kalan
serbest nakit akışıdır::

    İnşaat yılı:   FCFE = -(özkaynak katkısı)
    İşletme yılı:  FCFE = EBITDA - vergi - borç servisi (+ ömür sonu kalemleri)

FCFE özkaynağa ait olduğu için **özkaynak maliyeti (Ke)** ile iskonto
edilir, WACC ile değil. WACC yalnızca LCOE hesabında kullanılır.

Bütün tutarlar **nominal**dir (enflasyon içerir).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .finance import irr, npv, payback_period, pmt
from .inputs import CapexMode, DebtSizing, OpexMode, PriceMode, ProjectInputs, RentMode

__all__ = ["ModelResult", "run_model"]


@dataclass
class ModelResult:
    """Motorun çıktısı: dönem bazlı seriler + özet göstergeler."""

    inputs: ProjectInputs

    # Zaman ekseni ------------------------------------------------------
    periods: list[int] = field(default_factory=list)  # 0, 1, 2, ...
    calendar_years: list[int] = field(default_factory=list)
    is_operating: list[bool] = field(default_factory=list)

    # Operasyonel seriler ----------------------------------------------
    generation_mwh: list[float] = field(default_factory=list)
    price: list[float] = field(default_factory=list)  # gerçekleşen ortalama fiyat
    revenue_electricity: list[float] = field(default_factory=list)
    revenue_storage: list[float] = field(default_factory=list)
    revenue_agri: list[float] = field(default_factory=list)
    revenue_total: list[float] = field(default_factory=list)

    # Gider ve nakit akışı (giderler NEGATİF işaretlidir) ---------------
    opex: list[float] = field(default_factory=list)
    rent: list[float] = field(default_factory=list)
    ebitda: list[float] = field(default_factory=list)
    depreciation: list[float] = field(default_factory=list)
    interest: list[float] = field(default_factory=list)
    principal: list[float] = field(default_factory=list)
    debt_service: list[float] = field(default_factory=list)
    debt_balance: list[float] = field(default_factory=list)
    ebt: list[float] = field(default_factory=list)
    tax: list[float] = field(default_factory=list)
    capex: list[float] = field(default_factory=list)
    #: İşletme dönemindeki bakım yatırımları (invertör yenileme), negatif.
    #: Bu, paranın HARCANDIĞI yıldır.
    maintenance_capex: list[float] = field(default_factory=list)
    #: Bakım rezervine yapılan yıllık katkılar, negatif. Nakit akışını ve
    #: DSCR'ı fiilen etkileyen seri budur.
    maintenance_reserve: list[float] = field(default_factory=list)
    equity_injection: list[float] = field(default_factory=list)
    terminal_cashflow: list[float] = field(default_factory=list)
    cfads: list[float] = field(default_factory=list)
    dscr: list[float | None] = field(default_factory=list)
    fcfe: list[float] = field(default_factory=list)

    # Özet göstergeler --------------------------------------------------
    total_capex: float = 0.0
    #: Ömür boyu toplam bakım yatırımı (nominal, pozitif).
    total_maintenance_capex: float = 0.0
    capitalised_idc: float = 0.0
    financing_fees: float = 0.0
    debt_at_cod: float = 0.0
    #: COD'daki kredi bakiyesinin toplam CAPEX'e oranı. GEARING modunda
    #: girdiyle (artı IDC etkisiyle) hemen hemen aynıdır; SCULPTED_DSCR
    #: modunda ise bir ÇIKTIDIR — DSCR kısıtından türer.
    effective_gearing: float = 0.0
    #: Sculpting çözücüsünün kullandığı iterasyon sayısı (GEARING modunda 0).
    sculpting_iterations: int = 0
    #: Sculpting çözücüsü toleransa yakınsadı mı?
    sculpting_converged: bool = True
    total_equity: float = 0.0
    equity_irr: float | None = None
    project_npv: float = 0.0
    lcoe: float = 0.0
    min_dscr: float | None = None
    avg_dscr: float | None = None
    payback_years: float | None = None
    discounted_payback_years: float | None = None
    lifetime_generation_mwh: float = 0.0
    annual_co2_avoided_t: float = 0.0
    lifetime_co2_avoided_t: float = 0.0

    # ------------------------------------------------------------------
    def to_dataframe(self) -> pd.DataFrame:
        """Dönem bazlı bütün serileri tek bir tabloya çevirir."""
        return pd.DataFrame(
            {
                "Dönem": self.periods,
                "Yıl": self.calendar_years,
                "İşletmede": self.is_operating,
                "Üretim (MWh)": self.generation_mwh,
                "Fiyat (/MWh)": self.price,
                "Elektrik Geliri": self.revenue_electricity,
                "Depolama Geliri": self.revenue_storage,
                "Tarım Geliri": self.revenue_agri,
                "Toplam Gelir": self.revenue_total,
                "OPEX": self.opex,
                "  - Kira": self.rent,
                "EBITDA": self.ebitda,
                "Amortisman": self.depreciation,
                "Faiz": self.interest,
                "Anapara": self.principal,
                "Borç Servisi": self.debt_service,
                "Borç Bakiyesi": self.debt_balance,
                "Vergi Öncesi Kâr": self.ebt,
                "Vergi": self.tax,
                "CAPEX": self.capex,
                "Bakım Yatırımı": self.maintenance_capex,
                "Bakım Rezervi": self.maintenance_reserve,
                "Özkaynak Katkısı": self.equity_injection,
                "Ömür Sonu": self.terminal_cashflow,
                "CFADS": self.cfads,
                "DSCR": self.dscr,
                "FCFE": self.fcfe,
                "Kümülatif FCFE": np.cumsum(self.fcfe).tolist(),
            }
        )

    def kpi_dict(self) -> dict[str, object]:
        """Özet göstergeleri raporlanabilir bir sözlük olarak döner."""
        cur = self.inputs.currency
        return {
            "Toplam CAPEX": f"{self.total_capex:,.0f} {cur}",
            "Bakım Yatırımı (ömür boyu)": f"{self.total_maintenance_capex:,.0f} {cur}",
            "Kapitalize İnşaat Faizi (IDC)": f"{self.capitalised_idc:,.0f} {cur}",
            "Finansman Komisyonu": f"{self.financing_fees:,.0f} {cur}",
            "COD'da Kredi Bakiyesi": f"{self.debt_at_cod:,.0f} {cur}",
            "Efektif Kaldıraç": f"{self.effective_gearing:.1%}",
            "Toplam Özkaynak": f"{self.total_equity:,.0f} {cur}",
            "Özkaynak IRR": "n/a" if self.equity_irr is None else f"{self.equity_irr:.2%}",
            "NPV (Ke ile)": f"{self.project_npv:,.0f} {cur}",
            "LCOE": f"{self.lcoe:,.2f} {cur}/MWh",
            "Minimum DSCR": "n/a" if self.min_dscr is None else f"{self.min_dscr:.2f}x",
            "Ortalama DSCR": "n/a" if self.avg_dscr is None else f"{self.avg_dscr:.2f}x",
            "Geri Ödeme (yıl)": "n/a" if self.payback_years is None else f"{self.payback_years:.1f}",
            "İskontolu Geri Ödeme (yıl)": (
                "n/a"
                if self.discounted_payback_years is None
                else f"{self.discounted_payback_years:.1f}"
            ),
            "Ömür Boyu Üretim": f"{self.lifetime_generation_mwh:,.0f} MWh",
            "Yıllık Ort. CO₂ Tasarrufu": f"{self.annual_co2_avoided_t:,.0f} t",
            "Ömür Boyu CO₂ Tasarrufu": f"{self.lifetime_co2_avoided_t:,.0f} t",
        }


# ----------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------


def _capex_breakdown(inp: ProjectInputs) -> tuple[float, float]:
    """(toplam CAPEX, amortismana tabi olmayan tutar) döner."""
    if inp.capex_mode is CapexMode.TOTAL:
        base = inp.capex_total
        total = base * (1.0 + inp.contingency)
        non_depr = total * inp.non_depreciable_share
    else:
        per_mwp = (
            inp.capex_solar_per_mwp
            + inp.capex_land_per_mwp
            + inp.capex_grid_per_mwp
            + inp.capex_other_per_mwp
        )
        base = per_mwp * inp.capacity_mwp + inp.capex_storage_per_mw * inp.storage_power_mw
        total = base * (1.0 + inp.contingency)
        # Arazi amortismana tabi değildir.
        non_depr = inp.capex_land_per_mwp * inp.capacity_mwp * (1.0 + inp.contingency)
    return total, min(non_depr, total)


def _capex_weights(inp: ProjectInputs) -> list[float]:
    """İnşaat yıllarına CAPEX dağılım ağırlıkları (toplamı 1)."""
    raw = list(inp.capex_schedule) if inp.capex_schedule else [1.0] * inp.construction_years
    total = sum(raw)
    return [w / total for w in raw]


def _market_price(inp: ProjectInputs, calendar_year: int) -> float:
    """Verilen takvim yılı için piyasa fiyatı (yakalama oranı uygulanmadan)."""
    if inp.price_mode is PriceMode.FLAT:
        return inp.flat_price * ((1.0 + inp.price_escalation) ** (calendar_year - inp.base_year))

    curve = inp.price_curve
    if calendar_year in curve:
        return float(curve[calendar_year])
    last_year = max(curve)
    if calendar_year < min(curve):
        # Eğrinin başlangıcından önceki yıllar: geriye doğru sabit tutulur.
        return float(curve[min(curve)])
    # Eğri bittikten sonrası: eskalasyonla uzatılır.
    return float(curve[last_year]) * ((1.0 + inp.price_escalation) ** (calendar_year - last_year))


def _realised_price(inp: ProjectInputs, op_year: int, calendar_year: int) -> float:
    """Üretimin fiilen sattığı ortalama fiyat.

    İhale/PPA süresi içindeyse sözleşme fiyatı, dışındaysa piyasa fiyatı
    x yakalama oranı geçerlidir.
    """
    if inp.auction_active and op_year < inp.auction_years:
        return inp.auction_price * ((1.0 + inp.auction_escalation) ** op_year)
    return _market_price(inp, calendar_year) * inp.capture_rate


def _tax_for_year(
    ebt: float,
    tax_rate: float,
    pool: deque[list[float]],
    use_carryforward: bool,
    limit: int,
) -> float:
    """Yılın vergisini hesaplar ve zarar havuzunu günceller.

    `pool` içindeki her eleman ``[kalan_zarar, yaş]`` çiftidir. `limit` > 0
    ise `limit` yıldan eski zararlar kullanılamaz hale gelir.
    """
    if not use_carryforward:
        return max(0.0, ebt) * tax_rate

    # Zararları yaşlandır ve süresi dolanları at.
    for item in pool:
        item[1] += 1
    if limit > 0:
        while pool and pool[0][1] > limit:
            pool.popleft()

    if ebt < 0:
        pool.append([-ebt, 0])
        return 0.0

    taxable = ebt
    while taxable > 0 and pool:
        available = pool[0][0]
        used = min(available, taxable)
        taxable -= used
        pool[0][0] -= used
        if pool[0][0] <= 1e-9:
            pool.popleft()
    return taxable * tax_rate


# ----------------------------------------------------------------------
# Ana fonksiyon
# ----------------------------------------------------------------------


def run_model(inp: ProjectInputs) -> ModelResult:
    """Tam finansal modeli çalıştırır.

    ``debt_sizing`` GEARING ise tek geçişte hesaplanır. SCULPTED_DSCR ise
    önce `_solve_sculpted_debt` ile kredi planı çözülür, sonra o planla
    model çalıştırılır.
    """
    inp.validate()

    if inp.use_debt and inp.debt_sizing is DebtSizing.SCULPTED_DSCR:
        gearing, service, iterations, converged = _solve_sculpted_debt(inp)
        result = _run_core(inp, gearing=gearing, service=service)
        result.sculpting_iterations = iterations
        result.sculpting_converged = converged
        return result

    gearing = inp.debt_ratio if inp.use_debt else 0.0
    return _run_core(inp, gearing=gearing, service=None)


# ----------------------------------------------------------------------
# DSCR'a göre borç boyutlama (sculpting)
# ----------------------------------------------------------------------


def _solve_sculpted_debt(
    inp: ProjectInputs, max_iterations: int = 40, tolerance: float = 1.0
) -> tuple[float, list[float], int, bool]:
    """Hedef DSCR'ı tutturan kredi tutarını ve geri ödeme planını çözer.

    Yöntem
    ------
    Her işletme yılı için hedef borç servisi::

        DS_j = CFADS_j / hedef_DSCR

    Kredi tutarı bu ödemelerin kredi faiziyle bugünkü değeridir::

        D = Σ DS_j / (1 + r)^(j+1)

    Bu tanım gereği plan krediyi vade sonunda tam olarak kapatır ve DSCR
    her yıl hedefte sabit kalır.

    **Neden iterasyon gerekiyor?** CFADS vergiden sonraki nakittir; vergi
    faiz giderine bağlıdır; faiz kredi tutarına bağlıdır; kredi tutarı da
    CFADS'e. Ayrıca kapitalize inşaat faizi ve komisyonlar amortisman
    tabanını, o da yine vergiyi değiştirir. Bu döngü kapalı formda
    çözülemediği için sabit nokta iterasyonu kullanılır: bir kaldıraçla
    modeli çalıştır, çıkan CFADS'ten yeni kredi tutarını hesapla, tekrarla.
    Vergi kalkanı etkisi ikinci mertebeden olduğu için birkaç adımda
    yakınsar.

    Returns
    -------
    (kaldıraç, borç servisi planı, iterasyon sayısı, yakınsadı mı)
    """
    r = inp.interest_rate
    tenor = min(inp.loan_tenor, inp.operating_life)
    total_capex, _ = _capex_breakdown(inp)

    # İnşaat dönemi finansmanı kaldıraçla doğrusaldır: g=1 ile bir kez
    # hesaplayıp ölçekleyebiliriz.
    _, _, _, _, balance_per_unit_gearing = _construction_financing(inp, 1.0)
    if balance_per_unit_gearing <= 0 or total_capex <= 0:
        return 0.0, [], 0, True

    # Tavan, COD'daki kredi bakiyesinin toplam CAPEX'e oranına uygulanır —
    # "kaldıraç" denince kastedilen budur.
    max_debt = inp.max_gearing * total_capex
    gearing = inp.debt_ratio
    service: list[float] = []
    previous_service: list[float] = []
    previous_debt = -1.0
    converged = False
    iteration = 0

    for step in range(1, max_iterations + 1):
        iteration = step
        trial = _run_core(inp, gearing=gearing, service=service or None)

        # CFADS yalnızca vade içindeki işletme yıllarından alınır.
        cfads = [trial.cfads[inp.construction_years + j] for j in range(tenor)]
        service = [max(0.0, c / inp.target_dscr) for c in cfads]
        debt = sum(ds / ((1.0 + r) ** (j + 1)) for j, ds in enumerate(service))

        # Kaldıraç tavanı bağlayıcıysa planı hemen burada ölçekle. Ölçeklemeyi
        # döngüden sonraya bırakmak, iterasyonun test ettiği plan ile nihai
        # planın farklı olmasına ve DSCR'ın düz çıkmamasına yol açardı.
        if debt > max_debt:
            service = [ds * (max_debt / debt) for ds in service]
            debt = max_debt

        gearing = debt / balance_per_unit_gearing

        # Hem tutar hem de geri ödeme PROFİLİ sabitlenmeli. Yalnızca tutara
        # bakmak yetmez: kaldıraç tavanına takılan senaryolarda tutar ilk
        # adımda çakılır ama profil henüz oturmamıştır, bu da DSCR'ın düz
        # çıkmamasına yol açar.
        profile_shift = (
            max(abs(a - b) for a, b in zip(service, previous_service, strict=True))
            if len(previous_service) == len(service) and service
            else float("inf")
        )
        if abs(debt - previous_debt) < tolerance and profile_shift < tolerance:
            converged = True
            break
        previous_debt = debt
        previous_service = service

    return gearing, service, iteration, converged


def _construction_financing(
    inp: ProjectInputs, gearing: float
) -> tuple[list[float], list[float], float, float, float]:
    """İnşaat dönemi nakit akışlarını hesaplar.

    Returns
    -------
    (özkaynak katkıları, inşaat dönemi faiz serisi, kapitalize IDC,
     komisyon toplamı, COD'daki kredi bakiyesi)
    """
    C = inp.construction_years
    total_capex, _ = _capex_breakdown(inp)
    weights = _capex_weights(inp)

    equity_injection = [0.0] * C
    interest_series = [0.0] * C
    balance = 0.0
    capitalised_idc = 0.0
    fees_total = 0.0

    for t in range(C):
        spend = total_capex * weights[t]
        draw = spend * gearing if inp.use_debt else 0.0
        fee = draw * inp.upfront_fee
        fees_total += fee
        # Harcamanın yıla yayıldığı varsayımıyla yıl ortası çekim konvansiyonu.
        idc = (balance + draw * 0.5) * inp.interest_rate if inp.use_debt else 0.0
        interest_series[t] = -idc

        equity_spend = spend - draw + fee
        if inp.capitalize_idc:
            balance += draw + idc
            capitalised_idc += idc
        else:
            balance += draw
            equity_spend += idc  # IDC'yi özkaynak nakden öder

        equity_injection[t] = -equity_spend

    return equity_injection, interest_series, capitalised_idc, fees_total, balance


def _run_core(
    inp: ProjectInputs, gearing: float, service: list[float] | None = None
) -> ModelResult:
    """Verilen kaldıraç ve geri ödeme planıyla modeli tek geçişte çalıştırır.

    ``service`` ``None`` ise eşit taksitli anüite kullanılır; aksi halde
    verilen borç servisi planı uygulanır (sculpting).
    """
    C = inp.construction_years
    L = inp.operating_life
    T = C + L

    res = ModelResult(inputs=inp)
    res.periods = list(range(T))
    res.calendar_years = [inp.base_year + t for t in range(T)]
    res.is_operating = [t >= C for t in range(T)]

    # -- CAPEX -----------------------------------------------------------
    total_capex, non_depreciable = _capex_breakdown(inp)
    weights = _capex_weights(inp)
    capex_by_period = [0.0] * T
    for t in range(C):
        capex_by_period[t] = -total_capex * weights[t]
    res.total_capex = total_capex
    res.capex = capex_by_period

    # -- İnşaat dönemi finansmanı (drawdown, komisyon, IDC) --------------
    (
        construction_equity,
        construction_interest,
        capitalised_idc,
        fees_total,
        balance,
    ) = _construction_financing(inp, gearing)

    equity_injection = construction_equity + [0.0] * L
    interest_series = construction_interest + [0.0] * L

    res.capitalised_idc = capitalised_idc
    res.financing_fees = fees_total
    res.debt_at_cod = balance
    res.effective_gearing = balance / total_capex if total_capex > 0 else 0.0

    # -- Amortisman planı --------------------------------------------------
    # Komisyon ve kapitalize faiz aktifleştirilir; arazi amortismana tabi değildir.
    depreciable_base = max(0.0, total_capex - non_depreciable + capitalised_idc + fees_total)
    annual_depreciation = depreciable_base / inp.depreciation_years

    # Amortisman artık tek bir taban değil, bir PLAN: ilk yatırımın üstüne
    # her invertör yenilemesi kendi amortisman serisini ekler.
    depr = [0.0] * T
    for j in range(min(inp.depreciation_years, L)):
        depr[C + j] += annual_depreciation

    # -- Bakım yatırımı (invertör yenileme) --------------------------------
    maintenance_capex = [0.0] * T
    reserve = [0.0] * T
    for j0 in inp.effective_inverter_years:
        # Maliyet baz yıl parasıyla verilir, gerçekleşme yılına taşınır.
        cost = inp.inverter_cost_per_mwp * inp.capacity_mwp * ((1.0 + inp.inflation) ** (C + j0))
        maintenance_capex[C + j0] = -cost

        # Rezerv hesabı: maliyet, harcamadan önceki yıllara eşit yayılır.
        # Yenileme çok erken düşüyorsa biriktirecek yıl kalmaz; o durumda
        # harcama kendi yılında nakitten çıkar.
        accrual_years = min(inp.maintenance_reserve_years, j0)
        if accrual_years > 0:
            for k in range(accrual_years):
                reserve[C + j0 - accrual_years + k] -= cost / accrual_years
        else:
            reserve[C + j0] -= cost

        # Yenileme aktifleştirilir ve kalan ömür ile amortisman süresinin
        # kısası boyunca doğrusal olarak itfa edilir.
        span = min(inp.inverter_depreciation_years, L - j0)
        if span > 0:
            for k in range(span):
                depr[C + j0 + k] += cost / span
    res.maintenance_capex = maintenance_capex
    res.maintenance_reserve = reserve
    res.total_maintenance_capex = -sum(maintenance_capex)

    # -- Kredi geri ödeme planı -------------------------------------------
    tenor = min(inp.loan_tenor, L)
    # Ödeme planı: sculpting planı verilmişse o, verilmemişse eşit taksitli anüite.
    if service is not None:
        payment_schedule = list(service[:tenor]) + [0.0] * max(0, tenor - len(service))
    else:
        annuity = pmt(inp.interest_rate, tenor, balance) if inp.use_debt else 0.0
        payment_schedule = [annuity] * tenor

    # -- Seriler -----------------------------------------------------------
    gen = [0.0] * T
    price = [0.0] * T
    rev_el = [0.0] * T
    rev_st = [0.0] * T
    rev_ag = [0.0] * T
    rev_tot = [0.0] * T
    opex = [0.0] * T
    rent = [0.0] * T
    ebitda = [0.0] * T
    principal = [0.0] * T
    debt_service = [0.0] * T
    debt_balance = [0.0] * T
    ebt_list = [0.0] * T
    tax_list = [0.0] * T
    terminal = [0.0] * T
    cfads = [0.0] * T
    dscr: list[float | None] = [None] * T
    fcfe = [0.0] * T

    for t in range(C):
        debt_balance[t] = balance
        fcfe[t] = equity_injection[t]

    loss_pool: deque[list[float]] = deque()
    opex_esc = inp.effective_opex_escalation
    rent_esc = inp.effective_rent_escalation

    # Depolama yıllık deşarj enerjisi (sabit; kayıplar hariç).
    storage_discharge_mwh = (
        inp.storage_energy_mwh
        * inp.storage_cycles_per_year
        * inp.storage_round_trip_efficiency
        if inp.storage_power_mw > 0
        else 0.0
    )

    for j in range(L):
        t = C + j
        year = res.calendar_years[t]
        infl = (1.0 + inp.inflation) ** j

        # --- Üretim ve gelir ---
        gen[t] = (
            inp.capacity_mwp
            * inp.specific_yield
            * inp.availability
            * inp.p_factor
            * ((1.0 - inp.degradation) ** j)
        )
        price[t] = _realised_price(inp, j, year)
        rev_el[t] = gen[t] * price[t]

        if storage_discharge_mwh > 0:
            rev_st[t] = storage_discharge_mwh * inp.storage_spread * infl

        if inp.agri_active:
            rev_ag[t] = inp.agri_revenue_per_ha * inp.land_area_ha * infl

        rev_tot[t] = rev_el[t] + rev_st[t] + rev_ag[t]

        # --- İşletme giderleri ---
        opex_infl = (1.0 + opex_esc) ** j
        if inp.opex_mode is OpexMode.TOTAL:
            operating_cost = inp.opex_total * opex_infl
            rent_cost = 0.0
        else:
            operating_cost = (
                inp.opex_fixed + inp.opex_per_mwp * inp.capacity_mwp + inp.opex_per_mwh * gen[t]
            ) * opex_infl
            base_rent = inp.rent_base * ((1.0 + rent_esc) ** j)
            if inp.rent_mode is RentMode.MAX_BASE_OR_ROYALTY:
                rent_cost = max(base_rent, rev_el[t] * inp.rent_royalty)
            else:
                rent_cost = base_rent

        rent[t] = -rent_cost
        opex[t] = -(operating_cost + rent_cost)
        ebitda[t] = rev_tot[t] + opex[t]

        # --- Borç servisi ---
        if inp.use_debt and j < tenor and balance > 1e-6:
            interest_amount = balance * inp.interest_rate
            payment = payment_schedule[j]
            principal_amount = min(payment - interest_amount, balance)
            balance -= principal_amount
            interest_series[t] = -interest_amount
            principal[t] = -principal_amount
            debt_service[t] = -(interest_amount + principal_amount)
        debt_balance[t] = balance

        # --- Vergi ---
        # Amortisman plandan okunur (ilk yatırım + invertör yenilemeleri).
        ebt_list[t] = ebitda[t] - depr[t] + interest_series[t]
        tax_amount = _tax_for_year(
            ebt_list[t],
            inp.tax_rate,
            loss_pool,
            inp.loss_carryforward,
            inp.loss_carryforward_limit,
        )
        tax_list[t] = -tax_amount

        # --- Ömür sonu ---
        if j == L - 1:
            terminal[t] = inp.salvage_value - inp.decommissioning_per_mwp * inp.capacity_mwp

        # --- Nakit akışları ---
        # Bakım rezervi CFADS'ten düşülür: kredi verenler borç servisinden
        # önce santralin ayakta kalmasını finanse eder. Nakde fiilen çarpan
        # kalem harcamanın kendisi değil, rezerve yapılan katkıdır — para
        # yıllar içinde birikip harcama yılında hesaptan çıkar.
        cfads[t] = ebitda[t] + tax_list[t] + reserve[t]
        if debt_service[t] < 0:
            dscr[t] = cfads[t] / abs(debt_service[t])
        fcfe[t] = cfads[t] + debt_service[t] + terminal[t]

    # -- Sonuçları doldur --------------------------------------------------
    res.generation_mwh = gen
    res.price = price
    res.revenue_electricity = rev_el
    res.revenue_storage = rev_st
    res.revenue_agri = rev_ag
    res.revenue_total = rev_tot
    res.opex = opex
    res.rent = rent
    res.ebitda = ebitda
    res.depreciation = depr
    res.interest = interest_series
    res.principal = principal
    res.debt_service = debt_service
    res.debt_balance = debt_balance
    res.ebt = ebt_list
    res.tax = tax_list
    res.equity_injection = equity_injection
    res.terminal_cashflow = terminal
    res.cfads = cfads
    res.dscr = dscr
    res.fcfe = fcfe

    # -- Göstergeler -------------------------------------------------------
    res.total_equity = -sum(equity_injection)
    res.project_npv = npv(inp.cost_of_equity, fcfe)
    res.equity_irr = irr(fcfe) if res.total_equity > 1e-6 else None
    res.payback_years = payback_period(fcfe)
    res.discounted_payback_years = payback_period(fcfe, inp.cost_of_equity)

    valid_dscr = [d for d in dscr if d is not None]
    if valid_dscr:
        res.min_dscr = min(valid_dscr)
        res.avg_dscr = sum(valid_dscr) / len(valid_dscr)

    # LCOE: nominal maliyetlerin BD'si / nominal üretimin BD'si.
    # Finansman kalemleri (faiz, komisyon, vergi) LCOE'ye GİRMEZ; sermaye
    # maliyeti zaten iskonto oranının içindedir.
    cost_stream = [
        -capex_by_period[t] - opex[t] - maintenance_capex[t] - min(0.0, terminal[t])
        for t in range(T)
    ]
    npv_cost = npv(inp.lcoe_discount_rate, cost_stream)
    npv_gen = npv(inp.lcoe_discount_rate, gen)
    res.lcoe = npv_cost / npv_gen if npv_gen > 1e-9 else 0.0

    res.lifetime_generation_mwh = sum(gen)
    res.lifetime_co2_avoided_t = res.lifetime_generation_mwh * inp.grid_emission_factor
    res.annual_co2_avoided_t = res.lifetime_co2_avoided_t / L if L else 0.0

    return res
