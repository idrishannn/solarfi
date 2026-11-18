"""Model girdileri.

Tek bir dataclass (`ProjectInputs`) modelin bütün varsayımlarını tutar.
Motor bu nesneden başka hiçbir yerden veri okumaz — bu sayede her sonuç
tam olarak tek bir girdi kümesine kadar geri izlenebilir ve raporlarda
varsayım tablosu otomatik üretilebilir.

Yüzde alanları **ondalık kesir** olarak tutulur (ör. %7,5 faiz -> 0.075).
Arayüz katmanı dönüşümü kendisi yapar; motor asla "yüzde mi kesir mi"
tahmin etmez.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from enum import Enum


class PriceMode(str, Enum):
    """Elektrik fiyatının nasıl belirleneceği."""

    FLAT = "flat"  # tek bir fiyat, yıllık eskalasyonla artar
    CURVE = "curve"  # yıl -> fiyat eğrisi (CSV'den)


class CapexMode(str, Enum):
    TOTAL = "total"  # tek kalem toplam yatırım
    UNIT = "unit"  # MWp başına birim maliyetler


class OpexMode(str, Enum):
    TOTAL = "total"  # tek kalem yıllık işletme gideri
    DETAIL = "detail"  # sabit + MWp + MWh + kira kalemleri


class RentMode(str, Enum):
    SIMPLE = "simple"  # sadece baz kira, eskalasyonla artar
    MAX_BASE_OR_ROYALTY = "max_base_or_royalty"  # max(baz kira, ciro payı)


class DebtSizing(str, Enum):
    """Kredi tutarının nasıl belirleneceği.

    ``GEARING``
        Klasik yaklaşım: borç = CAPEX × sabit bir oran. Basittir ama
        gerçek kredi süreçlerini yansıtmaz.

    ``SCULPTED_DSCR``
        Modern proje finansmanı yaklaşımı: geri ödeme planı, her dönemde
        hedef DSCR'ı tutturacak şekilde **şekillendirilir** (sculpting) ve
        kredi tutarı bu ödemelerin bugünkü değeri olarak *çıkar*. Yani
        kaldıraç bir girdi değil, sonuçtur.

        Neden önemli: üretimi bozunan ve fiyatı düşen bir varlıkta eşit
        taksitli anüite, ilk yıl minimum DSCR verip sonra gereksizce
        yükselir. Bu, borç kapasitesini israf etmek demektir.
    """

    GEARING = "gearing"
    SCULPTED_DSCR = "sculpted_dscr"


@dataclass
class ProjectInputs:
    """Bir güneş santrali projesinin bütün finansal varsayımları."""

    # ------------------------------------------------------------------
    # Proje kimliği ve zaman ekseni
    # ------------------------------------------------------------------
    project_name: str = "Adsız Proje"
    currency: str = "EUR"
    #: t=0'ın takvim yılı (finansal kapanış / ilk yatırım harcaması).
    base_year: int = 2026
    #: İnşaat süresi (yıl). CAPEX bu dönemde harcanır, üretim sonrasında başlar.
    construction_years: int = 1
    #: CAPEX'in inşaat yıllarına dağılım ağırlıkları. Boşsa eşit dağıtılır.
    capex_schedule: list[float] = field(default_factory=list)
    #: Ticari işletmeye geçişten (COD) sonraki işletme ömrü (yıl).
    operating_life: int = 30

    # ------------------------------------------------------------------
    # Teknik
    # ------------------------------------------------------------------
    capacity_mwp: float = 50.0
    #: Spesifik verim (kWh/kWp/yıl), P50 ve kayıplar sonrası.
    specific_yield: float = 1650.0
    #: Yıllık panel bozunması (kesir, ör. 0.005 = %0,5).
    degradation: float = 0.005
    #: Teknik kullanılabilirlik (kesir).
    availability: float = 0.99
    #: Üretim olasılık senaryosu çarpanı. P50 = 1.00, tipik P90 ≈ 0.92.
    p_factor: float = 1.00

    # Depolama (opsiyonel)
    storage_power_mw: float = 0.0
    storage_energy_mwh: float = 0.0
    #: Yılda tam eşdeğer çevrim sayısı.
    storage_cycles_per_year: float = 300.0
    #: Gidiş-dönüş verimi (kesir).
    storage_round_trip_efficiency: float = 0.85
    #: Arbitraj için gün içi ortalama fiyat farkı (para birimi/MWh).
    storage_spread: float = 0.0

    # ------------------------------------------------------------------
    # Gelir: elektrik fiyatı
    # ------------------------------------------------------------------
    price_mode: PriceMode = PriceMode.FLAT
    #: FLAT modunda başlangıç fiyatı (para birimi/MWh).
    flat_price: float = 45.0
    #: Fiyatın yıllık nominal artışı (kesir). FLAT modunda ve eğri bittikten
    #: sonraki yıllarda uygulanır.
    price_escalation: float = 0.02
    #: CURVE modunda yıl -> fiyat eşlemesi. Eğrinin bittiği yıldan sonrası
    #: `price_escalation` ile uzatılır.
    price_curve: Mapping[int, float] = field(default_factory=dict)
    #: Piyasa fiyatına uygulanan yakalama oranı (capture rate). Güneş
    #: üretimi baz yük fiyatının altında yakaladığı için < 1 olabilir.
    capture_rate: float = 1.0

    # İhale / PPA (belirli bir süre boyunca sabit fiyat)
    auction_active: bool = False
    auction_price: float = 65.0
    auction_years: int = 15
    #: İhale fiyatı da endekseniyorsa yıllık artış (kesir).
    auction_escalation: float = 0.0

    # Agri-PV yan geliri
    agri_active: bool = False
    agri_revenue_per_ha: float = 0.0
    land_area_ha: float = 0.0

    # ------------------------------------------------------------------
    # CAPEX
    # ------------------------------------------------------------------
    capex_mode: CapexMode = CapexMode.UNIT
    #: TOTAL modunda toplam yatırım tutarı.
    capex_total: float = 35_000_000.0
    # UNIT modunda MWp başına birim maliyetler:
    capex_solar_per_mwp: float = 400_000.0
    capex_land_per_mwp: float = 35_000.0
    capex_grid_per_mwp: float = 50_000.0
    capex_other_per_mwp: float = 20_000.0
    #: Depolama için MW başına maliyet.
    capex_storage_per_mw: float = 150_000.0
    #: Beklenmeyen giderler payı (kesir), yukarıdaki toplamın üzerine eklenir.
    contingency: float = 0.05
    #: Amortismana tabi olmayan CAPEX payı (arazi). TOTAL modunda kesir
    #: olarak verilir; UNIT modunda arazi kaleminden hesaplanır.
    non_depreciable_share: float = 0.0

    # ------------------------------------------------------------------
    # Bakım yatırımı (invertör yenileme)
    # ------------------------------------------------------------------
    #: İnvertörlerin yenileneceği işletme yılları (1 = ilk üretim yılı).
    #: Panellerin ömrü 30+ yılken invertörlerin ömrü tipik olarak 12–15
    #: yıldır, dolayısıyla bu gerçek bir santralde kaçınılmaz bir kalemdir.
    #: İşletme ömrünü aşan yıllar sessizce yok sayılır, böylece kısa ömürlü
    #: projelerde varsayılan ayar sorun çıkarmaz.
    inverter_replacement_years: list[int] = field(default_factory=lambda: [13])
    #: MWp başına yenileme maliyeti, **baz yıl** parasıyla. Gerçekleşme
    #: yılına enflasyonla taşınır.
    inverter_cost_per_mwp: float = 60_000.0
    #: Yenileme yatırımının amortisman süresi (yıl). Kalan işletme ömrü
    #: daha kısaysa o kullanılır.
    inverter_depreciation_years: int = 10
    #: Bakım rezerv hesabı (MRA): yenileme maliyetinin kaç yıl önceden
    #: biriktirileceği. 0 = rezerv yok, harcama yılında nakitten çıkar.
    #:
    #: Sıfır bırakmak DSCR'ı yenileme yılında çökertir; gerçek kredi
    #: sözleşmeleri tam da bu yüzden rezerv hesabı şart koşar. Para önceden
    #: ayrıldığı için nakit çıkışı öne çekilir ve düzleşir; toplam tutar
    #: değişmez, yalnızca zamanlaması değişir.
    maintenance_reserve_years: int = 5

    # ------------------------------------------------------------------
    # OPEX
    # ------------------------------------------------------------------
    opex_mode: OpexMode = OpexMode.DETAIL
    #: TOTAL modunda ilk yıl toplam işletme gideri.
    opex_total: float = 600_000.0
    # DETAIL modu kalemleri:
    opex_fixed: float = 100_000.0
    opex_per_mwp: float = 6_000.0
    opex_per_mwh: float = 1.5
    #: OPEX kalemlerinin yıllık artışı (kesir). Boş bırakılırsa enflasyon.
    opex_escalation: float | None = None

    # Arazi kirası
    rent_base: float = 50_000.0
    rent_mode: RentMode = RentMode.SIMPLE
    #: Ciro payı (kesir), MAX_BASE_OR_ROYALTY modunda kullanılır.
    rent_royalty: float = 0.03
    #: Kiranın yıllık artışı (kesir). Boş bırakılırsa enflasyon.
    rent_escalation: float | None = None

    # ------------------------------------------------------------------
    # Finansman
    # ------------------------------------------------------------------
    use_debt: bool = True
    #: Kredi tutarının belirlenme yöntemi.
    debt_sizing: DebtSizing = DebtSizing.GEARING
    #: GEARING modunda kredinin toplam CAPEX'e oranı (kesir).
    #: SCULPTED_DSCR modunda yalnızca çözümün başlangıç tahmini olarak kullanılır.
    debt_ratio: float = 0.70
    #: SCULPTED_DSCR modunda tutturulmaya çalışılan DSCR (ör. 1,30 = 1,30x).
    target_dscr: float = 1.30
    #: Sculpting sonucu ne kadar yüksek çıkarsa çıksın aşılmayacak kaldıraç tavanı.
    max_gearing: float = 0.85
    interest_rate: float = 0.075
    loan_tenor: int = 15
    #: Kredi kullandırım komisyonu (kesir), çekilen tutar üzerinden.
    upfront_fee: float = 0.01
    #: İnşaat dönemi faizi (IDC) anaparaya eklensin mi? Hayırsa özkaynak öder.
    capitalize_idc: bool = True

    # ------------------------------------------------------------------
    # Vergi
    # ------------------------------------------------------------------
    tax_rate: float = 0.25
    depreciation_years: int = 15
    #: Zarar mahsubu (geçmiş yıl zararlarının kârdan düşülmesi) açık mı?
    loss_carryforward: bool = True
    #: Zararın taşınabileceği azami yıl. 0 = sınırsız.
    loss_carryforward_limit: int = 0

    # ------------------------------------------------------------------
    # Ömür sonu
    # ------------------------------------------------------------------
    #: Son işletme yılında MWp başına söküm/rehabilitasyon maliyeti.
    decommissioning_per_mwp: float = 0.0
    #: Son işletme yılında elde edilen hurda/ikinci el değeri.
    salvage_value: float = 0.0

    # ------------------------------------------------------------------
    # Makro
    # ------------------------------------------------------------------
    inflation: float = 0.02
    #: Özkaynak maliyeti (Ke). FCFE iskonto edildiği için bu WACC DEĞİLDİR.
    cost_of_equity: float = 0.09
    #: LCOE hesabında kullanılan iskonto oranı (genelde WACC).
    lcoe_discount_rate: float = 0.06
    #: Şebekenin ortalama emisyon faktörü (ton CO2 / MWh). İtalya için
    #: ~0,25-0,35 aralığındadır; ülkeye göre mutlaka güncellenmeli.
    grid_emission_factor: float = 0.30

    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Fiziksel/finansal olarak imkânsız girdileri erkenden yakalar."""
        errors: list[str] = []

        if self.capacity_mwp <= 0:
            errors.append("Kurulu güç sıfırdan büyük olmalı.")
        if self.specific_yield < 0:
            errors.append("Spesifik verim negatif olamaz.")
        if not 0.0 <= self.degradation < 1.0:
            errors.append("Degradasyon 0 ile 1 arasında olmalı.")
        if not 0.0 < self.availability <= 1.0:
            errors.append("Kullanılabilirlik 0 ile 1 arasında olmalı.")
        if self.p_factor <= 0:
            errors.append("P-faktörü sıfırdan büyük olmalı.")
        if self.operating_life < 1:
            errors.append("İşletme ömrü en az 1 yıl olmalı.")
        if self.construction_years < 1:
            errors.append("İnşaat süresi en az 1 yıl olmalı.")
        if self.capex_schedule and len(self.capex_schedule) != self.construction_years:
            errors.append(
                "CAPEX dağılımı inşaat süresiyle aynı uzunlukta olmalı "
                f"({len(self.capex_schedule)} ağırlık / {self.construction_years} yıl)."
            )
        if self.capex_schedule and sum(self.capex_schedule) <= 0:
            errors.append("CAPEX dağılım ağırlıklarının toplamı pozitif olmalı.")
        if not 0.0 <= self.debt_ratio <= 1.0:
            errors.append("Kredi oranı 0 ile 1 arasında olmalı.")
        if not 0.0 <= self.max_gearing <= 1.0:
            errors.append("Kaldıraç tavanı 0 ile 1 arasında olmalı.")
        if self.debt_sizing is DebtSizing.SCULPTED_DSCR and self.target_dscr <= 0:
            errors.append("Hedef DSCR sıfırdan büyük olmalı.")
        if self.debt_sizing is DebtSizing.SCULPTED_DSCR and self.target_dscr < 1.0:
            errors.append(
                "Hedef DSCR 1,00'in altında olamaz — bu, nakit akışının borç "
                "servisini karşılamadığı anlamına gelir."
            )
        if self.use_debt and self.loan_tenor < 1:
            errors.append("Kredi vadesi en az 1 yıl olmalı.")
        if self.use_debt and self.loan_tenor > self.operating_life:
            errors.append("Kredi vadesi işletme ömrünü aşamaz.")
        if self.interest_rate < 0:
            errors.append("Faiz oranı negatif olamaz.")
        if not 0.0 <= self.tax_rate < 1.0:
            errors.append("Vergi oranı 0 ile 1 arasında olmalı.")
        if self.depreciation_years < 1:
            errors.append("Amortisman süresi en az 1 yıl olmalı.")
        if self.cost_of_equity <= -1.0 or self.lcoe_discount_rate <= -1.0:
            errors.append("İskonto oranları -100%'den büyük olmalı.")
        if not 0.0 <= self.non_depreciable_share < 1.0:
            errors.append("Amortismana tabi olmayan pay 0 ile 1 arasında olmalı.")
        if self.price_mode is PriceMode.CURVE and not self.price_curve:
            errors.append("CURVE modunda bir fiyat eğrisi verilmeli.")
        if self.agri_active and self.land_area_ha <= 0:
            errors.append("Agri-PV geliri için arazi alanı sıfırdan büyük olmalı.")
        if self.auction_active and self.auction_years < 1:
            errors.append("İhale süresi en az 1 yıl olmalı.")
        if self.storage_power_mw > 0 and self.storage_energy_mwh <= 0:
            errors.append("Depolama gücü verildiyse enerji kapasitesi de verilmeli.")
        if self.inverter_cost_per_mwp < 0:
            errors.append("İnvertör yenileme maliyeti negatif olamaz.")
        if self.inverter_depreciation_years < 1:
            errors.append("İnvertör amortisman süresi en az 1 yıl olmalı.")
        if any(year < 1 for year in self.inverter_replacement_years):
            errors.append("İnvertör yenileme yılları 1'den küçük olamaz (1 = ilk üretim yılı).")
        if len(set(self.inverter_replacement_years)) != len(self.inverter_replacement_years):
            errors.append("İnvertör yenileme yılları tekrar edemez.")

        if errors:
            raise ValueError("Geçersiz girdi:\n- " + "\n- ".join(errors))

    # ------------------------------------------------------------------
    @property
    def effective_opex_escalation(self) -> float:
        return self.inflation if self.opex_escalation is None else self.opex_escalation

    @property
    def effective_rent_escalation(self) -> float:
        return self.inflation if self.rent_escalation is None else self.rent_escalation

    @property
    def cod_year(self) -> int:
        """İlk üretim yılının takvim yılı."""
        return self.base_year + self.construction_years

    @property
    def effective_inverter_years(self) -> list[int]:
        """İşletme ömrü içine düşen yenileme yılları (0 tabanlı, sıralı).

        Ömrü aşan girişler yok sayılır: 30 yıllık bir proje için tanımlanan
        varsayılan ayarın 5 yıllık bir testte hata vermesi istenmez.
        """
        if self.inverter_cost_per_mwp <= 0:
            return []
        return sorted(
            year - 1 for year in self.inverter_replacement_years if 1 <= year <= self.operating_life
        )

    def to_dict(self) -> dict:
        """Rapor/CSV çıktısı için düz sözlük."""
        raw = asdict(self)
        return {k: (v.value if isinstance(v, Enum) else v) for k, v in raw.items()}

    def replace(self, **changes) -> ProjectInputs:
        """Girdilerin değiştirilmiş bir kopyasını döner (duyarlılık analizi için)."""
        data = {**asdict(self), **changes}
        # asdict Enum'ları bozmaz ama iç içe dict/list'leri kopyalar; sorun yok.
        return ProjectInputs(**data)
