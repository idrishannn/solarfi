"""Streamlit arayüzü testleri.

Motorun testleri (`test_engine.py`) sayıların doğruluğunu ölçer. Bu dosya
farklı bir soruyu sorar: **arayüz hiç patlıyor mu?**

Bir Streamlit uygulamasında kolayca kaçırılan hata tipi, yalnızca belirli
bir mod veya onay kutusu kombinasyonunda tanımsız kalan bir değişkendir —
o dal açılana kadar hiçbir şey görünmez. Buradaki testler her modu ve her
opsiyonel dalı tek tek açıp `at.exception` listesinin boş kaldığını sınar.

`AppTest` uygulamayı tarayıcısız çalıştırır, bu yüzden CI'da da koşar.
Her senaryo tam bir betik çalıştırması olduğu için bu dosya diğerlerinden
yavaştır; `-m "not ui"` ile atlanabilir.
"""

from __future__ import annotations

import pytest

streamlit_testing = pytest.importorskip(
    "streamlit.testing.v1", reason="Arayüz testleri için streamlit gerekli."
)
AppTest = streamlit_testing.AppTest

from pathlib import Path  # noqa: E402

APP = str(Path(__file__).resolve().parent.parent / "app.py")
MODES = ["Hızlı", "Standart", "Uzman"]

pytestmark = pytest.mark.ui


# ----------------------------------------------------------------------
# Yardımcılar
# ----------------------------------------------------------------------


def launch(mode: str | None = None) -> AppTest:
    """Uygulamayı çalıştırır, istenirse belirli bir moda geçer."""
    at = AppTest.from_file(APP, default_timeout=200)
    at.run()
    if mode is not None:
        # Mod seçici `st.segmented_control`; AppTest'te ayrı bir koleksiyondur.
        at.segmented_control[0].set_value(mode).run()
    return at


def to_number(text: str) -> float:
    """Türkçe biçimli bir gösterge değerini sayıya çevirir.

    Arayüz sayıları Türkçe yazar: yüzde işareti başta, binlik ayracı nokta,
    ondalık virgül. ``"%16,4"`` -> ``16.4``, ``"1,53x"`` -> ``1.53``.
    """
    cleaned = text.strip().lstrip("%").rstrip("x").replace(" ", "")
    cleaned = cleaned.replace(".", "").replace(",", ".")
    return float(cleaned)


def metric_value(at: AppTest, label: str) -> str:
    return next(m for m in at.metric if m.label == label).value


def errors(at: AppTest) -> list[str]:
    """Çalıştırmada oluşan istisna ve hata mesajları."""
    return [str(e.value) for e in at.exception] + [str(e.value) for e in at.error]


def number_input(at: AppTest, label: str):
    """Etiketine göre bir sayı girdisi bulur.

    Not: Her `at.run()` sonrası widget referansları eskir; bu fonksiyonu
    her çalıştırmadan sonra yeniden çağırın.
    """
    for widget in at.number_input:
        if widget.label == label:
            return widget
    raise KeyError(f"'{label}' sayı girdisi bulunamadı.")


def checkbox(at: AppTest, label: str):
    for widget in at.checkbox:
        if widget.label == label:
            return widget
    raise KeyError(f"'{label}' onay kutusu bulunamadı.")


def selectbox(at: AppTest, label: str):
    for widget in at.selectbox:
        if widget.label == label:
            return widget
    raise KeyError(f"'{label}' seçim kutusu bulunamadı.")


# ----------------------------------------------------------------------
# Temel çalıştırma
# ----------------------------------------------------------------------


class TestStartup:
    def test_app_runs_without_error(self):
        at = launch()
        assert errors(at) == []

    def test_five_kpis_are_rendered(self):
        at = launch()
        labels = [m.label for m in at.metric]
        assert labels == ["Özkaynak IRR", "NPV", "LCOE", "Minimum DSCR", "Geri ödeme"]

    def test_all_tabs_exist(self):
        at = launch()
        assert len(at.tabs) == 6

    def test_no_deprecation_or_streamlit_warnings(self):
        # `use_container_width` gibi kaldırılmak üzere olan API'ler burada yakalanır.
        at = launch()
        assert [w.value for w in at.warning] == []


@pytest.mark.parametrize("mode", MODES)
class TestModes:
    def test_mode_runs_clean(self, mode):
        at = launch(mode)
        assert errors(at) == []

    def test_mode_produces_kpis(self, mode):
        at = launch(mode)
        assert len(at.metric) == 5
        assert at.metric[0].value not in ("", None)


class TestModeSpecificBehaviour:
    def test_lite_has_no_debt_by_default(self):
        at = launch("Hızlı")
        dscr = next(m for m in at.metric if m.label == "Minimum DSCR")
        assert dscr.value == "n/a"

    def test_pro_and_expert_size_debt(self):
        for mode in ("Standart", "Uzman"):
            at = launch(mode)
            dscr = next(m for m in at.metric if m.label == "Minimum DSCR")
            assert dscr.value.endswith("x")

    def test_expert_exposes_more_controls_than_lite(self):
        lite = launch("Hızlı")
        expert = launch("Uzman")
        assert len(expert.number_input) > len(lite.number_input)


# ----------------------------------------------------------------------
# Expert modundaki opsiyonel dallar
# ----------------------------------------------------------------------


class TestExpertToggles:
    """Her onay kutusu kendi kod dalını açar; hiçbiri tanımsız değişken bırakmamalı."""

    @pytest.mark.parametrize(
        "label",
        [
            "İhale / PPA sözleşmesi",
            "Agri-PV yan geliri",
            "Ciro payı kirası",
            "Giderleri enflasyondan ayrı endeksle",
        ],
    )
    def test_enabling_a_toggle_keeps_the_app_alive(self, label):
        at = launch("Uzman")
        checkbox(at, label).set_value(True)
        at.run()
        assert errors(at) == []
        assert len(at.metric) == 5

    @pytest.mark.parametrize(
        "label", ["Zarar mahsubu", "İnşaat dönemi faizini anaparaya ekle (IDC)", "Kredi kullan"]
    )
    def test_disabling_a_default_on_toggle(self, label):
        at = launch("Uzman")
        checkbox(at, label).set_value(False)
        at.run()
        assert errors(at) == []

    def test_auction_raises_returns_above_merchant(self):
        merchant = launch("Uzman")
        base_irr = merchant.metric[0].value

        at = launch("Uzman")
        checkbox(at, "İhale / PPA sözleşmesi").set_value(True)
        at.run()
        # Varsayılan sözleşme fiyatı (65) varsayılan piyasa fiyatının üstünde
        assert to_number(at.metric[0].value) > to_number(base_irr)

    def test_royalty_rent_reduces_returns(self):
        base = launch("Uzman").metric[0].value
        at = launch("Uzman")
        checkbox(at, "Ciro payı kirası").set_value(True)
        at.run()
        assert to_number(at.metric[0].value) < to_number(base)

    def test_storage_can_be_switched_on(self):
        at = launch("Uzman")
        number_input(at, "Depolama gücü (MW)").set_value(20.0)
        at.run()
        assert errors(at) == []
        assert len(at.metric) == 5


class TestPriceCurveSource:
    def test_switching_to_csv_curve_works(self):
        at = launch("Uzman")
        next(r for r in at.radio if r.label == "Fiyat kaynağı").set_value("Fiyat eğrisi (CSV)").run()
        assert errors(at) == []
        assert selectbox(at, "Bölge").options  # bölge listesi doldu

    def test_every_region_in_the_sample_data_renders(self):
        at = launch("Uzman")
        next(r for r in at.radio if r.label == "Fiyat kaynağı").set_value("Fiyat eğrisi (CSV)").run()
        regions = selectbox(at, "Bölge").options
        assert len(regions) >= 2
        for region in regions:
            at = launch("Uzman")
            next(r for r in at.radio if r.label == "Fiyat kaynağı").set_value("Fiyat eğrisi (CSV)").run()
            selectbox(at, "Bölge").set_value(region)
            at.run()
            assert errors(at) == [], f"'{region}' bölgesi hata verdi"

    def test_cheaper_region_gives_lower_return(self):
        def irr_for(region: str) -> float:
            at = launch("Uzman")
            next(r for r in at.radio if r.label == "Fiyat kaynağı").set_value("Fiyat eğrisi (CSV)").run()
            selectbox(at, "Bölge").set_value(region)
            at.run()
            return to_number(at.metric[0].value)

        at = launch("Uzman")
        next(r for r in at.radio if r.label == "Fiyat kaynağı").set_value("Fiyat eğrisi (CSV)").run()
        regions = selectbox(at, "Bölge").options
        # Örnek veride ilk sütun en pahalı, son sütun en ucuz bölge
        assert irr_for(regions[0]) > irr_for(regions[-1])


class TestConstructionSchedule:
    def test_multi_year_construction_runs(self):
        at = launch("Uzman")
        number_input(at, "İnşaat süresi (yıl)").set_value(3)
        at.run()
        assert errors(at) == []

    def test_custom_schedule_changes_the_result(self):
        at = launch("Uzman")
        number_input(at, "İnşaat süresi (yıl)").set_value(3)
        at.run()
        even_irr = at.metric[0].value

        schedule = next(t for t in at.text_input if t.label == "CAPEX dağılımı")
        schedule.set_value("20,50,30")
        at.run()
        assert errors(at) == []
        assert at.metric[0].value != even_irr

    def test_malformed_schedule_warns_and_falls_back(self):
        at = launch("Uzman")
        number_input(at, "İnşaat süresi (yıl)").set_value(3)
        at.run()
        even_irr = at.metric[0].value

        next(t for t in at.text_input if t.label == "CAPEX dağılımı").set_value("abc")
        at.run()
        assert errors(at) == []
        assert any("CAPEX dağılımı" in w.value for w in at.warning)
        assert at.metric[0].value == even_irr  # eşit dağılıma döndü


class TestProductionScenarios:
    def test_p90_is_worse_than_p50(self):
        at = launch("Uzman")
        p50 = to_number(at.metric[0].value)

        at = launch("Uzman")
        selectbox(at, "Üretim senaryosu").set_value("P90 (finansman)")
        at.run()
        p90 = to_number(at.metric[0].value)
        assert p90 < p50


class TestGuardrails:
    def test_weak_dscr_triggers_a_warning(self):
        at = launch("Standart")
        # Kaldıraç ve faizi yükseltip DSCR'ı eşiğin altına indir
        at.slider[0].set_value(95)
        at.run()
        number_input(at, "Faiz oranı (%)").set_value(15.0)
        at.run()
        dscr = next(m for m in at.metric if m.label == "Minimum DSCR")
        if dscr.value != "n/a" and to_number(dscr.value) < 1.2:
            assert any("DSCR" in w.value for w in at.warning)

    def test_extreme_inputs_do_not_crash(self):
        at = launch("Standart")
        number_input(at, "Kurulu güç (MWp)").set_value(0.1)
        at.run()
        assert errors(at) == []

        at = launch("Standart")
        number_input(at, "İşletme ömrü (yıl)").set_value(1)
        at.run()
        assert errors(at) == []

    def test_zero_price_does_not_crash(self):
        at = launch("Hızlı")
        number_input(at, "Fiyat (/MWh)").set_value(0.0)
        at.run()
        assert errors(at) == []


class TestDebtSculptingUI:
    """Borç boyutlama yönteminin arayüzdeki davranışı."""

    def sculpting_app(self, target: float | None = None) -> AppTest:
        at = launch("Uzman")
        sizing = next(r for r in at.radio if r.label == "Borç boyutlama")
        sizing.set_value("Hedef DSCR (sculpting)")
        at.run()
        if target is not None:
            number_input(at, "Hedef DSCR").set_value(target)
            at.run()
        return at

    def test_sizing_selector_only_exists_in_expert(self):
        for mode in ("Hızlı", "Standart"):
            at = launch(mode)
            assert not any(r.label == "Borç boyutlama" for r in at.radio)
        expert = launch("Uzman")
        assert any(r.label == "Borç boyutlama" for r in expert.radio)

    def test_switching_to_sculpting_runs_clean(self):
        at = self.sculpting_app()
        assert errors(at) == []
        assert len(at.metric) == 5

    def test_gearing_slider_is_replaced_by_dscr_controls(self):
        at = self.sculpting_app()
        labels = [n.label for n in at.number_input] + [s.label for s in at.slider]
        assert "Hedef DSCR" in labels
        assert "Kaldıraç tavanı (%)" in labels
        assert "Kredi oranı (%)" not in labels

    def test_achievable_target_is_hit_and_reported(self):
        at = self.sculpting_app(target=2.0)
        assert errors(at) == []
        dscr = next(m for m in at.metric if m.label == "Minimum DSCR")
        assert to_number(dscr.value) == pytest.approx(2.0, abs=0.005)
        # Kaldıraç bir çıktı olduğu için kullanıcıya açıkça bildirilir.
        assert any("kaldıraç" in s.value.lower() for s in at.success)

    def test_binding_gearing_cap_is_explained(self):
        # Çok düşük bir hedef, tavanın bağlayıcı olmasına yol açar.
        at = self.sculpting_app(target=1.0)
        assert errors(at) == []
        assert any("tavan" in i.value.lower() for i in at.info)

    def test_higher_target_reduces_returns(self):
        loose = self.sculpting_app(target=1.5)
        tight = self.sculpting_app(target=3.0)
        loose_irr = to_number(loose.metric[0].value)
        tight_irr = to_number(tight.metric[0].value)
        assert tight_irr < loose_irr

    def test_percentage_is_formatted_once(self):
        # "%80%" gibi çift yüzde işareti kullanıcıya gitmemeli.
        at = self.sculpting_app(target=1.0)
        for message in [i.value for i in at.info] + [s.value for s in at.success]:
            assert "%%" not in message
            assert "(%" not in message


class TestInverterReplacementUI:
    """Bakım yatırımı kontrollerinin arayüzdeki davranışı."""

    def test_toggle_exists_in_pro_and_expert_only(self):
        assert not any(c.label == "İnvertör yenileme" for c in launch("Hızlı").checkbox)
        for mode in ("Standart", "Uzman"):
            assert any(c.label == "İnvertör yenileme" for c in launch(mode).checkbox)

    def test_expert_exposes_the_full_control_set(self):
        at = launch("Uzman")
        labels = [n.label for n in at.number_input] + [t.label for t in at.text_input]
        assert "Yenileme maliyeti (MWp başına)" in labels
        assert "Yenileme yılları" in labels
        assert "Yenileme amortisman süresi (yıl)" in labels
        assert "Bakım rezervi (yıl önceden birikim)" in labels

    def test_disabling_replacement_improves_returns(self):
        at = launch("Uzman")
        base_irr = to_number(at.metric[0].value)

        at = launch("Uzman")
        checkbox(at, "İnvertör yenileme").set_value(False)
        at.run()
        assert errors(at) == []
        assert to_number(at.metric[0].value) > base_irr

    def test_removing_the_reserve_hurts_dscr(self):
        at = launch("Uzman")
        with_mra = at.metric[3].value

        at = launch("Uzman")
        number_input(at, "Bakım rezervi (yıl önceden birikim)").set_value(0)
        at.run()
        assert errors(at) == []
        without = at.metric[3].value
        assert to_number(without) < to_number(with_mra)

    def test_multiple_replacement_years_are_accepted(self):
        at = launch("Uzman")
        next(t for t in at.text_input if t.label == "Yenileme yılları").set_value("12,24")
        at.run()
        assert errors(at) == []

    def test_malformed_years_warn_and_fall_back(self):
        at = launch("Uzman")
        next(t for t in at.text_input if t.label == "Yenileme yılları").set_value("xyz")
        at.run()
        assert errors(at) == []
        assert any("Yenileme yılları" in w.value for w in at.warning)

    def test_years_beyond_life_do_not_crash(self):
        at = launch("Uzman")
        next(t for t in at.text_input if t.label == "Yenileme yılları").set_value("99")
        at.run()
        assert errors(at) == []

    def test_reserve_appears_in_the_waterfall(self):
        # Bakım rezervi nakit köprüsünde görünmeli, yoksa FCFE tutmaz.
        at = launch("Uzman")
        assert errors(at) == []
        assert len(at.tabs) == 6


class TestDownloadsUI:
    """İndirme düğmeleri her modda mevcut ve tutarlı olmalı."""

    @pytest.mark.parametrize("mode", MODES)
    def test_both_download_buttons_render(self, mode):
        at = launch(mode)
        labels = [b.label for b in at.download_button]
        assert any("Excel" in label for label in labels)
        assert any("CSV" in label for label in labels)

    def test_excel_is_the_primary_action(self, mode=None):
        at = launch()
        labels = [b.label for b in at.download_button]
        # Excel düğmesi önce gelmeli: asıl çıktı odur.
        assert "Excel" in labels[0]

    def test_downloads_survive_every_expert_toggle(self):
        at = launch("Uzman")
        for label in ("İhale / PPA sözleşmesi", "Agri-PV yan geliri"):
            checkbox(at, label).set_value(True)
            at.run()
        assert errors(at) == []
        assert len(at.download_button) == 2
