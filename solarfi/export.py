"""Excel (.xlsx) çıktısı.

Neden CSV değil
---------------
Modelin çıktısı üç ayrı tablodur (varsayımlar, göstergeler, nakit akışı).
Bunları tek bir CSV'ye alt alta yazmak Excel'de okunamaz bir yığın üretir.
Üç sekmeli bir çalışma kitabı hem düzgün görünür hem de finans dünyasının
beklediği formattır.

Tasarım kararı: **canlı formüller**
-----------------------------------
Çıktı, hesaplanmış sayıların ölü bir dökümü değildir. Türetilmiş satırlar
(gelir, EBITDA, CFADS, DSCR, FCFE, kümülatif) ve bütün göstergeler (IRR,
NPV, LCOE, DSCR) **Excel formülü** olarak yazılır. Kullanıcı iskonto
oranını değiştirdiğinde NPV yeniden hesaplanır; bir gider satırını
düzelttiğinde EBITDA ve aşağısı takip eder.

Formüller bütün dönemlerde birebir aynıdır — bir finansal modelde en sık
görülen sessiz hata, satırın ortasında elle düzeltilmiş tek bir hücredir.

Neyin formül olmadığı
---------------------
Sürücü satırlar (üretim, fiyat, OPEX, vergi, borç servisi) değer olarak
yazılır. Sebebi dürüstçe şu: bunlar zarar mahsubu havuzu, ciro payı
kirası ve DSCR'a göre iterasyonla çözülen borç planı gibi mekanizmalardan
geliyor ve bunlar Excel formülüne kapalı formda çevrilemez. Kitap bu
haliyle "hesaplanmış bir senaryonun raporu"dur; motorun Excel'e
yeniden yazılmış hâli değildir. Varsayımlar sekmesi de tam bu yüzden var:
sonucun hangi girdilerden çıktığı okunabilsin diye.

Biçim konvansiyonları (standart finansal model pratiği)
-------------------------------------------------------
* Girdi hücreleri **mavi**, formüller **siyah**, başka sekmeye bağlantılar
  **yeşil**; kilit varsayımlar sarı dolgulu.
* Negatifler parantezli, sıfırlar tire, binlik ayraçlı.
* Yüzdeler kesir olarak saklanır (0,15 → %15,0), katsayılar ``0,00x``.
* Yıllar metin olarak yazılır ki ``2.026`` diye biçimlenmesin.
"""

from __future__ import annotations

import io
from datetime import date

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .engine import ModelResult
from .inputs import DebtSizing, PriceMode

__all__ = ["build_workbook", "workbook_bytes", "build_cashflow_workbook", "cashflow_bytes"]

# ----------------------------------------------------------------------
# Biçim sabitleri
# ----------------------------------------------------------------------

FONT = "Arial"

INPUT_COLOUR = "0000FF"  # mavi: elle girilen değer
FORMULA_COLOUR = "000000"  # siyah: formül
LINK_COLOUR = "008000"  # yeşil: başka sekmeye bağlantı
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
SECTION_FILL = PatternFill("solid", fgColor="D9E2F3")
KEY_FILL = PatternFill("solid", fgColor="FFFF00")

CURRENCY = "#,##0;(#,##0);-"
CURRENCY_2DP = "#,##0.00;(#,##0.00);-"
QUANTITY = "#,##0;(#,##0);-"
PERCENT = "0.0%"
MULTIPLE = '0.00"x"'
YEARS_1DP = '0.0" yıl"'

THIN_BOTTOM = Border(bottom=Side(style="thin", color="8EA9DB"))

#: Nakit akışı tablosunda ilk veri sütunu (A=etiket, B=birim).
FIRST_DATA_COL = 3


def _col(index: int) -> str:
    """0 tabanlı dönem indeksinden Excel sütun harfi."""
    return get_column_letter(FIRST_DATA_COL + index)


# ----------------------------------------------------------------------
# Ortak yazma yardımcıları
# ----------------------------------------------------------------------


def _title(sheet: Worksheet, row: int, text: str, span: int) -> int:
    cell = sheet.cell(row=row, column=1, value=text)
    cell.font = Font(name=FONT, size=14, bold=True, color="FFFFFF")
    cell.fill = HEADER_FILL
    for col in range(2, span + 1):
        sheet.cell(row=row, column=col).fill = HEADER_FILL
    sheet.row_dimensions[row].height = 22
    return row + 1


def _section(sheet: Worksheet, row: int, text: str, span: int) -> int:
    cell = sheet.cell(row=row, column=1, value=text)
    cell.font = Font(name=FONT, size=10, bold=True, color="1F3864")
    cell.fill = SECTION_FILL
    for col in range(2, span + 1):
        sheet.cell(row=row, column=col).fill = SECTION_FILL
    return row + 1


def _write_row(
    sheet: Worksheet,
    row: int,
    label: str,
    values: list,
    *,
    unit: str = "",
    number_format: str = CURRENCY,
    colour: str = FORMULA_COLOUR,
    bold: bool = False,
    indent: int = 0,
    border: bool = False,
) -> int:
    """Bir satır etiket + birim + dönem değerleri yazar."""
    label_cell = sheet.cell(row=row, column=1, value=label)
    label_cell.font = Font(name=FONT, size=10, bold=bold)
    label_cell.alignment = Alignment(indent=indent)

    unit_cell = sheet.cell(row=row, column=2, value=unit)
    unit_cell.font = Font(name=FONT, size=9, italic=True, color="808080")

    for i, value in enumerate(values):
        cell = sheet.cell(row=row, column=FIRST_DATA_COL + i, value=value)
        cell.font = Font(name=FONT, size=10, bold=bold, color=colour)
        cell.number_format = number_format
        if border:
            cell.border = THIN_BOTTOM
    if border:
        label_cell.border = THIN_BOTTOM
        unit_cell.border = THIN_BOTTOM
    return row + 1


# ----------------------------------------------------------------------
# Nakit akışı sekmesi
# ----------------------------------------------------------------------


def _build_cashflow(sheet: Worksheet, result: ModelResult) -> dict[str, int]:
    """Nakit akışı tablosunu yazar ve satır numaralarını döner."""
    inp = result.inputs
    periods = len(result.periods)
    span = FIRST_DATA_COL + periods - 1
    cur = inp.currency
    rows: dict[str, int] = {}

    row = _title(sheet, 1, f"{inp.project_name} — Nakit Akışı", span)
    note = sheet.cell(
        row=row,
        column=1,
        value=f"Bütün tutarlar nominal {cur}. Giderler negatiftir. "
        f"Siyah rakamlar formüldür ve girdiler değişince yeniden hesaplanır.",
    )
    note.font = Font(name=FONT, size=9, italic=True, color="808080")
    row += 2

    # --- Başlık satırları ---
    header = sheet.cell(row=row, column=1, value="Kalem")
    header.font = Font(name=FONT, size=10, bold=True)
    sheet.cell(row=row, column=2, value="Birim").font = Font(name=FONT, size=10, bold=True)
    for i, year in enumerate(result.calendar_years):
        cell = sheet.cell(row=row, column=FIRST_DATA_COL + i, value=str(year))
        cell.font = Font(name=FONT, size=10, bold=True)
        cell.alignment = Alignment(horizontal="right")
        cell.border = THIN_BOTTOM
    header.border = THIN_BOTTOM
    sheet.cell(row=row, column=2).border = THIN_BOTTOM
    rows["header"] = row
    row += 1

    row = _write_row(
        sheet, row, "Dönem", list(result.periods), unit="t", number_format="0", colour="808080"
    )
    row = _write_row(
        sheet,
        row,
        "Faz",
        ["İşletme" if op else "İnşaat" for op in result.is_operating],
        number_format="General",
        colour="808080",
    )
    row += 1

    # --- Üretim ve fiyat ---
    row = _section(sheet, row, "ÜRETİM VE FİYAT", span)
    rows["generation"] = row
    row = _write_row(
        sheet, row, "Üretim", result.generation_mwh, unit="MWh", number_format=QUANTITY,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["price"] = row
    row = _write_row(
        sheet, row, "Gerçekleşen fiyat", result.price, unit=f"{cur}/MWh",
        number_format=CURRENCY_2DP, colour=INPUT_COLOUR, indent=1,
    )
    row += 1

    # --- Gelir ---
    row = _section(sheet, row, "GELİR", span)
    gen_row, price_row = rows["generation"], rows["price"]
    row = _write_row(
        sheet, row, "Elektrik geliri", 
        [f"={_col(i)}{gen_row}*{_col(i)}{price_row}" for i in range(periods)],
        unit=cur, indent=1,
    )
    rows["rev_el"] = row - 1
    rows["rev_st"] = row
    row = _write_row(
        sheet, row, "Depolama geliri", result.revenue_storage, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["rev_ag"] = row
    row = _write_row(
        sheet, row, "Tarım geliri", result.revenue_agri, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["rev_total"] = row
    row = _write_row(
        sheet, row, "Toplam gelir",
        [
            f"=SUM({_col(i)}{rows['rev_el']}:{_col(i)}{rows['rev_ag']})"
            for i in range(periods)
        ],
        unit=cur, bold=True, border=True,
    )
    row += 1

    # --- Giderler ---
    row = _section(sheet, row, "İŞLETME GİDERLERİ", span)
    rows["opex_ex_rent"] = row
    row = _write_row(
        sheet, row, "İşletme ve bakım",
        [o - r for o, r in zip(result.opex, result.rent, strict=True)],
        unit=cur, colour=INPUT_COLOUR, indent=1,
    )
    rows["rent"] = row
    row = _write_row(
        sheet, row, "Arazi kirası", result.rent, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    rows["opex_total"] = row
    row = _write_row(
        sheet, row, "Toplam OPEX",
        [
            f"=SUM({_col(i)}{rows['opex_ex_rent']}:{_col(i)}{rows['rent']})"
            for i in range(periods)
        ],
        unit=cur, indent=1,
    )
    rows["ebitda"] = row
    row = _write_row(
        sheet, row, "EBITDA",
        [f"={_col(i)}{rows['rev_total']}+{_col(i)}{rows['opex_total']}" for i in range(periods)],
        unit=cur, bold=True, border=True,
    )
    row += 1

    # --- Vergi ---
    row = _section(sheet, row, "VERGİ", span)
    rows["depr"] = row
    row = _write_row(
        sheet, row, "Amortisman", result.depreciation, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    rows["interest"] = row
    row = _write_row(
        sheet, row, "Faiz gideri", result.interest, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    rows["ebt"] = row
    row = _write_row(
        sheet, row, "Vergi öncesi kâr",
        [
            f"={_col(i)}{rows['ebitda']}-{_col(i)}{rows['depr']}+{_col(i)}{rows['interest']}"
            for i in range(periods)
        ],
        unit=cur, indent=1,
    )
    rows["tax"] = row
    row = _write_row(
        sheet, row, "Vergi", result.tax, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    row += 1

    # --- Borç ve nakit ---
    row = _section(sheet, row, "NAKİT AKIŞI VE BORÇ SERVİSİ", span)
    rows["reserve"] = row
    row = _write_row(
        sheet, row, "Bakım rezervine katkı", result.maintenance_reserve, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["cfads"] = row
    row = _write_row(
        sheet, row, "CFADS",
        [
            f"={_col(i)}{rows['ebitda']}+{_col(i)}{rows['tax']}+{_col(i)}{rows['reserve']}"
            for i in range(periods)
        ],
        unit=cur, bold=True,
    )
    rows["debt_service"] = row
    row = _write_row(
        sheet, row, "Borç servisi", result.debt_service, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    rows["dscr"] = row
    row = _write_row(
        sheet, row, "DSCR",
        [
            f'=IF({_col(i)}{rows["debt_service"]}=0,"",'
            f'{_col(i)}{rows["cfads"]}/-{_col(i)}{rows["debt_service"]})'
            for i in range(periods)
        ],
        unit="x", number_format=MULTIPLE, bold=True,
    )
    rows["debt_balance"] = row
    row = _write_row(
        sheet, row, "Kredi bakiyesi (dönem sonu)", result.debt_balance, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    row += 1

    # --- Yatırım ve özkaynak ---
    row = _section(sheet, row, "YATIRIM VE ÖZKAYNAK", span)
    rows["capex"] = row
    row = _write_row(
        sheet, row, "Yatırım harcaması", result.capex, unit=cur, colour=INPUT_COLOUR, indent=1
    )
    rows["maintenance"] = row
    row = _write_row(
        sheet, row, "Bakım yatırımı (rezervden karşılanır)", result.maintenance_capex,
        unit=cur, colour="808080", indent=1,
    )
    rows["equity"] = row
    row = _write_row(
        sheet, row, "Özkaynak katkısı", result.equity_injection, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["terminal"] = row
    row = _write_row(
        sheet, row, "Ömür sonu", result.terminal_cashflow, unit=cur,
        colour=INPUT_COLOUR, indent=1,
    )
    rows["fcfe"] = row
    row = _write_row(
        sheet, row, "FCFE (özkaynağa serbest nakit)",
        [
            f"={_col(i)}{rows['equity']}+{_col(i)}{rows['cfads']}"
            f"+{_col(i)}{rows['debt_service']}+{_col(i)}{rows['terminal']}"
            for i in range(periods)
        ],
        unit=cur, bold=True, border=True,
    )
    rows["cumulative"] = row
    cumulative = [f"={_col(0)}{rows['fcfe']}"]
    cumulative += [
        f"={_col(i - 1)}{rows['cumulative']}+{_col(i)}{rows['fcfe']}" for i in range(1, periods)
    ]
    row = _write_row(sheet, row, "Kümülatif FCFE", cumulative, unit=cur)
    row += 1

    # --- LCOE yardımcı satırı ---
    row = _section(sheet, row, "LCOE YARDIMCI HESABI", span)
    rows["lcoe_cost"] = row
    row = _write_row(
        sheet, row, "İskonto edilecek maliyet akışı",
        [
            f"=-{_col(i)}{rows['capex']}-{_col(i)}{rows['opex_total']}"
            f"-{_col(i)}{rows['maintenance']}+MAX(0,-{_col(i)}{rows['terminal']})"
            for i in range(periods)
        ],
        unit=cur, colour="808080", indent=1,
    )
    hint = sheet.cell(
        row=row,
        column=1,
        value="Finansman kalemleri (faiz, komisyon, vergi) LCOE'ye girmez; "
        "sermaye maliyeti iskonto oranının içindedir.",
    )
    hint.font = Font(name=FONT, size=9, italic=True, color="808080")

    # --- Sütun genişlikleri ve dondurma ---
    sheet.column_dimensions["A"] = sheet.column_dimensions["A"]
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 10
    for i in range(periods):
        sheet.column_dimensions[_col(i)].width = 13
    sheet.freeze_panes = sheet.cell(row=rows["header"] + 1, column=FIRST_DATA_COL)
    sheet.sheet_view.showGridLines = False
    return rows


# ----------------------------------------------------------------------
# Özet sekmesi
# ----------------------------------------------------------------------


def _build_summary(sheet: Worksheet, result: ModelResult, rows: dict[str, int]) -> None:
    """Göstergeleri nakit akışına bağlı formüllerle yazar."""
    inp = result.inputs
    cur = inp.currency
    periods = len(result.periods)
    cf = "'Nakit Akışı'"
    first, last = _col(0), _col(periods - 1)
    second = _col(1)

    def series(row_key: str) -> str:
        return f"{cf}!{first}{rows[row_key]}:{last}{rows[row_key]}"

    def tail(row_key: str) -> str:
        """t=1'den sona kadar (Excel NPV ilk elemanı iskonto ettiği için)."""
        return f"{cf}!{second}{rows[row_key]}:{last}{rows[row_key]}"

    def first_cell(row_key: str) -> str:
        return f"{cf}!{first}{rows[row_key]}"

    row = _title(sheet, 1, f"{inp.project_name} — Özet", 4)
    sheet.cell(
        row=row, column=1,
        value=f"Oluşturulma: {date.today().isoformat()} · Para birimi: {cur} (nominal)",
    ).font = Font(name=FONT, size=9, italic=True, color="808080")
    row += 2

    # --- Değiştirilebilir oranlar ---
    row = _section(sheet, row, "İSKONTO ORANLARI (değiştirilebilir)", 4)
    ke_row = row
    sheet.cell(row=row, column=1, value="Özkaynak maliyeti (Ke)").font = Font(name=FONT, size=10)
    ke = sheet.cell(row=row, column=2, value=inp.cost_of_equity)
    ke.font = Font(name=FONT, size=10, bold=True, color=INPUT_COLOUR)
    ke.number_format = PERCENT
    ke.fill = KEY_FILL
    sheet.cell(
        row=row, column=3, value="FCFE bu oranla iskonto edilir (WACC değil)."
    ).font = Font(name=FONT, size=9, italic=True, color="808080")
    row += 1

    wacc_row = row
    sheet.cell(row=row, column=1, value="LCOE iskonto oranı (WACC)").font = Font(name=FONT, size=10)
    wacc = sheet.cell(row=row, column=2, value=inp.lcoe_discount_rate)
    wacc.font = Font(name=FONT, size=10, bold=True, color=INPUT_COLOUR)
    wacc.number_format = PERCENT
    wacc.fill = KEY_FILL
    sheet.cell(
        row=row, column=3, value="Bu iki hücreyi değiştirin, aşağısı yeniden hesaplanır."
    ).font = Font(name=FONT, size=9, italic=True, color="808080")
    row += 2

    # --- Getiri göstergeleri ---
    row = _section(sheet, row, "GETİRİ", 4)
    metrics: list[tuple[str, str, str, str]] = [
        (
            "Özkaynak IRR",
            f'=IFERROR(IRR({series("fcfe")}),"n/a")',
            PERCENT,
            "FCFE serisini sıfırlayan iskonto oranı",
        ),
        (
            "NPV",
            f"={first_cell('fcfe')}+NPV($B${ke_row},{tail('fcfe')})",
            CURRENCY,
            "t=0 iskonto edilmez; Excel NPV'si ilk elemanı iskonto ettiği için ayrı eklenir",
        ),
        (
            "LCOE",
            f"=({first_cell('lcoe_cost')}+NPV($B${wacc_row},{tail('lcoe_cost')}))"
            f"/({first_cell('generation')}+NPV($B${wacc_row},{tail('generation')}))",
            CURRENCY_2DP,
            f"{cur}/MWh · iskonto edilmiş maliyet / iskonto edilmiş üretim",
        ),
    ]
    for label, formula, fmt, note in metrics:
        sheet.cell(row=row, column=1, value=label).font = Font(name=FONT, size=10, bold=True)
        cell = sheet.cell(row=row, column=2, value=formula)
        cell.font = Font(name=FONT, size=11, bold=True, color=LINK_COLOUR)
        cell.number_format = fmt
        sheet.cell(row=row, column=3, value=note).font = Font(
            name=FONT, size=9, italic=True, color="808080"
        )
        row += 1
    row += 1

    # --- Kredi göstergeleri ---
    row = _section(sheet, row, "KREDİ", 4)
    debt_metrics = [
        ("Minimum DSCR", f'=IFERROR(MIN({series("dscr")}),"n/a")', MULTIPLE, ""),
        ("Ortalama DSCR", f'=IFERROR(AVERAGE({series("dscr")}),"n/a")', MULTIPLE, ""),
        (
            "COD'da kredi bakiyesi",
            result.debt_at_cod,
            CURRENCY,
            "DSCR'a göre çözüldü"
            if inp.debt_sizing is DebtSizing.SCULPTED_DSCR
            else "Sabit kaldıraçla belirlendi",
        ),
        (
            "Efektif kaldıraç",
            result.effective_gearing,
            PERCENT,
            "Sculpting modunda bu bir ÇIKTIDIR"
            if inp.debt_sizing is DebtSizing.SCULPTED_DSCR
            else "Girdi olarak verildi",
        ),
        ("Toplam özkaynak", result.total_equity, CURRENCY, ""),
    ]
    for label, value, fmt, note in debt_metrics:
        sheet.cell(row=row, column=1, value=label).font = Font(name=FONT, size=10, bold=True)
        cell = sheet.cell(row=row, column=2, value=value)
        is_formula = isinstance(value, str)
        cell.font = Font(
            name=FONT, size=11, bold=True,
            color=LINK_COLOUR if is_formula else FORMULA_COLOUR,
        )
        cell.number_format = fmt
        if note:
            sheet.cell(row=row, column=3, value=note).font = Font(
                name=FONT, size=9, italic=True, color="808080"
            )
        row += 1
    row += 1

    # --- Yatırım ve üretim ---
    row = _section(sheet, row, "YATIRIM VE ÜRETİM", 4)
    others: list[tuple[str, object, str]] = [
        ("Toplam CAPEX", result.total_capex, CURRENCY),
        ("Bakım yatırımı (ömür boyu)", result.total_maintenance_capex, CURRENCY),
        ("Kapitalize inşaat faizi (IDC)", result.capitalised_idc, CURRENCY),
        ("Finansman komisyonu", result.financing_fees, CURRENCY),
        (
            "Geri ödeme süresi",
            result.payback_years if result.payback_years is not None else "n/a",
            YEARS_1DP,
        ),
        (
            "İskontolu geri ödeme",
            result.discounted_payback_years
            if result.discounted_payback_years is not None
            else "n/a",
            YEARS_1DP,
        ),
        ("Ömür boyu üretim", result.lifetime_generation_mwh, QUANTITY),
        ("Ömür boyu önlenen CO₂", result.lifetime_co2_avoided_t, QUANTITY),
    ]
    for label, value, fmt in others:
        sheet.cell(row=row, column=1, value=label).font = Font(name=FONT, size=10)
        cell = sheet.cell(row=row, column=2, value=value)
        cell.font = Font(name=FONT, size=10)
        cell.number_format = fmt
        row += 1
    row += 1

    # --- Uyarı ---
    warning = sheet.cell(
        row=row,
        column=1,
        value="Bu dosya eğitim ve ön fizibilite amaçlıdır; yatırım tavsiyesi değildir. "
        "Yeşil hücreler Nakit Akışı sekmesine bağlı formüllerdir.",
    )
    warning.font = Font(name=FONT, size=9, italic=True, color="C00000")

    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 18
    sheet.column_dimensions["C"].width = 62
    sheet.sheet_view.showGridLines = False


# ----------------------------------------------------------------------
# Varsayımlar sekmesi
# ----------------------------------------------------------------------


def _build_assumptions(sheet: Worksheet, result: ModelResult) -> None:
    """Bütün girdileri gruplanmış ve okunabilir biçimde yazar."""
    inp = result.inputs
    cur = inp.currency

    key_fields = {
        "capacity_mwp",
        "specific_yield",
        "flat_price",
        "capex_solar_per_mwp",
        "capex_total",
        "interest_rate",
        "debt_ratio",
        "target_dscr",
        "cost_of_equity",
        "lcoe_discount_rate",
    }

    groups: list[tuple[str, list[tuple[str, str, object, str]]]] = [
        (
            "PROJE VE ZAMAN EKSENİ",
            [
                ("project_name", "Proje adı", inp.project_name, ""),
                ("currency", "Para birimi", inp.currency, ""),
                ("base_year", "Baz yıl (t=0)", str(inp.base_year), ""),
                ("construction_years", "İnşaat süresi", inp.construction_years, "yıl"),
                ("cod", "İlk üretim yılı (COD)", str(inp.cod_year), ""),
                ("operating_life", "İşletme ömrü", inp.operating_life, "yıl"),
            ],
        ),
        (
            "TEKNİK",
            [
                ("capacity_mwp", "Kurulu güç", inp.capacity_mwp, "MWp"),
                ("specific_yield", "Spesifik verim", inp.specific_yield, "kWh/kWp"),
                ("degradation", "Yıllık degradasyon", inp.degradation, "%"),
                ("availability", "Kullanılabilirlik", inp.availability, "%"),
                ("p_factor", "Üretim senaryosu çarpanı", inp.p_factor, "x"),
                ("storage_power_mw", "Depolama gücü", inp.storage_power_mw, "MW"),
                ("storage_energy_mwh", "Depolama enerjisi", inp.storage_energy_mwh, "MWh"),
            ],
        ),
        (
            "GELİR",
            [
                (
                    "price_mode",
                    "Fiyat kaynağı",
                    "Fiyat eğrisi (CSV)"
                    if inp.price_mode is PriceMode.CURVE
                    else "Sabit fiyat",
                    "",
                ),
                ("flat_price", "Baz fiyat", inp.flat_price, f"{cur}/MWh"),
                ("price_escalation", "Fiyat eskalasyonu", inp.price_escalation, "%"),
                ("capture_rate", "Yakalama oranı", inp.capture_rate, "%"),
                ("auction_active", "İhale / PPA", "Evet" if inp.auction_active else "Hayır", ""),
                ("auction_price", "Sözleşme fiyatı", inp.auction_price, f"{cur}/MWh"),
                ("auction_years", "Sözleşme süresi", inp.auction_years, "yıl"),
                ("agri_active", "Agri-PV", "Evet" if inp.agri_active else "Hayır", ""),
                ("agri_revenue_per_ha", "Tarım geliri", inp.agri_revenue_per_ha, f"{cur}/ha"),
                ("land_area_ha", "Arazi alanı", inp.land_area_ha, "ha"),
            ],
        ),
        (
            "YATIRIM (CAPEX)",
            [
                ("capex_mode", "CAPEX modu", inp.capex_mode.value, ""),
                ("capex_total", "Toplam yatırım", inp.capex_total, cur),
                ("capex_solar_per_mwp", "Panel + EPC", inp.capex_solar_per_mwp, f"{cur}/MWp"),
                ("capex_land_per_mwp", "Arazi", inp.capex_land_per_mwp, f"{cur}/MWp"),
                ("capex_grid_per_mwp", "Şebeke", inp.capex_grid_per_mwp, f"{cur}/MWp"),
                ("capex_other_per_mwp", "Diğer", inp.capex_other_per_mwp, f"{cur}/MWp"),
                ("contingency", "Beklenmeyen giderler", inp.contingency, "%"),
            ],
        ),
        (
            "BAKIM YATIRIMI",
            [
                (
                    "inverter_replacement_years",
                    "İnvertör yenileme yılları",
                    ", ".join(str(y) for y in inp.inverter_replacement_years) or "yok",
                    "",
                ),
                (
                    "inverter_cost_per_mwp",
                    "Yenileme maliyeti",
                    inp.inverter_cost_per_mwp,
                    f"{cur}/MWp",
                ),
                (
                    "maintenance_reserve_years",
                    "Bakım rezervi",
                    inp.maintenance_reserve_years,
                    "yıl önceden",
                ),
            ],
        ),
        (
            "İŞLETME GİDERLERİ",
            [
                ("opex_mode", "OPEX modu", inp.opex_mode.value, ""),
                ("opex_total", "Toplam OPEX", inp.opex_total, f"{cur}/yıl"),
                ("opex_fixed", "Sabit gider", inp.opex_fixed, f"{cur}/yıl"),
                ("opex_per_mwp", "O&M", inp.opex_per_mwp, f"{cur}/MWp"),
                ("opex_per_mwh", "Değişken gider", inp.opex_per_mwh, f"{cur}/MWh"),
                ("rent_base", "Arazi kirası", inp.rent_base, f"{cur}/yıl"),
                ("rent_mode", "Kira tipi", inp.rent_mode.value, ""),
                ("rent_royalty", "Ciro payı", inp.rent_royalty, "%"),
            ],
        ),
        (
            "FİNANSMAN",
            [
                ("use_debt", "Kredi kullanımı", "Evet" if inp.use_debt else "Hayır", ""),
                (
                    "debt_sizing",
                    "Borç boyutlama",
                    "Hedef DSCR (sculpting)"
                    if inp.debt_sizing is DebtSizing.SCULPTED_DSCR
                    else "Sabit kaldıraç",
                    "",
                ),
                ("debt_ratio", "Kredi oranı (girdi)", inp.debt_ratio, "%"),
                ("target_dscr", "Hedef DSCR", inp.target_dscr, "x"),
                ("max_gearing", "Kaldıraç tavanı", inp.max_gearing, "%"),
                ("interest_rate", "Faiz oranı", inp.interest_rate, "%"),
                ("loan_tenor", "Vade", inp.loan_tenor, "yıl"),
                ("upfront_fee", "Kullandırım komisyonu", inp.upfront_fee, "%"),
                (
                    "capitalize_idc",
                    "IDC kapitalize",
                    "Evet" if inp.capitalize_idc else "Hayır",
                    "",
                ),
            ],
        ),
        (
            "VERGİ VE MAKRO",
            [
                ("tax_rate", "Kurumlar vergisi", inp.tax_rate, "%"),
                ("depreciation_years", "Amortisman süresi", inp.depreciation_years, "yıl"),
                (
                    "loss_carryforward",
                    "Zarar mahsubu",
                    "Evet" if inp.loss_carryforward else "Hayır",
                    "",
                ),
                ("inflation", "Enflasyon", inp.inflation, "%"),
                ("cost_of_equity", "Özkaynak maliyeti (Ke)", inp.cost_of_equity, "%"),
                ("lcoe_discount_rate", "LCOE iskonto oranı", inp.lcoe_discount_rate, "%"),
                (
                    "grid_emission_factor",
                    "Şebeke emisyon faktörü",
                    inp.grid_emission_factor,
                    "tCO₂/MWh",
                ),
            ],
        ),
    ]

    row = _title(sheet, 1, f"{inp.project_name} — Varsayımlar", 4)
    sheet.cell(
        row=row,
        column=1,
        value="Mavi hücreler modele girilen değerlerdir. Sarı dolgu kilit varsayımları "
        "işaretler. Bu sekme raporun izlenebilirliği içindir: sonucun hangi girdilerden "
        "çıktığı buradan okunur.",
    ).font = Font(name=FONT, size=9, italic=True, color="808080")
    row += 2

    for group_name, entries in groups:
        row = _section(sheet, row, group_name, 4)
        for key, label, value, unit in entries:
            sheet.cell(row=row, column=1, value=label).font = Font(name=FONT, size=10)
            cell = sheet.cell(row=row, column=2, value=value)
            cell.font = Font(name=FONT, size=10, bold=True, color=INPUT_COLOUR)
            if unit == "%":
                cell.number_format = PERCENT
            elif unit == "x":
                cell.number_format = MULTIPLE
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                cell.number_format = CURRENCY_2DP if abs(value) < 1000 else CURRENCY
            if key in key_fields:
                cell.fill = KEY_FILL
            sheet.cell(row=row, column=3, value=unit).font = Font(
                name=FONT, size=9, italic=True, color="808080"
            )
            row += 1
        row += 1

    if inp.price_mode is PriceMode.CURVE:
        note = sheet.cell(
            row=row,
            column=1,
            value="Fiyat eğrisi bir CSV dosyasından yüklendi. Depoda gelen örnek eğriler "
            "SENTETİKTİR ve hiçbir gerçek piyasa görüşünü temsil etmez.",
        )
        note.font = Font(name=FONT, size=9, italic=True, color="C00000")

    sheet.column_dimensions["A"].width = 32
    sheet.column_dimensions["B"].width = 20
    sheet.column_dimensions["C"].width = 16
    sheet.sheet_view.showGridLines = False


# ----------------------------------------------------------------------
# Genel giriş noktaları
# ----------------------------------------------------------------------


def _build_charts(sheet: Worksheet, result: ModelResult, rows: dict[str, int]) -> None:
    """Nakit akışı hücrelerine bağlı yerel Excel grafikleri.

    Grafikler resim değil, canlı seri referanslarıdır: nakit akışı
    sekmesindeki bir hücre değişince grafik de değişir.
    """
    inp = result.inputs
    periods = len(result.periods)
    cf_name = "Nakit Akışı"
    first_col, last_col = FIRST_DATA_COL, FIRST_DATA_COL + periods - 1
    cashflow = sheet.parent[cf_name]

    def years() -> Reference:
        return Reference(
            cashflow,
            min_col=first_col,
            max_col=last_col,
            min_row=rows["header"],
            max_row=rows["header"],
        )

    def band(row_key: str) -> Reference:
        """Etiket sütunu dahil bir satır — seri adı oradan okunur."""
        return Reference(
            cashflow,
            min_col=1,
            max_col=last_col,
            min_row=rows[row_key],
            max_row=rows[row_key],
        )

    row = _title(sheet, 1, f"{inp.project_name} — Grafikler", 12)
    sheet.cell(
        row=row,
        column=1,
        value="Grafikler Nakit Akışı sekmesindeki hücrelere bağlıdır; "
        "veri değişince kendiliğinden güncellenir.",
    ).font = Font(name=FONT, size=9, italic=True, color="808080")

    # --- 1. Yıllık nakit akışı bileşenleri ---
    bars = BarChart()
    bars.type = "col"
    bars.grouping = "stacked"
    bars.overlap = 100
    bars.title = "Yıllık nakit akışı bileşenleri"
    bars.y_axis.title = inp.currency
    bars.height, bars.width = 9, 26
    for key in ("rev_total", "opex_total", "tax", "debt_service"):
        bars.add_data(band(key), from_rows=True, titles_from_data=True)
    bars.set_categories(years())
    for series, colour in zip(
        bars.series, ("2E7D32", "EF6C00", "757575", "C62828"), strict=False
    ):
        series.graphicalProperties.solidFill = colour
        series.graphicalProperties.line.noFill = True

    line = LineChart()
    line.add_data(band("fcfe"), from_rows=True, titles_from_data=True)
    line.series[0].graphicalProperties.line.solidFill = "1565C0"
    line.series[0].graphicalProperties.line.width = 26_000
    line.series[0].smooth = False
    bars += line
    sheet.add_chart(bars, "A4")

    # --- 2. Kümülatif nakit akışı ---
    cumulative = LineChart()
    cumulative.title = "Kümülatif FCFE (başabaş noktası)"
    cumulative.y_axis.title = inp.currency
    cumulative.height, cumulative.width = 8, 26
    cumulative.add_data(band("cumulative"), from_rows=True, titles_from_data=True)
    cumulative.set_categories(years())
    cumulative.series[0].graphicalProperties.line.solidFill = "0D47A1"
    cumulative.series[0].graphicalProperties.line.width = 26_000
    cumulative.series[0].smooth = False
    sheet.add_chart(cumulative, "A23")

    # --- 3. DSCR profili (yalnızca kredi varsa anlamlı) ---
    if any(value is not None for value in result.dscr):
        dscr_chart = LineChart()
        dscr_chart.title = "DSCR profili"
        dscr_chart.y_axis.title = "x"
        dscr_chart.height, dscr_chart.width = 8, 26
        dscr_chart.add_data(band("dscr"), from_rows=True, titles_from_data=True)
        dscr_chart.set_categories(years())
        dscr_chart.series[0].graphicalProperties.line.solidFill = "6A1B9A"
        dscr_chart.series[0].graphicalProperties.line.width = 26_000
        dscr_chart.series[0].smooth = False
        sheet.add_chart(dscr_chart, "A40")

    # --- 4. Üretim profili ---
    generation = BarChart()
    generation.type = "col"
    generation.title = "Yıllık üretim (degradasyon dahil)"
    generation.y_axis.title = "MWh"
    generation.height, generation.width = 8, 26
    generation.add_data(band("generation"), from_rows=True, titles_from_data=True)
    generation.set_categories(years())
    generation.series[0].graphicalProperties.solidFill = "F9A825"
    generation.series[0].graphicalProperties.line.noFill = True
    sheet.add_chart(generation, "A57")

    sheet.column_dimensions["A"].width = 18
    sheet.sheet_view.showGridLines = False


def build_workbook(result: ModelResult) -> Workbook:
    """Dört sekmeli tam raporu oluşturur."""
    workbook = Workbook()
    summary = workbook.active
    summary.title = "Özet"
    cashflow = workbook.create_sheet("Nakit Akışı")
    charts = workbook.create_sheet("Grafikler")
    assumptions = workbook.create_sheet("Varsayımlar")

    rows = _build_cashflow(cashflow, result)
    _build_summary(summary, result, rows)
    _build_charts(charts, result, rows)
    _build_assumptions(assumptions, result)

    workbook.active = 0
    return workbook


def workbook_bytes(result: ModelResult) -> bytes:
    """İndirme düğmesine verilebilecek ham bayt dizisi."""
    buffer = io.BytesIO()
    build_workbook(result).save(buffer)
    return buffer.getvalue()


def build_cashflow_workbook(result: ModelResult) -> Workbook:
    """Yalnızca nakit akışını içeren tek sayfalık kitap.

    Tam raporun aksine burada formül yoktur: hücrelerde hesaplanmış
    değerler durur. Amaç, tabloyu başka bir modele ya da sunuma sorunsuz
    yapıştırabilmek — dış referans taşıyan formüller yapıştırıldığında
    kırılır.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Nakit Akışı"

    rows = _build_cashflow(sheet, result)

    # Formülleri hesaplanmış değerlerle değiştir.
    computed = {
        "rev_el": result.revenue_electricity,
        "rev_total": result.revenue_total,
        "opex_total": result.opex,
        "ebitda": result.ebitda,
        "ebt": result.ebt,
        "cfads": result.cfads,
        "fcfe": result.fcfe,
        "lcoe_cost": [
            -c - o - m + max(0.0, -t)
            for c, o, m, t in zip(
                result.capex,
                result.opex,
                result.maintenance_capex,
                result.terminal_cashflow,
                strict=True,
            )
        ],
    }
    for key, series in computed.items():
        for i, value in enumerate(series):
            sheet.cell(row=rows[key], column=FIRST_DATA_COL + i).value = value

    # DSCR borçsuz yıllarda None'dır. `cell(value=None)` atama YAPMAZ, bu
    # yüzden formül yerinde kalırdı — doğrudan `.value` ile yazıyoruz.
    for i, ratio in enumerate(result.dscr):
        sheet.cell(row=rows["dscr"], column=FIRST_DATA_COL + i).value = ratio

    running = 0.0
    for i, value in enumerate(result.fcfe):
        running += value
        sheet.cell(row=rows["cumulative"], column=FIRST_DATA_COL + i).value = running

    return workbook


def cashflow_bytes(result: ModelResult) -> bytes:
    """Tek sayfalık nakit akışı kitabının baytları."""
    buffer = io.BytesIO()
    build_cashflow_workbook(result).save(buffer)
    return buffer.getvalue()
