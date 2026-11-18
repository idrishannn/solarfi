"""`solarfi.engine` testleri.

Yaklaşım: motorun her bloğunu (üretim, fiyat, OPEX, borç, vergi, nakit
akışı) motordan bağımsız olarak elle hesaplayıp karşılaştırmak. Bir
finansal modelde en tehlikeli hata sessiz olanıdır; bu yüzden testler
"çalışıyor mu" değil "doğru sayıyı mı veriyor" sorusunu soruyor.
"""

import pytest

from solarfi import ProjectInputs, run_model
from solarfi.finance import npv, pmt
from solarfi.inputs import CapexMode, DebtSizing, OpexMode, PriceMode, RentMode


def simple_inputs(**overrides) -> ProjectInputs:
    """Elle hesaplanması kolay, sade bir proje."""
    defaults = dict(
        capacity_mwp=10.0,
        specific_yield=1000.0,
        availability=1.0,
        p_factor=1.0,
        degradation=0.0,
        construction_years=1,
        operating_life=5,
        price_mode=PriceMode.FLAT,
        flat_price=50.0,
        price_escalation=0.0,
        capex_mode=CapexMode.TOTAL,
        capex_total=10_000_000.0,
        contingency=0.0,
        opex_mode=OpexMode.TOTAL,
        opex_total=100_000.0,
        opex_escalation=0.0,
        use_debt=False,
        loan_tenor=5,  # operating_life ile uyumlu olmalı (doğrulama şart koşuyor)
        upfront_fee=0.0,
        tax_rate=0.0,
        inflation=0.0,
        cost_of_equity=0.08,
        lcoe_discount_rate=0.08,
        contingency_placeholder=None,
    )
    defaults.pop("contingency_placeholder")
    defaults.update(overrides)
    return ProjectInputs(**defaults)


class TestTimeline:
    def test_period_count_is_construction_plus_life(self):
        r = run_model(simple_inputs(construction_years=2, operating_life=25))
        assert len(r.periods) == 27
        assert sum(r.is_operating) == 25

    def test_no_generation_during_construction(self):
        r = run_model(simple_inputs(construction_years=2))
        assert r.generation_mwh[0] == 0.0
        assert r.generation_mwh[1] == 0.0
        assert r.generation_mwh[2] > 0.0

    def test_calendar_years_start_at_base_year(self):
        r = run_model(simple_inputs(base_year=2030, construction_years=2))
        assert r.calendar_years[0] == 2030
        assert r.calendar_years[2] == 2032  # COD yılı
        assert r.inputs.cod_year == 2032


class TestGeneration:
    def test_generation_formula(self):
        # 10 MWp * 1000 kWh/kWp = 10.000 MWh
        r = run_model(simple_inputs())
        assert r.generation_mwh[1] == pytest.approx(10_000.0)

    def test_zero_degradation_keeps_output_flat(self):
        r = run_model(simple_inputs(degradation=0.0))
        operating = [g for g, op in zip(r.generation_mwh, r.is_operating, strict=True) if op]
        assert all(g == pytest.approx(operating[0]) for g in operating)

    def test_degradation_compounds(self):
        r = run_model(simple_inputs(degradation=0.01))
        first, second = r.generation_mwh[1], r.generation_mwh[2]
        assert second == pytest.approx(first * 0.99)

    def test_availability_and_p_factor_scale_output(self):
        base = run_model(simple_inputs()).generation_mwh[1]
        r = run_model(simple_inputs(availability=0.95, p_factor=0.92))
        assert r.generation_mwh[1] == pytest.approx(base * 0.95 * 0.92)


class TestPricing:
    def test_flat_price_with_escalation(self):
        r = run_model(simple_inputs(flat_price=50.0, price_escalation=0.02, base_year=2026))
        # COD 2027 -> baz yıldan 1 yıl sonra
        assert r.price[1] == pytest.approx(50.0 * 1.02)
        assert r.price[2] == pytest.approx(50.0 * 1.02**2)

    def test_curve_lookup(self):
        curve = {2027: 60.0, 2028: 55.0, 2029: 50.0}
        r = run_model(
            simple_inputs(price_mode=PriceMode.CURVE, price_curve=curve, base_year=2026)
        )
        assert r.price[1] == pytest.approx(60.0)
        assert r.price[2] == pytest.approx(55.0)

    def test_curve_is_extrapolated_after_last_year(self):
        curve = {2027: 60.0, 2028: 55.0}
        r = run_model(
            simple_inputs(
                price_mode=PriceMode.CURVE,
                price_curve=curve,
                price_escalation=0.02,
                base_year=2026,
            )
        )
        assert r.price[3] == pytest.approx(55.0 * 1.02)  # 2029
        assert r.price[4] == pytest.approx(55.0 * 1.02**2)  # 2030

    def test_capture_rate_reduces_realised_price(self):
        r = run_model(simple_inputs(flat_price=50.0, capture_rate=0.90))
        assert r.price[1] == pytest.approx(45.0)

    def test_auction_overrides_market_price(self):
        r = run_model(
            simple_inputs(
                flat_price=50.0, auction_active=True, auction_price=70.0, auction_years=2
            )
        )
        assert r.price[1] == pytest.approx(70.0)  # ihale yılı 0
        assert r.price[2] == pytest.approx(70.0)  # ihale yılı 1
        assert r.price[3] == pytest.approx(50.0)  # ihale bitti, piyasaya döndü

    def test_capture_rate_does_not_apply_to_auction(self):
        r = run_model(
            simple_inputs(
                capture_rate=0.8, auction_active=True, auction_price=70.0, auction_years=1
            )
        )
        assert r.price[1] == pytest.approx(70.0)


class TestRevenue:
    def test_electricity_revenue_is_generation_times_price(self):
        r = run_model(simple_inputs())
        assert r.revenue_electricity[1] == pytest.approx(10_000 * 50.0)

    def test_agri_revenue_added_when_active(self):
        r = run_model(
            simple_inputs(agri_active=True, agri_revenue_per_ha=2_000.0, land_area_ha=100.0)
        )
        assert r.revenue_agri[1] == pytest.approx(200_000.0)
        assert r.revenue_total[1] == pytest.approx(r.revenue_electricity[1] + 200_000.0)

    def test_no_agri_revenue_when_inactive(self):
        r = run_model(simple_inputs(agri_active=False))
        assert all(v == 0.0 for v in r.revenue_agri)

    def test_storage_revenue(self):
        r = run_model(
            simple_inputs(
                storage_power_mw=5.0,
                storage_energy_mwh=20.0,
                storage_cycles_per_year=300.0,
                storage_round_trip_efficiency=0.85,
                storage_spread=40.0,
            )
        )
        # 20 MWh * 300 çevrim * 0.85 verim * 40 marj
        assert r.revenue_storage[1] == pytest.approx(20 * 300 * 0.85 * 40)

    def test_storage_ignored_when_power_is_zero(self):
        r = run_model(simple_inputs(storage_energy_mwh=20.0, storage_spread=40.0))
        assert all(v == 0.0 for v in r.revenue_storage)


class TestOpex:
    def test_total_mode(self):
        r = run_model(simple_inputs(opex_mode=OpexMode.TOTAL, opex_total=100_000.0))
        assert r.opex[1] == pytest.approx(-100_000.0)

    def test_detail_mode_components(self):
        r = run_model(
            simple_inputs(
                opex_mode=OpexMode.DETAIL,
                opex_fixed=50_000.0,
                opex_per_mwp=6_000.0,
                opex_per_mwh=1.5,
                rent_base=20_000.0,
                rent_escalation=0.0,
            )
        )
        # 50.000 + 6.000*10 MWp + 1,5*10.000 MWh + 20.000 kira
        expected = 50_000 + 60_000 + 15_000 + 20_000
        assert r.opex[1] == pytest.approx(-expected)
        assert r.rent[1] == pytest.approx(-20_000.0)

    def test_opex_escalates(self):
        r = run_model(simple_inputs(opex_total=100_000.0, opex_escalation=0.03))
        assert r.opex[2] == pytest.approx(-100_000 * 1.03)

    def test_royalty_rent_takes_maximum(self):
        # Elektrik geliri 500.000; %5 ciro payı = 25.000 > 20.000 baz kira
        r = run_model(
            simple_inputs(
                opex_mode=OpexMode.DETAIL,
                opex_fixed=0.0,
                opex_per_mwp=0.0,
                opex_per_mwh=0.0,
                rent_base=20_000.0,
                rent_escalation=0.0,
                rent_mode=RentMode.MAX_BASE_OR_ROYALTY,
                rent_royalty=0.05,
            )
        )
        assert r.rent[1] == pytest.approx(-25_000.0)

    def test_royalty_rent_falls_back_to_base(self):
        r = run_model(
            simple_inputs(
                opex_mode=OpexMode.DETAIL,
                opex_fixed=0.0,
                opex_per_mwp=0.0,
                opex_per_mwh=0.0,
                rent_base=200_000.0,
                rent_escalation=0.0,
                rent_mode=RentMode.MAX_BASE_OR_ROYALTY,
                rent_royalty=0.01,  # 5.000 < 200.000
            )
        )
        assert r.rent[1] == pytest.approx(-200_000.0)


class TestCapex:
    def test_unit_mode_totals(self):
        r = run_model(
            simple_inputs(
                capex_mode=CapexMode.UNIT,
                capacity_mwp=10.0,
                capex_solar_per_mwp=400_000.0,
                capex_land_per_mwp=30_000.0,
                capex_grid_per_mwp=50_000.0,
                capex_other_per_mwp=20_000.0,
                storage_power_mw=0.0,
                contingency=0.0,
            )
        )
        assert r.total_capex == pytest.approx(500_000 * 10)

    def test_contingency_is_applied(self):
        r = run_model(simple_inputs(capex_total=10_000_000.0, contingency=0.10))
        assert r.total_capex == pytest.approx(11_000_000.0)

    def test_capex_schedule_splits_spend(self):
        r = run_model(
            simple_inputs(
                construction_years=2,
                capex_schedule=[0.3, 0.7],
                capex_total=10_000_000.0,
                contingency=0.0,
            )
        )
        assert r.capex[0] == pytest.approx(-3_000_000.0)
        assert r.capex[1] == pytest.approx(-7_000_000.0)

    def test_default_schedule_is_even(self):
        r = run_model(
            simple_inputs(construction_years=2, capex_total=10_000_000.0, contingency=0.0)
        )
        assert r.capex[0] == pytest.approx(-5_000_000.0)
        assert r.capex[1] == pytest.approx(-5_000_000.0)


class TestDebt:
    def test_no_debt_means_equity_funds_everything(self):
        r = run_model(simple_inputs(use_debt=False, capex_total=10_000_000.0))
        assert r.total_equity == pytest.approx(10_000_000.0)
        assert r.debt_at_cod == pytest.approx(0.0)
        assert all(d == 0.0 for d in r.debt_service)

    def test_idc_is_capitalised(self):
        r = run_model(
            simple_inputs(
                use_debt=True,
                debt_ratio=0.5,
                interest_rate=0.10,
                loan_tenor=5,
                capex_total=10_000_000.0,
                capitalize_idc=True,
            )
        )
        draw = 5_000_000.0
        idc = draw * 0.5 * 0.10  # yıl ortası çekim konvansiyonu
        assert r.capitalised_idc == pytest.approx(idc)
        assert r.debt_at_cod == pytest.approx(draw + idc)

    def test_uncapitalised_idc_is_paid_by_equity(self):
        common = dict(
            use_debt=True,
            debt_ratio=0.5,
            interest_rate=0.10,
            loan_tenor=5,
            capex_total=10_000_000.0,
        )
        cap = run_model(simple_inputs(capitalize_idc=True, **common))
        cash = run_model(simple_inputs(capitalize_idc=False, **common))
        assert cash.capitalised_idc == pytest.approx(0.0)
        assert cash.total_equity > cap.total_equity
        assert cash.debt_at_cod < cap.debt_at_cod

    def test_loan_amortises_to_zero_at_tenor_end(self):
        r = run_model(
            simple_inputs(
                use_debt=True, debt_ratio=0.7, interest_rate=0.06, loan_tenor=5, operating_life=5
            )
        )
        assert r.debt_balance[-1] == pytest.approx(0.0, abs=1e-6)

    def test_debt_service_matches_annuity(self):
        r = run_model(
            simple_inputs(
                use_debt=True, debt_ratio=0.7, interest_rate=0.06, loan_tenor=5, operating_life=5
            )
        )
        expected = pmt(0.06, 5, r.debt_at_cod)
        assert abs(r.debt_service[1]) == pytest.approx(expected)

    def test_no_debt_service_after_tenor(self):
        r = run_model(
            simple_inputs(
                use_debt=True, debt_ratio=0.7, interest_rate=0.06, loan_tenor=2, operating_life=5
            )
        )
        assert r.debt_service[3] == 0.0
        assert r.dscr[3] is None

    def test_upfront_fee_increases_equity(self):
        no_fee = run_model(simple_inputs(use_debt=True, debt_ratio=0.5, upfront_fee=0.0))
        with_fee = run_model(simple_inputs(use_debt=True, debt_ratio=0.5, upfront_fee=0.02))
        assert with_fee.total_equity > no_fee.total_equity


class TestTax:
    def test_zero_tax_rate(self):
        r = run_model(simple_inputs(tax_rate=0.0))
        assert all(t == 0.0 for t in r.tax)

    def test_tax_on_profit_without_carryforward(self):
        r = run_model(
            simple_inputs(
                tax_rate=0.25,
                depreciation_years=5,
                operating_life=5,
                loss_carryforward=False,
                use_debt=False,
            )
        )
        # EBITDA = 500.000 gelir - 100.000 opex = 400.000
        # Amortisman = 10.000.000 / 5 = 2.000.000 -> zarar, vergi yok
        assert r.tax[1] == pytest.approx(0.0)

    def test_loss_carryforward_defers_tax(self):
        common = dict(
            tax_rate=0.25,
            depreciation_years=2,
            operating_life=6,
            use_debt=False,
            capex_total=1_000_000.0,
            flat_price=50.0,
        )
        without = run_model(simple_inputs(loss_carryforward=False, **common))
        with_cf = run_model(simple_inputs(loss_carryforward=True, **common))
        # Zarar mahsubu toplam vergiyi azaltmalı (ya da eşit tutmalı)
        assert sum(with_cf.tax) >= sum(without.tax)

    def test_carryforward_pool_is_consumed(self):
        # 1. yıl büyük amortisman -> zarar; sonraki yıllar bu zararı kullanır
        r = run_model(
            simple_inputs(
                tax_rate=0.30,
                depreciation_years=1,
                operating_life=4,
                use_debt=False,
                capex_total=1_000_000.0,
                loss_carryforward=True,
            )
        )
        assert r.tax[1] == pytest.approx(0.0)  # zarar yılı
        assert r.tax[-1] < 0  # havuz tükendikten sonra vergi ödenir

    def test_carryforward_expiry_limit(self):
        unlimited = run_model(
            simple_inputs(
                tax_rate=0.30,
                depreciation_years=1,
                operating_life=8,
                use_debt=False,
                capex_total=2_000_000.0,
                loss_carryforward=True,
                loss_carryforward_limit=0,
            )
        )
        limited = run_model(
            simple_inputs(
                tax_rate=0.30,
                depreciation_years=1,
                operating_life=8,
                use_debt=False,
                capex_total=2_000_000.0,
                loss_carryforward=True,
                loss_carryforward_limit=1,
            )
        )
        # Süre sınırı zararların bir kısmını kullanılamaz hale getirir
        assert sum(limited.tax) <= sum(unlimited.tax)

    def test_interest_is_tax_deductible(self):
        common = dict(tax_rate=0.25, depreciation_years=30, operating_life=10, flat_price=80.0)
        no_debt = run_model(simple_inputs(use_debt=False, **common))
        with_debt = run_model(
            simple_inputs(use_debt=True, debt_ratio=0.7, interest_rate=0.08, loan_tenor=10, **common)
        )
        # Faiz vergi kalkanı: borçlu senaryoda ödenen vergi daha düşük
        assert sum(with_debt.tax) > sum(no_debt.tax)  # tax negatif işaretli

    def test_land_is_not_depreciated(self):
        with_land = run_model(
            simple_inputs(
                capex_mode=CapexMode.UNIT,
                capex_solar_per_mwp=400_000.0,
                capex_land_per_mwp=100_000.0,
                capex_grid_per_mwp=0.0,
                capex_other_per_mwp=0.0,
                contingency=0.0,
                depreciation_years=5,
                operating_life=5,
            )
        )
        # Amortisman tabanı yalnızca arazi dışı kalem: 400.000 * 10 MWp / 5 yıl
        assert with_land.depreciation[1] == pytest.approx(400_000 * 10 / 5)


class TestCashflowIdentities:
    def test_fcfe_equals_components(self):
        r = run_model(simple_inputs(use_debt=True, debt_ratio=0.6, tax_rate=0.25))
        for t, operating in enumerate(r.is_operating):
            if not operating:
                continue
            expected = (
                r.ebitda[t] + r.tax[t] + r.debt_service[t] + r.terminal_cashflow[t]
            )
            assert r.fcfe[t] == pytest.approx(expected)

    def test_ebitda_equals_revenue_minus_opex(self):
        r = run_model(simple_inputs())
        for t, operating in enumerate(r.is_operating):
            if operating:
                assert r.ebitda[t] == pytest.approx(r.revenue_total[t] + r.opex[t])

    def test_cfads_is_ebitda_after_tax(self):
        r = run_model(simple_inputs(tax_rate=0.25))
        for t, operating in enumerate(r.is_operating):
            if operating:
                assert r.cfads[t] == pytest.approx(
                r.ebitda[t] + r.tax[t] + r.maintenance_reserve[t]
            )

    def test_dscr_definition(self):
        r = run_model(simple_inputs(use_debt=True, debt_ratio=0.7, loan_tenor=5, operating_life=5))
        for t, d in enumerate(r.dscr):
            if d is not None:
                assert d == pytest.approx(r.cfads[t] / abs(r.debt_service[t]))

    def test_construction_fcfe_is_equity_only(self):
        r = run_model(simple_inputs(construction_years=2))
        assert r.fcfe[0] == r.equity_injection[0]
        assert r.fcfe[1] == r.equity_injection[1]

    def test_npv_uses_cost_of_equity(self):
        inp = simple_inputs(cost_of_equity=0.09)
        r = run_model(inp)
        assert r.project_npv == pytest.approx(npv(0.09, r.fcfe))


class TestTerminalValue:
    def test_decommissioning_hits_last_year(self):
        r = run_model(simple_inputs(decommissioning_per_mwp=10_000.0, capacity_mwp=10.0))
        assert r.terminal_cashflow[-1] == pytest.approx(-100_000.0)
        assert all(v == 0.0 for v in r.terminal_cashflow[:-1])

    def test_salvage_offsets_decommissioning(self):
        r = run_model(
            simple_inputs(
                decommissioning_per_mwp=10_000.0, capacity_mwp=10.0, salvage_value=40_000.0
            )
        )
        assert r.terminal_cashflow[-1] == pytest.approx(-60_000.0)


class TestLcoe:
    def test_lcoe_matches_manual_calculation(self):
        inp = simple_inputs(
            use_debt=False,
            capex_total=10_000_000.0,
            opex_total=100_000.0,
            opex_escalation=0.0,
            operating_life=5,
            lcoe_discount_rate=0.08,
            degradation=0.0,
            contingency=0.0,
        )
        r = run_model(inp)
        costs = [10_000_000.0] + [100_000.0] * 5
        gen = [0.0] + [10_000.0] * 5
        expected = npv(0.08, costs) / npv(0.08, gen)
        assert r.lcoe == pytest.approx(expected)

    def test_lcoe_is_independent_of_price(self):
        # LCOE bir maliyet göstergesidir; gelir varsayımından etkilenmemeli
        a = run_model(simple_inputs(flat_price=30.0)).lcoe
        b = run_model(simple_inputs(flat_price=90.0)).lcoe
        assert a == pytest.approx(b)

    def test_lcoe_is_independent_of_leverage(self):
        # Finansman yapısı iskonto oranının içindedir, maliyet akışında değil
        a = run_model(simple_inputs(use_debt=False)).lcoe
        b = run_model(simple_inputs(use_debt=True, debt_ratio=0.8)).lcoe
        assert a == pytest.approx(b)

    def test_higher_capex_raises_lcoe(self):
        low = run_model(simple_inputs(capex_total=10_000_000.0)).lcoe
        high = run_model(simple_inputs(capex_total=15_000_000.0)).lcoe
        assert high > low


class TestKpis:
    def test_irr_is_none_when_project_never_profits(self):
        r = run_model(simple_inputs(flat_price=0.1, operating_life=3))
        assert r.equity_irr is None or r.equity_irr < 0

    def test_irr_zeroes_the_npv(self):
        r = run_model(simple_inputs())
        assert r.equity_irr is not None
        assert npv(r.equity_irr, r.fcfe) == pytest.approx(0.0, abs=1.0)

    def test_co2_scales_with_generation(self):
        r = run_model(simple_inputs(grid_emission_factor=0.4))
        assert r.lifetime_co2_avoided_t == pytest.approx(r.lifetime_generation_mwh * 0.4)
        assert r.annual_co2_avoided_t == pytest.approx(
            r.lifetime_co2_avoided_t / r.inputs.operating_life
        )

    def test_dataframe_has_one_row_per_period(self):
        r = run_model(simple_inputs(construction_years=2, operating_life=10))
        assert len(r.to_dataframe()) == 12

    def test_kpi_dict_is_all_strings(self):
        r = run_model(simple_inputs())
        assert all(isinstance(v, str) for v in r.kpi_dict().values())


class TestValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"capacity_mwp": 0},
            {"capacity_mwp": -5},
            {"degradation": 1.5},
            {"availability": 0},
            {"operating_life": 0},
            {"construction_years": 0},
            {"debt_ratio": 1.5},
            {"tax_rate": 1.0},
            {"depreciation_years": 0},
            {"capex_schedule": [0.5, 0.5]},  # construction_years=1 ile uyumsuz
            {"price_mode": PriceMode.CURVE},  # eğri verilmedi
            {"agri_active": True, "land_area_ha": 0},
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(ValueError):
            simple_inputs(**kwargs)

    def test_tenor_longer_than_life_raises(self):
        with pytest.raises(ValueError):
            simple_inputs(use_debt=True, loan_tenor=40, operating_life=25)

    def test_error_message_lists_all_problems(self):
        with pytest.raises(ValueError) as exc:
            simple_inputs(capacity_mwp=-1, tax_rate=2.0)
        message = str(exc.value)
        assert "Kurulu güç" in message
        assert "Vergi oranı" in message


class TestDeterminism:
    def test_same_inputs_give_same_results(self):
        a = run_model(simple_inputs())
        b = run_model(simple_inputs())
        assert a.fcfe == b.fcfe
        assert a.project_npv == pytest.approx(b.project_npv)

    def test_replace_does_not_mutate_original(self):
        original = simple_inputs(capacity_mwp=10.0)
        modified = original.replace(capacity_mwp=99.0)
        assert original.capacity_mwp == 10.0
        assert modified.capacity_mwp == 99.0


class TestSculptedDebt:
    """DSCR'a göre borç boyutlama.

    Sculpting'in tanımlayıcı özelliği: geri ödeme planı hedef DSCR'ı her
    dönemde tutturacak şekilde şekillendirilir, kredi tutarı da bu
    ödemelerin bugünkü değeri olarak *çıkar*. Testler bu iki özelliği
    ayrı ayrı doğrular.
    """

    def sculpted(self, **overrides) -> ProjectInputs:
        defaults = dict(
            capacity_mwp=50.0,
            specific_yield=1_650.0,
            flat_price=45.0,
            operating_life=25,
            use_debt=True,
            debt_sizing=DebtSizing.SCULPTED_DSCR,
            target_dscr=1.45,
            max_gearing=0.85,
            interest_rate=0.065,
            loan_tenor=15,
            capex_mode=CapexMode.UNIT,
        )
        defaults.update(overrides)
        return ProjectInputs(**defaults)

    def test_dscr_is_flat_at_the_target(self):
        r = run_model(self.sculpted(target_dscr=1.45))
        ratios = [d for d in r.dscr if d is not None]
        assert len(ratios) == 15
        for ratio in ratios:
            assert ratio == pytest.approx(1.45, abs=1e-6)

    @pytest.mark.parametrize("target", [1.30, 1.50, 1.75, 2.00, 3.00])
    def test_various_targets_are_hit_exactly(self, target):
        r = run_model(self.sculpted(target_dscr=target, max_gearing=0.95))
        ratios = [d for d in r.dscr if d is not None]
        assert max(ratios) - min(ratios) == pytest.approx(0.0, abs=1e-6)
        assert r.min_dscr == pytest.approx(target, abs=1e-6)

    def test_loan_is_fully_repaid_at_tenor_end(self):
        r = run_model(self.sculpted())
        assert r.debt_balance[r.inputs.construction_years + 14] == pytest.approx(0.0, abs=1e-4)

    def test_debt_equals_present_value_of_service(self):
        inp = self.sculpted(max_gearing=0.95)
        r = run_model(inp)
        service = [
            -r.debt_service[inp.construction_years + j] for j in range(inp.loan_tenor)
        ]
        pv = sum(ds / ((1 + inp.interest_rate) ** (j + 1)) for j, ds in enumerate(service))
        assert pv == pytest.approx(r.debt_at_cod, rel=1e-6)

    def test_higher_target_means_less_debt(self):
        loose = run_model(self.sculpted(target_dscr=1.30, max_gearing=0.95))
        tight = run_model(self.sculpted(target_dscr=2.00, max_gearing=0.95))
        assert tight.debt_at_cod < loose.debt_at_cod
        assert tight.effective_gearing < loose.effective_gearing
        assert tight.total_equity > loose.total_equity

    def test_gearing_is_an_output_not_an_input(self):
        # debt_ratio yalnızca başlangıç tahminidir; sonucu değiştirmemeli.
        a = run_model(self.sculpted(debt_ratio=0.40))
        b = run_model(self.sculpted(debt_ratio=0.80))
        assert a.debt_at_cod == pytest.approx(b.debt_at_cod, rel=1e-4)
        assert a.equity_irr == pytest.approx(b.equity_irr, rel=1e-4)

    def test_max_gearing_cap_is_respected(self):
        r = run_model(self.sculpted(target_dscr=1.05, max_gearing=0.60))
        assert r.effective_gearing <= 0.60 + 1e-9

    def test_capped_solution_still_has_flat_dscr(self):
        r = run_model(self.sculpted(target_dscr=1.05, max_gearing=0.60))
        ratios = [d for d in r.dscr if d is not None]
        assert max(ratios) - min(ratios) == pytest.approx(0.0, abs=1e-6)

    def test_capped_dscr_exceeds_the_target(self):
        # Kısıt DSCR değil kaldıraç tavanıysa gerçekleşen DSCR hedefin üstündedir.
        r = run_model(self.sculpted(target_dscr=1.05, max_gearing=0.60))
        assert r.min_dscr > 1.05

    def test_solver_converges(self):
        r = run_model(self.sculpted())
        assert r.sculpting_converged
        assert 1 <= r.sculpting_iterations <= 40

    def test_gearing_mode_reports_no_iterations(self):
        r = run_model(self.sculpted(debt_sizing=DebtSizing.GEARING, debt_ratio=0.70))
        assert r.sculpting_iterations == 0

    def test_sculpting_beats_annuity_on_leverage(self):
        # Aynı minimum DSCR'da sculpting daha fazla borç taşır: anüite planı
        # ilk yıl minimumu verip sonra gereksizce yükselir, bu da borç
        # kapasitesini israf eder.
        annuity = run_model(self.sculpted(debt_sizing=DebtSizing.GEARING, debt_ratio=0.70))
        sculpted = run_model(self.sculpted(target_dscr=annuity.min_dscr, max_gearing=0.99))
        assert sculpted.debt_at_cod > annuity.debt_at_cod
        assert sculpted.equity_irr > annuity.equity_irr

    def test_annuity_dscr_profile_rises_over_time(self):
        # Sculpting'in çözdüğü sorunun kendisi: anüitede DSCR düz değildir.
        r = run_model(self.sculpted(debt_sizing=DebtSizing.GEARING, debt_ratio=0.70))
        ratios = [d for d in r.dscr if d is not None]
        assert ratios[-1] > ratios[0]

    def test_identities_still_hold_under_sculpting(self):
        inp = self.sculpted()
        r = run_model(inp)
        for t, operating in enumerate(r.is_operating):
            if not operating:
                continue
            assert r.cfads[t] == pytest.approx(
                r.ebitda[t] + r.tax[t] + r.maintenance_reserve[t]
            )
            assert r.fcfe[t] == pytest.approx(
                r.cfads[t] + r.debt_service[t] + r.terminal_cashflow[t]
            )

    def test_no_debt_disables_sculpting(self):
        r = run_model(self.sculpted(use_debt=False))
        assert r.debt_at_cod == pytest.approx(0.0)
        assert all(d is None for d in r.dscr)

    def test_unprofitable_project_gets_no_debt(self):
        r = run_model(self.sculpted(flat_price=0.5, price_escalation=0.0))
        assert r.debt_at_cod == pytest.approx(0.0, abs=1.0)

    def test_target_below_one_is_rejected(self):
        with pytest.raises(ValueError, match="Hedef DSCR"):
            self.sculpted(target_dscr=0.9)

    def test_result_is_deterministic(self):
        a = run_model(self.sculpted())
        b = run_model(self.sculpted())
        assert a.debt_at_cod == pytest.approx(b.debt_at_cod, rel=1e-12)


class TestPublicApi:
    """README ve örneklerde geçen isimler paketten doğrudan alınabilmeli."""

    @pytest.mark.parametrize(
        "name",
        [
            "ProjectInputs",
            "ModelResult",
            "run_model",
            "PriceMode",
            "CapexMode",
            "OpexMode",
            "RentMode",
            "DebtSizing",
            "run_tornado",
            "run_scenarios",
            "two_way_table",
        ],
    )
    def test_name_is_exported(self, name):
        import solarfi

        assert hasattr(solarfi, name), f"solarfi.{name} dışa aktarılmamış"
        assert name in solarfi.__all__

    def test_readme_sculpting_snippet_runs(self):
        # README'deki örneğin birebir çalıştığını doğrula.
        from solarfi import DebtSizing, ProjectInputs, run_model

        inputs = ProjectInputs(
            debt_sizing=DebtSizing.SCULPTED_DSCR,
            target_dscr=1.45,
            max_gearing=0.85,
        )
        result = run_model(inputs)
        assert 0.0 < result.effective_gearing <= 0.85
        assert result.min_dscr is not None


class TestInverterReplacement:
    """İnvertör yenileme yatırımı ve bakım rezerv hesabı.

    Paneller 30+ yıl dayanırken invertörler 12–15 yılda değişir. Bu kalem
    gerçek bir santralde kaçınılmazdır ve toplu bir harcama olduğu için
    rezerv hesabı olmadan DSCR'ı o yıl çökertir.
    """

    def project(self, **overrides) -> ProjectInputs:
        defaults = dict(
            capacity_mwp=10.0,
            specific_yield=1_000.0,
            availability=1.0,
            degradation=0.0,
            operating_life=20,
            loan_tenor=15,
            flat_price=60.0,
            price_escalation=0.0,
            inflation=0.0,  # enflasyonsuz: maliyetler elle doğrulanabilir
            opex_escalation=0.0,
            rent_escalation=0.0,
            inverter_replacement_years=[10],
            inverter_cost_per_mwp=60_000.0,
            maintenance_reserve_years=0,
        )
        defaults.update(overrides)
        return ProjectInputs(**defaults)

    # -- Harcamanın kendisi --------------------------------------------
    def test_cost_lands_in_the_right_year(self):
        r = self.project()
        result = run_model(r)
        # 10. işletme yılı = indeks C + 9
        spend_index = r.construction_years + 9
        assert result.maintenance_capex[spend_index] == pytest.approx(-600_000.0)

    def test_no_spend_in_other_years(self):
        result = run_model(self.project())
        spends = [v for v in result.maintenance_capex if v != 0.0]
        assert len(spends) == 1

    def test_cost_is_escalated_to_the_spend_year(self):
        inp = self.project(inflation=0.03, opex_escalation=0.0, rent_escalation=0.0)
        result = run_model(inp)
        spend_index = inp.construction_years + 9
        # Baz yıl parası, C + 9 yıl taşınır
        expected = 60_000 * 10 * (1.03 ** (inp.construction_years + 9))
        assert result.maintenance_capex[spend_index] == pytest.approx(-expected)

    def test_multiple_replacements(self):
        result = run_model(self.project(inverter_replacement_years=[8, 16]))
        spends = [v for v in result.maintenance_capex if v != 0.0]
        assert len(spends) == 2
        assert result.total_maintenance_capex == pytest.approx(1_200_000.0)

    def test_replacements_beyond_life_are_ignored(self):
        # Varsayılan 13. yıl, 5 yıllık bir projede hata vermemeli.
        result = run_model(self.project(operating_life=5, loan_tenor=5))
        assert result.total_maintenance_capex == pytest.approx(0.0)

    def test_zero_cost_disables_the_feature(self):
        result = run_model(self.project(inverter_cost_per_mwp=0.0))
        assert result.total_maintenance_capex == pytest.approx(0.0)

    def test_empty_list_disables_the_feature(self):
        result = run_model(self.project(inverter_replacement_years=[]))
        assert result.total_maintenance_capex == pytest.approx(0.0)

    def test_cost_scales_with_capacity(self):
        small = run_model(self.project(capacity_mwp=10.0)).total_maintenance_capex
        large = run_model(self.project(capacity_mwp=30.0)).total_maintenance_capex
        assert large == pytest.approx(small * 3)

    # -- Vergi ve amortisman -------------------------------------------
    def test_replacement_is_capitalised_not_expensed(self):
        result = run_model(self.project())
        spend_index = result.inputs.construction_years + 9
        # Harcama EBITDA'yı etkilemez (gider değil, yatırımdır)
        assert result.ebitda[spend_index] == pytest.approx(result.ebitda[spend_index - 1])

    def test_replacement_adds_to_depreciation(self):
        without = run_model(self.project(inverter_cost_per_mwp=0.0))
        with_inv = run_model(self.project())
        spend_index = with_inv.inputs.construction_years + 9
        assert with_inv.depreciation[spend_index] > without.depreciation[spend_index]

    def test_depreciation_is_spread_over_the_configured_span(self):
        inp = self.project(inverter_depreciation_years=5, depreciation_years=1)
        result = run_model(inp)
        start = inp.construction_years + 9
        for k in range(5):
            assert result.depreciation[start + k] == pytest.approx(600_000 / 5)
        assert result.depreciation[start + 5] == pytest.approx(0.0)

    def test_depreciation_span_is_capped_by_remaining_life(self):
        # 18. yılda yenileme, 20 yıllık ömür -> yalnızca 2 yıl itfa edilir
        inp = self.project(
            inverter_replacement_years=[19], inverter_depreciation_years=10, depreciation_years=1
        )
        result = run_model(inp)
        start = inp.construction_years + 18
        assert result.depreciation[start] == pytest.approx(600_000 / 2)
        assert result.depreciation[start + 1] == pytest.approx(600_000 / 2)

    # -- Rezerv hesabı --------------------------------------------------
    def test_reserve_contributions_equal_the_spend(self):
        result = run_model(self.project(maintenance_reserve_years=5))
        assert -sum(result.maintenance_reserve) == pytest.approx(
            result.total_maintenance_capex
        )

    def test_reserve_accrues_before_the_spend_year(self):
        inp = self.project(maintenance_reserve_years=4)
        result = run_model(inp)
        spend_index = inp.construction_years + 9
        for k in range(1, 5):
            assert result.maintenance_reserve[spend_index - k] == pytest.approx(-600_000 / 4)
        # Harcama yılında nakit çıkışı yok: para rezervden karşılanır
        assert result.maintenance_reserve[spend_index] == pytest.approx(0.0)

    def test_reserve_smooths_dscr(self):
        without = run_model(self.project(maintenance_reserve_years=0))
        with_mra = run_model(self.project(maintenance_reserve_years=5))
        assert with_mra.min_dscr > without.min_dscr

    def test_no_reserve_craters_dscr_in_the_spend_year(self):
        result = run_model(self.project(maintenance_reserve_years=0))
        spend_index = result.inputs.construction_years + 9
        assert result.dscr[spend_index] < result.dscr[spend_index - 1]

    def test_early_replacement_cannot_prefund(self):
        # 1. yılda yenileme: biriktirecek önceki yıl yok, harcama o yıl düşer
        inp = self.project(inverter_replacement_years=[1], maintenance_reserve_years=5)
        result = run_model(inp)
        spend_index = inp.construction_years
        assert result.maintenance_reserve[spend_index] == pytest.approx(-600_000.0)

    def test_reserve_shortens_but_does_not_change_total(self):
        short = run_model(self.project(maintenance_reserve_years=2))
        long = run_model(self.project(maintenance_reserve_years=8))
        assert -sum(short.maintenance_reserve) == pytest.approx(-sum(long.maintenance_reserve))
        # Nakit daha erken çıktığı için uzun rezerv NPV'yi düşürür
        assert long.project_npv < short.project_npv

    # -- Göstergelere etkisi -------------------------------------------
    def test_replacement_raises_lcoe(self):
        without = run_model(self.project(inverter_cost_per_mwp=0.0)).lcoe
        with_inv = run_model(self.project()).lcoe
        assert with_inv > without

    def test_reserve_timing_does_not_change_lcoe(self):
        # Rezerv bir finansman mekaniğidir, ekonomik maliyet değil.
        a = run_model(self.project(maintenance_reserve_years=0)).lcoe
        b = run_model(self.project(maintenance_reserve_years=8)).lcoe
        assert a == pytest.approx(b)

    def test_replacement_reduces_npv_and_irr(self):
        without = run_model(self.project(inverter_cost_per_mwp=0.0))
        with_inv = run_model(self.project())
        assert with_inv.project_npv < without.project_npv
        assert with_inv.equity_irr < without.equity_irr

    def test_fcfe_identity_holds_with_maintenance(self):
        result = run_model(self.project(maintenance_reserve_years=5))
        for t, operating in enumerate(result.is_operating):
            if operating:
                assert result.fcfe[t] == pytest.approx(
                    result.cfads[t] + result.debt_service[t] + result.terminal_cashflow[t]
                )

    def test_dataframe_exposes_both_series(self):
        df = run_model(self.project(maintenance_reserve_years=5)).to_dataframe()
        assert "Bakım Yatırımı" in df.columns
        assert "Bakım Rezervi" in df.columns

    # -- Sculpting ile etkileşim ---------------------------------------
    def test_sculpting_keeps_dscr_flat_through_the_reserve_period(self):
        inp = self.project(
            debt_sizing=DebtSizing.SCULPTED_DSCR,
            target_dscr=1.40,
            max_gearing=0.95,
            maintenance_reserve_years=5,
        )
        result = run_model(inp)
        ratios = [d for d in result.dscr if d is not None]
        assert max(ratios) - min(ratios) == pytest.approx(0.0, abs=1e-6)
        assert result.min_dscr == pytest.approx(1.40, abs=1e-6)

    def test_sculpting_reduces_debt_service_during_accrual(self):
        inp = self.project(
            debt_sizing=DebtSizing.SCULPTED_DSCR,
            target_dscr=1.40,
            max_gearing=0.95,
            maintenance_reserve_years=5,
        )
        result = run_model(inp)
        spend_index = inp.construction_years + 9
        during = abs(result.debt_service[spend_index - 1])
        after = abs(result.debt_service[spend_index])
        assert during < after  # rezerv yıllarında daha az anapara ödenir

    # -- Doğrulama ------------------------------------------------------
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"inverter_replacement_years": [0]},
            {"inverter_replacement_years": [-3]},
            {"inverter_replacement_years": [5, 5]},
            {"inverter_cost_per_mwp": -1.0},
            {"inverter_depreciation_years": 0},
        ],
    )
    def test_invalid_inputs_raise(self, kwargs):
        with pytest.raises(ValueError):
            self.project(**kwargs)
