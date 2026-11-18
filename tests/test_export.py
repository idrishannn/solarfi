"""`solarfi.export` testleri.

İki katman var:

1. **Yapı testleri** — sekmeler, satırlar, biçimler yerinde mi. Hızlı,
   her ortamda çalışır.
2. **Formül doğrulama** — LibreOffice ile dosyayı yeniden hesaplayıp
   Excel'in ürettiği sayıların motorunkilerle aynı olduğunu sınar. Bu
   testler `soffice` yoksa atlanır.

İkinci katman kritik: openpyxl formülleri düz metin olarak yazar, hiçbir
şey doğruluğunu kontrol etmez. Bir sütun kayması ya da yanlış satıra
referans, tamamen hatasız görünen ama yanlış sayılar üreten bir dosya
verir. Tek koruma, dosyayı gerçekten hesaplatıp karşılaştırmaktır.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from openpyxl import load_workbook

from solarfi import ProjectInputs, run_model
from solarfi.export import (
    build_cashflow_workbook,
    build_workbook,
    cashflow_bytes,
    workbook_bytes,
)
from solarfi.inputs import DebtSizing

RECALC_SCRIPT = Path("/mnt/skills/public/xlsx/scripts/recalc.py")


@pytest.fixture(scope="module")
def result():
    return run_model(ProjectInputs())


@pytest.fixture(scope="module")
def workbook(result):
    return build_workbook(result)


def _labels(sheet, max_row: int = 80) -> dict[str, int]:
    """Etiket -> satır numarası eşlemesi."""
    found = {}
    for row in sheet.iter_rows(min_row=1, max_row=max_row, max_col=1):
        if isinstance(row[0].value, str):
            found.setdefault(row[0].value, row[0].row)
    return found


def _pairs(sheet, max_row: int = 60) -> dict[str, object]:
    """A sütunu etiket, B sütunu değer olan sayfalar için eşleme."""
    found = {}
    for row in sheet.iter_rows(min_row=1, max_row=max_row, max_col=2):
        if isinstance(row[0].value, str) and row[1].value is not None:
            found.setdefault(row[0].value, row[1].value)
    return found


# ----------------------------------------------------------------------
# Yapı
# ----------------------------------------------------------------------


class TestStructure:
    def test_four_sheets_in_order(self, workbook):
        assert workbook.sheetnames == ["Özet", "Nakit Akışı", "Grafikler", "Varsayımlar"]

    def test_opens_on_the_summary(self, workbook):
        assert workbook.active.title == "Özet"

    def test_bytes_are_a_valid_workbook(self, result):
        data = workbook_bytes(result)
        assert data[:2] == b"PK"  # xlsx bir zip arşividir
        reloaded = load_workbook(io.BytesIO(data))
        assert reloaded.sheetnames == ["Özet", "Nakit Akışı", "Grafikler", "Varsayımlar"]

    def test_cashflow_has_one_column_per_period(self, workbook, result):
        sheet = workbook["Nakit Akışı"]
        labels = _labels(sheet)
        row = labels["Üretim"]
        values = [
            sheet.cell(row=row, column=3 + i).value for i in range(len(result.periods))
        ]
        assert len(values) == len(result.periods)
        assert sheet.cell(row=row, column=3 + len(result.periods)).value is None

    def test_years_are_written_as_text(self, workbook, result):
        # 2026 sayı olarak yazılırsa Excel bunu 2.026 diye biçimler.
        sheet = workbook["Nakit Akışı"]
        header_row = _labels(sheet)["Kalem"]
        first_year = sheet.cell(row=header_row, column=3).value
        assert first_year == str(result.calendar_years[0])
        assert isinstance(first_year, str)

    def test_freeze_panes_is_set(self, workbook):
        assert workbook["Nakit Akışı"].freeze_panes is not None

    def test_uses_a_professional_font(self, workbook):
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)["EBITDA"]
        assert sheet.cell(row=row, column=3).font.name == "Arial"

    def test_negative_numbers_use_parentheses(self, workbook):
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)["Arazi kirası"]
        assert "(" in sheet.cell(row=row, column=3).number_format

    def test_dscr_uses_multiple_format(self, workbook):
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)["DSCR"]
        assert "x" in sheet.cell(row=row, column=3).number_format

    def test_percentages_are_stored_as_fractions(self, workbook, result):
        summary = workbook["Özet"]
        values = _pairs(summary)
        ke = values["Özkaynak maliyeti (Ke)"]
        assert ke == pytest.approx(result.inputs.cost_of_equity)
        assert ke < 1.0  # 0,09 olmalı, 9 değil


class TestFormulasArePresent:
    """Türetilmiş satırlar sabit sayı değil, formül olmalı."""

    @pytest.mark.parametrize(
        "label",
        [
            "Elektrik geliri",
            "Toplam gelir",
            "Toplam OPEX",
            "EBITDA",
            "Vergi öncesi kâr",
            "CFADS",
            "DSCR",
            "FCFE (özkaynağa serbest nakit)",
            "Kümülatif FCFE",
        ],
    )
    def test_derived_row_is_a_formula(self, workbook, label):
        sheet = workbook["Nakit Akışı"]
        value = sheet.cell(row=_labels(sheet)[label], column=3).value
        assert isinstance(value, str) and value.startswith("=")

    @pytest.mark.parametrize("label", ["Özkaynak IRR", "NPV", "LCOE", "Minimum DSCR"])
    def test_headline_metric_is_a_formula(self, workbook, label):
        value = _pairs(workbook["Özet"])[label]
        assert isinstance(value, str) and value.startswith("=")

    def test_formula_is_identical_across_periods(self, workbook, result):
        # Satır ortasında elle düzeltilmiş tek bir hücre, finansal modellerde
        # en sık görülen sessiz hatadır. Formül şablonu her yerde aynı olmalı.
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)["EBITDA"]
        shapes = set()
        for i in range(len(result.periods)):
            formula = sheet.cell(row=row, column=3 + i).value
            # Sütun harflerini soyutla, yapı kalsın
            shapes.add("".join(ch for ch in formula if not ch.isalpha()))
        assert len(shapes) == 1

    def test_discount_rates_are_referenced_not_hardcoded(self, workbook):
        # NPV formülü oranı bir hücreden almalı ki kullanıcı değiştirebilsin.
        npv_formula = _pairs(workbook["Özet"])["NPV"]
        assert "$B$" in npv_formula

    def test_npv_formula_handles_the_t0_convention(self, workbook):
        # Excel'in NPV'si ilk elemanı iskonto eder; bizim t=0 etmez.
        # Bu yüzden ilk hücre ayrı eklenmeli.
        npv_formula = _pairs(workbook["Özet"])["NPV"]
        assert npv_formula.count("NPV(") == 1
        assert "+NPV(" in npv_formula

    def test_sheet_name_with_space_is_quoted(self, workbook):
        # 'Nakit Akışı' tırnaksız yazılırsa #VALUE! verir.
        formula = _pairs(workbook["Özet"])["Özkaynak IRR"]
        assert "'Nakit Akışı'!" in formula

    def test_dscr_guards_division_by_zero(self, workbook):
        sheet = workbook["Nakit Akışı"]
        formula = sheet.cell(row=_labels(sheet)["DSCR"], column=3).value
        assert formula.startswith("=IF(")


class TestAssumptionsSheet:
    def test_records_key_inputs(self, workbook):
        values = _pairs(workbook["Varsayımlar"], max_row=120)
        assert "Kurulu güç" in values
        assert "Faiz oranı" in values
        assert "Özkaynak maliyeti (Ke)" in values

    def test_capacity_matches_the_model(self, workbook, result):
        values = _pairs(workbook["Varsayımlar"], max_row=120)
        assert values["Kurulu güç"] == pytest.approx(result.inputs.capacity_mwp)

    def test_key_assumptions_are_highlighted(self, workbook):
        sheet = workbook["Varsayımlar"]
        for row in sheet.iter_rows(min_row=1, max_row=120, max_col=2):
            if row[0].value == "Kurulu güç":
                assert row[1].fill.fgColor.rgb.endswith("FFFF00")
                return
        pytest.fail("'Kurulu güç' satırı bulunamadı")

    def test_inputs_are_blue(self, workbook):
        sheet = workbook["Varsayımlar"]
        for row in sheet.iter_rows(min_row=1, max_row=120, max_col=2):
            if row[0].value == "Faiz oranı":
                assert row[1].font.color.rgb.endswith("0000FF")
                return
        pytest.fail("'Faiz oranı' satırı bulunamadı")

    def test_sculpting_mode_is_recorded(self):
        inputs = ProjectInputs(debt_sizing=DebtSizing.SCULPTED_DSCR, target_dscr=1.45)
        workbook = build_workbook(run_model(inputs))
        values = _pairs(workbook["Varsayımlar"], max_row=120)
        assert "sculpting" in str(values["Borç boyutlama"]).lower()


class TestEdgeCases:
    @pytest.mark.parametrize(
        "inputs",
        [
            ProjectInputs(use_debt=False),
            ProjectInputs(construction_years=3, capex_schedule=[0.3, 0.4, 0.3]),
            ProjectInputs(operating_life=2, loan_tenor=2),
            ProjectInputs(debt_sizing=DebtSizing.SCULPTED_DSCR, target_dscr=1.45),
            ProjectInputs(agri_active=True, agri_revenue_per_ha=2000, land_area_ha=100),
            ProjectInputs(inverter_replacement_years=[]),
            ProjectInputs(project_name="Şirket / Proje: 2026 *test*"),
        ],
    )
    def test_workbook_builds(self, inputs):
        data = workbook_bytes(run_model(inputs))
        assert len(data) > 5_000
        assert load_workbook(io.BytesIO(data)).sheetnames[0] == "Özet"

    def test_no_debt_leaves_dscr_blank(self):
        workbook = build_workbook(run_model(ProjectInputs(use_debt=False)))
        sheet = workbook["Nakit Akışı"]
        formula = sheet.cell(row=_labels(sheet)["DSCR"], column=3).value
        assert formula.startswith("=IF(")  # sıfıra bölme koruması devrede


# ----------------------------------------------------------------------
# Formül doğrulama (LibreOffice gerekir)
# ----------------------------------------------------------------------

recalc_available = pytest.mark.skipif(
    not RECALC_SCRIPT.exists() or shutil.which("soffice") is None,
    reason="LibreOffice veya recalc betiği yok; formül doğrulaması atlandı.",
)


def _recalculate(path: Path) -> dict:
    completed = subprocess.run(
        ["python3", str(RECALC_SCRIPT), str(path), "90"],
        capture_output=True,
        text=True,
        timeout=180,
    )
    return json.loads(completed.stdout)


@recalc_available
class TestComputedValues:
    """Excel'in hesapladığı sayılar motorunkilerle aynı mı?

    Bu, çıktının tek gerçek güvencesi: formüller sözdizimsel olarak
    doğru olabilir ama yanlış hücreye referans veriyorsa hiçbir hata
    çıkmadan yanlış sayı üretirler.
    """

    @pytest.fixture(scope="module")
    def computed(self, tmp_path_factory):
        model = run_model(ProjectInputs())
        path = tmp_path_factory.mktemp("xlsx") / "rapor.xlsx"
        build_workbook(model).save(path)
        report = _recalculate(path)
        assert report.get("status") == "success", report
        assert report.get("total_errors", 0) == 0, report
        return model, load_workbook(path, data_only=True), report

    def test_no_formula_errors(self, computed):
        _, _, report = computed
        assert report["total_errors"] == 0
        assert report["total_formulas"] > 100

    @pytest.mark.parametrize(
        ("label", "attribute", "tolerance"),
        [
            ("Özkaynak IRR", "equity_irr", 1e-9),
            ("NPV", "project_npv", 1.0),
            ("LCOE", "lcoe", 1e-4),
            ("Minimum DSCR", "min_dscr", 1e-9),
            ("Ortalama DSCR", "avg_dscr", 1e-9),
        ],
    )
    def test_metric_matches_the_engine(self, computed, label, attribute, tolerance):
        model, workbook, _ = computed
        excel_value = _pairs(workbook["Özet"])[label]
        assert excel_value == pytest.approx(getattr(model, attribute), abs=tolerance)

    @pytest.mark.parametrize(
        ("label", "attribute"),
        [
            ("Toplam gelir", "revenue_total"),
            ("EBITDA", "ebitda"),
            ("CFADS", "cfads"),
            ("FCFE (özkaynağa serbest nakit)", "fcfe"),
        ],
    )
    def test_series_matches_the_engine(self, computed, label, attribute):
        model, workbook, _ = computed
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)[label]
        engine_series = getattr(model, attribute)
        for i, expected in enumerate(engine_series):
            actual = sheet.cell(row=row, column=3 + i).value or 0.0
            assert actual == pytest.approx(expected, abs=0.01), f"{label} dönem {i}"

    def test_cumulative_row_sums_the_cashflow(self, computed):
        model, workbook, _ = computed
        sheet = workbook["Nakit Akışı"]
        row = _labels(sheet)["Kümülatif FCFE"]
        last = sheet.cell(row=row, column=3 + len(model.fcfe) - 1).value
        assert last == pytest.approx(sum(model.fcfe), abs=0.01)

    def test_changing_the_discount_rate_recalculates_npv(self, tmp_path):
        """Dosyanın canlı olduğunun kanıtı."""
        model = run_model(ProjectInputs(cost_of_equity=0.09))
        path = tmp_path / "canli.xlsx"
        build_workbook(model).save(path)

        workbook = load_workbook(path)
        summary = workbook["Özet"]
        for row in summary.iter_rows(min_row=1, max_row=20, max_col=2):
            if row[0].value == "Özkaynak maliyeti (Ke)":
                row[1].value = 0.14
        workbook.save(path)

        report = _recalculate(path)
        assert report.get("status") == "success", report

        recomputed = _pairs(load_workbook(path, data_only=True)["Özet"])["NPV"]
        expected = run_model(ProjectInputs(cost_of_equity=0.14)).project_npv
        assert recomputed == pytest.approx(expected, abs=1.0)

    def test_lcoe_is_independent_of_the_equity_discount_rate(self, tmp_path):
        model = run_model(ProjectInputs())
        path = tmp_path / "lcoe.xlsx"
        build_workbook(model).save(path)
        before = _recalculate(path)
        assert before.get("status") == "success"
        lcoe_before = _pairs(load_workbook(path, data_only=True)["Özet"])["LCOE"]

        workbook = load_workbook(path)
        for row in workbook["Özet"].iter_rows(min_row=1, max_row=20, max_col=2):
            if row[0].value == "Özkaynak maliyeti (Ke)":
                row[1].value = 0.20
        workbook.save(path)
        _recalculate(path)
        lcoe_after = _pairs(load_workbook(path, data_only=True)["Özet"])["LCOE"]

        assert lcoe_after == pytest.approx(lcoe_before, rel=1e-9)

    @pytest.mark.parametrize(
        "inputs",
        [
            ProjectInputs(use_debt=False),
            ProjectInputs(debt_sizing=DebtSizing.SCULPTED_DSCR, target_dscr=1.45),
            ProjectInputs(construction_years=3, capex_schedule=[0.3, 0.4, 0.3]),
        ],
    )
    def test_variants_recalculate_without_errors(self, inputs, tmp_path):
        path = tmp_path / "variant.xlsx"
        build_workbook(run_model(inputs)).save(path)
        report = _recalculate(path)
        assert report.get("status") == "success", report
        assert report.get("total_errors", 0) == 0, report


class TestCharts:
    """Grafikler sekmesi: resim değil, veriye bağlı yerel Excel grafikleri."""

    def test_chart_sheet_exists(self, workbook):
        assert "Grafikler" in workbook.sheetnames

    def test_several_charts_are_added(self, workbook):
        assert len(workbook["Grafikler"]._charts) >= 3

    def test_charts_reference_the_cashflow_sheet(self, workbook):
        # Grafik bir görüntü değil, canlı seri referansı olmalı: hücre
        # değişince grafik de değişsin.
        chart = workbook["Grafikler"]._charts[0]
        for series in chart.series:
            assert "Nakit Akışı" in series.val.numRef.f

    def test_stacked_chart_has_all_four_components(self, workbook):
        bar = next(
            chart
            for chart in workbook["Grafikler"]._charts
            if type(chart).__name__ == "BarChart" and len(chart.series) >= 4
        )
        assert len(bar.series) >= 4

    def test_charts_survive_a_recalculation(self, tmp_path):
        if not RECALC_SCRIPT.exists() or shutil.which("soffice") is None:
            pytest.skip("LibreOffice yok.")
        path = tmp_path / "grafikli.xlsx"
        build_workbook(run_model(ProjectInputs())).save(path)
        report = _recalculate(path)
        assert report.get("status") == "success", report
        assert len(load_workbook(path)["Grafikler"]._charts) >= 3


class TestCashflowOnlyWorkbook:
    """Kendi modeline yapıştırmak isteyen için sade tek sayfalık kitap."""

    def test_single_sheet(self, result):
        workbook = build_cashflow_workbook(result)
        assert workbook.sheetnames == ["Nakit Akışı"]

    def test_contains_no_formulas(self, result):
        # Dış referanslı formüller başka bir dosyaya yapıştırıldığında kırılır.
        sheet = build_cashflow_workbook(result)["Nakit Akışı"]
        formulas = [
            cell.value
            for row in sheet.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
        assert formulas == []

    def test_values_match_the_engine(self, result):
        sheet = build_cashflow_workbook(result)["Nakit Akışı"]
        row = _labels(sheet)["FCFE (özkaynağa serbest nakit)"]
        for i, expected in enumerate(result.fcfe):
            assert sheet.cell(row=row, column=3 + i).value == pytest.approx(expected)

    def test_cumulative_is_computed(self, result):
        sheet = build_cashflow_workbook(result)["Nakit Akışı"]
        row = _labels(sheet)["Kümülatif FCFE"]
        last = sheet.cell(row=row, column=3 + len(result.fcfe) - 1).value
        assert last == pytest.approx(sum(result.fcfe))

    def test_dscr_is_blank_outside_the_tenor(self, result):
        sheet = build_cashflow_workbook(result)["Nakit Akışı"]
        row = _labels(sheet)["DSCR"]
        # Motor borçsuz yıllarda None döner; hücre de boş kalmalı.
        for i, ratio in enumerate(result.dscr):
            cell = sheet.cell(row=row, column=3 + i).value
            if ratio is None:
                assert cell is None
            else:
                assert cell == pytest.approx(ratio)

    def test_bytes_are_a_valid_workbook(self, result):
        data = cashflow_bytes(result)
        assert data[:2] == b"PK"
        assert load_workbook(io.BytesIO(data)).sheetnames == ["Nakit Akışı"]
