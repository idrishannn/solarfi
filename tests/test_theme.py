"""`solarfi.theme` testleri.

Görsel tercihler test edilemez ama **biçimlendirme** edilebilir, ve
biçimlendirme hataları sessizdir: `%1638,0` diye yazan bir yüzde ya da
`2.026` diye biçimlenen bir yıl, kimse fark etmeden rapora girer.
"""

import pytest

from solarfi import theme


class TestTurkishNumbers:
    """Binlik ayracı nokta, ondalık virgül."""

    @pytest.mark.parametrize(
        ("value", "decimals", "expected"),
        [
            (1234567.8, 1, "1.234.567,8"),
            (1000.0, 0, "1.000"),
            (0.5, 2, "0,50"),
            (-2500.25, 2, "-2.500,25"),
            (0.0, 0, "0"),
        ],
    )
    def test_number_formatting(self, value, decimals, expected):
        assert theme.tr_number(value, decimals) == expected

    def test_separators_do_not_swap_incorrectly(self):
        # En kolay yapılan hata: iki ayracı sırayla değiştirip birbirine karıştırmak.
        assert theme.tr_number(1234.56, 2) == "1.234,56"


class TestPercent:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0.1638, "%16,4"), (0.09, "%9,0"), (0.0, "%0,0"), (1.0, "%100,0")],
    )
    def test_fraction_becomes_percent(self, value, expected):
        assert theme.percent(value) == expected

    def test_does_not_double_scale(self):
        # 0,15 -> %15,0 olmalı; %1500,0 değil.
        assert theme.percent(0.15) == "%15,0"


class TestMoney:
    def test_symbol_lookup(self):
        assert theme.currency_symbol("EUR") == "€"
        assert theme.currency_symbol("TRY") == "₺"

    def test_unknown_currency_falls_back_to_code(self):
        assert theme.currency_symbol("XYZ") == "XYZ"

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (8_430_000, "8,4 M€"),
            (940_000, "940 B€"),
            (2_100_000_000, "2,10 Mr€"),
            (250, "250 €"),
        ],
    )
    def test_compact_money(self, value, expected):
        assert theme.compact_money(value, "EUR") == expected

    def test_negative_amounts_keep_their_sign(self):
        assert theme.compact_money(-3_200_000, "EUR").startswith("-")


class TestMultipleAndYears:
    def test_multiple(self):
        assert theme.multiple(1.53) == "1,53x"

    def test_years(self):
        assert theme.years(7.1) == "7,1 yıl"


class TestPalette:
    def test_no_terracotta_default(self):
        # Krem + terrakota kombinasyonu bilinçli olarak elendi.
        assert theme.ACCENT.upper() != "#D97757"
        assert theme.ACCENT.upper() != "#C2410C"

    def test_every_colour_is_a_hex_triplet(self):
        for name in ("INK", "MUTED", "ACCENT", "POSITIVE", "NEGATIVE", "NEUTRAL", "GRID"):
            value = getattr(theme, name)
            assert value.startswith("#") and len(value) == 7

    def test_positive_and_negative_are_distinguishable(self):
        assert theme.POSITIVE != theme.NEGATIVE


class TestCss:
    def test_loads_a_tabular_numeric_face(self):
        # Nakit akışı tablosunda sütunların hizalanması buna bağlı.
        assert "IBM+Plex+Mono" in theme.CSS
        assert "tabular-nums" in theme.CSS

    def test_has_no_unresolved_placeholders(self):
        # f-string ile üretiliyor; kaçmış bir `{` kalırsa CSS bozulur.
        assert "{INK}" not in theme.CSS
        assert "{ACCENT}" not in theme.CSS

    def test_is_wrapped_in_a_style_tag(self):
        assert theme.CSS.strip().startswith("<style>") or "<style>" in theme.CSS
        assert "</style>" in theme.CSS


class TestDscrBand:
    """Arayüzün imza öğesi."""

    def test_marker_moves_with_the_value(self):
        low = theme.dscr_band(0.5)
        high = theme.dscr_band(1.8)
        assert "sfi-marker" in low and "sfi-marker" in high
        assert low != high

    def test_marker_is_clamped_at_both_ends(self):
        for value in (-5.0, 0.0, 99.0):
            markup = theme.dscr_band(value)
            position = float(markup.split("calc(")[1].split("%")[0])
            assert 0.0 <= position <= 100.0

    def test_thresholds_appear_on_the_scale(self):
        markup = theme.dscr_band(1.4)
        assert "1,20x" in markup
        assert "1,35x" in markup

    def test_no_debt_renders_a_placeholder(self):
        assert "kredi yok" in theme.dscr_band(None)
        assert "sfi-marker" not in theme.dscr_band(None)

    def test_thresholds_match_the_documented_values(self):
        assert theme.DSCR_FLOOR == 1.20
        assert theme.DSCR_COMFORT == 1.35
