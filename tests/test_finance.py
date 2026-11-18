"""`solarfi.finance` testleri.

Bu fonksiyonlar elle hesaplanabilir olduğu için testler bilinen kapalı
form sonuçlarla karşılaştırma yapar; motorun geri kalanına bağımlı değildir.
"""

import pytest

from solarfi.finance import discount_factors, irr, npv, payback_period, pmt


class TestNpv:
    def test_zero_rate_is_plain_sum(self):
        assert npv(0.0, [-100, 50, 50, 50]) == pytest.approx(50)

    def test_first_element_is_not_discounted(self):
        # t=0 iskonto edilmez: 100 + 100/1.1 = 190.909...
        assert npv(0.10, [100, 100]) == pytest.approx(190.909090, rel=1e-6)

    def test_empty(self):
        assert npv(0.08, []) == 0.0

    def test_matches_manual_calculation(self):
        rate = 0.07
        flows = [-1000, 400, 400, 400]
        expected = -1000 + 400 / 1.07 + 400 / 1.07**2 + 400 / 1.07**3
        assert npv(rate, flows) == pytest.approx(expected)


class TestDiscountFactors:
    def test_first_factor_is_one(self):
        assert discount_factors(0.08, 5)[0] == 1.0

    def test_rejects_impossible_rate(self):
        with pytest.raises(ValueError):
            discount_factors(-1.5, 3)


class TestPmt:
    def test_zero_rate_is_straight_line(self):
        assert pmt(0.0, 10, 1000) == pytest.approx(100.0)

    def test_known_annuity(self):
        # 100.000 anapara, %5, 10 yıl -> 12.950,46 (standart anüite tablosu)
        assert pmt(0.05, 10, 100_000) == pytest.approx(12_950.457, rel=1e-5)

    def test_amortisation_fully_repays_principal(self):
        principal, rate, n = 250_000.0, 0.065, 12
        payment = pmt(rate, n, principal)
        balance = principal
        for _ in range(n):
            balance = balance * (1 + rate) - payment
        assert balance == pytest.approx(0.0, abs=1e-6)


class TestIrr:
    def test_simple_doubling(self):
        # -100 bugün, 200 bir yıl sonra -> %100
        assert irr([-100, 200]) == pytest.approx(1.0, abs=1e-6)

    def test_npv_at_irr_is_zero(self):
        flows = [-5000, 1200, 1300, 1400, 1500, 1600]
        rate = irr(flows)
        assert rate is not None
        assert npv(rate, flows) == pytest.approx(0.0, abs=1e-6)

    def test_returns_none_when_all_positive(self):
        assert irr([100, 200, 300]) is None

    def test_returns_none_when_all_negative(self):
        assert irr([-100, -200]) is None

    def test_returns_none_for_too_short_series(self):
        assert irr([-100]) is None

    def test_negative_irr_is_found(self):
        flows = [-1000, 300, 300, 300]  # toplam 900 < 1000, IRR negatif
        rate = irr(flows)
        assert rate is not None
        assert rate < 0
        assert npv(rate, flows) == pytest.approx(0.0, abs=1e-6)


class TestPaybackPeriod:
    def test_exact_breakeven(self):
        # -100, +50, +50 -> 2. yılın sonunda tam başabaş
        assert payback_period([-100, 50, 50]) == pytest.approx(2.0)

    def test_interpolates_within_year(self):
        # -100, +50, +100 -> 1. yıl sonunda -50, sonra 50/100 = 0.5 yıl
        assert payback_period([-100, 50, 100]) == pytest.approx(1.5)

    def test_returns_none_when_never_recovered(self):
        assert payback_period([-100, 10, 10]) is None

    def test_discounted_is_longer_than_simple(self):
        flows = [-1000, 300, 300, 300, 300, 300]
        simple = payback_period(flows)
        discounted = payback_period(flows, discount_rate=0.10)
        assert simple is not None and discounted is not None
        assert discounted > simple
