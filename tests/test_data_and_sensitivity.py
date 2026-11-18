"""`solarfi.data_loader` ve `solarfi.sensitivity` testleri."""

import pandas as pd
import pytest

from solarfi import ProjectInputs, run_model, run_scenarios, run_tornado
from solarfi.data_loader import (
    curve_to_mapping,
    load_price_curves,
    make_synthetic_curves,
)
from solarfi.inputs import PriceMode
from solarfi.sensitivity import DEFAULT_VARIABLES, two_way_table


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "Year,Kuzey,Güney\n2026,80.0,75.0\n2027,70.0,66.0\n2028,60.0,57.0\n",
        encoding="utf-8",
    )
    return path


class TestLoadPriceCurves:
    def test_reads_and_indexes_by_year(self, csv_file):
        df = load_price_curves(csv_file)
        assert list(df.index) == [2026, 2027, 2028]
        assert df.loc[2027, "Kuzey"] == 80.0 - 10.0

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_price_curves(tmp_path / "yok.csv")

    def test_missing_year_column_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("Yil,Kuzey\n2026,80\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Year"):
            load_price_curves(path)

    def test_no_region_columns_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("Year\n2026\n2027\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bölge"):
            load_price_curves(path)

    def test_duplicate_years_raise(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("Year,Kuzey\n2026,80\n2026,70\n", encoding="utf-8")
        with pytest.raises(ValueError, match="Tekrar eden"):
            load_price_curves(path)

    def test_non_numeric_year_raises(self, tmp_path):
        path = tmp_path / "bad.csv"
        path.write_text("Year,Kuzey\nabc,80\n", encoding="utf-8")
        with pytest.raises(ValueError, match="sayısal"):
            load_price_curves(path)

    def test_case_insensitive_year_header(self, tmp_path):
        path = tmp_path / "ok.csv"
        path.write_text("year,Kuzey\n2026,80\n", encoding="utf-8")
        assert load_price_curves(path).index[0] == 2026


class TestCurveToMapping:
    def test_converts_column(self, csv_file):
        mapping = curve_to_mapping(load_price_curves(csv_file), "Kuzey")
        assert mapping == {2026: 80.0, 2027: 70.0, 2028: 60.0}

    def test_unknown_region_raises(self, csv_file):
        with pytest.raises(KeyError):
            curve_to_mapping(load_price_curves(csv_file), "Batı")

    def test_mapping_feeds_the_engine(self, csv_file):
        mapping = curve_to_mapping(load_price_curves(csv_file), "Güney")
        inp = ProjectInputs(
            price_mode=PriceMode.CURVE,
            price_curve=mapping,
            base_year=2026,
            construction_years=1,
            operating_life=3,
            loan_tenor=3,
        )
        r = run_model(inp)
        assert r.price[1] == pytest.approx(66.0)  # 2027


class TestSyntheticCurves:
    def test_shape_and_range(self):
        df = make_synthetic_curves(2026, 2040)
        assert list(df.index) == list(range(2026, 2041))
        assert (df > 0).all().all()

    def test_prices_decline_from_start(self):
        df = make_synthetic_curves(2026, 2050)
        for col in df.columns:
            assert df[col].iloc[-1] < df[col].iloc[0]

    def test_tracker_is_priced_above_fixed(self):
        fixed = make_synthetic_curves(2026, 2035, technology="fixed")
        tracker = make_synthetic_curves(2026, 2035, technology="tracker")
        assert (tracker.values > fixed.values).all()

    def test_custom_regions(self):
        df = make_synthetic_curves(2026, 2030, regions={"Anadolu": 40.0})
        assert list(df.columns) == ["Anadolu"]


class TestTornado:
    def test_returns_two_rows_per_variable(self):
        df = run_tornado(ProjectInputs())
        assert set(df["Yön"]) == {"Yukarı", "Aşağı"}
        assert len(df) == len(DEFAULT_VARIABLES) * 2

    def test_sorted_by_absolute_impact(self):
        df = run_tornado(ProjectInputs())
        impacts = df.groupby("Parametre", observed=True)["MutlakEtki"].max()
        ordered = [impacts[p] for p in df.attrs["order"]]
        assert ordered == sorted(ordered, reverse=True)

    def test_price_up_increases_npv(self):
        df = run_tornado(ProjectInputs())
        row = df[(df["Parametre"] == "Elektrik Fiyatı") & (df["Yön"] == "Yukarı")]
        assert row["Etki"].iloc[0] > 0

    def test_capex_up_decreases_npv(self):
        df = run_tornado(ProjectInputs())
        row = df[(df["Parametre"] == "CAPEX") & (df["Yön"] == "Yukarı")]
        assert row["Etki"].iloc[0] < 0

    def test_capex_variable_moves_total_not_just_solar(self):
        base = ProjectInputs()
        from solarfi.sensitivity import _scale_capex

        bumped = _scale_capex(base, 1)
        assert run_model(bumped).total_capex == pytest.approx(
            run_model(base).total_capex * 1.10
        )

    def test_alternative_metric(self):
        df = run_tornado(ProjectInputs(), metric="lcoe")
        assert df.attrs["metric"] == "lcoe"
        row = df[(df["Parametre"] == "CAPEX") & (df["Yön"] == "Yukarı")]
        assert row["Etki"].iloc[0] > 0  # CAPEX artınca LCOE artar

    def test_base_value_recorded(self):
        base = ProjectInputs()
        df = run_tornado(base)
        assert df.attrs["base_value"] == pytest.approx(run_model(base).project_npv)


class TestScenarios:
    def test_contains_expected_rows(self):
        df = run_scenarios(ProjectInputs())
        assert "Base Case" in set(df["Senaryo"])
        assert "P90 Üretim" in set(df["Senaryo"])

    def test_high_case_beats_low_case(self):
        df = run_scenarios(ProjectInputs()).set_index("Senaryo")
        assert df.loc["High Case", "NPV"] > df.loc["Low Case", "NPV"]

    def test_p90_is_worse_than_base(self):
        df = run_scenarios(ProjectInputs()).set_index("Senaryo")
        assert df.loc["P90 Üretim", "NPV"] < df.loc["Base Case", "NPV"]

    def test_stress_case_is_worst(self):
        df = run_scenarios(ProjectInputs()).set_index("Senaryo")
        assert df["NPV"].idxmin() == "Stres (Düşük fiyat + P90)"


class TestTwoWayTable:
    def test_shape(self):
        price = DEFAULT_VARIABLES[0]
        capex = DEFAULT_VARIABLES[2]
        table = two_way_table(ProjectInputs(), price, capex, steps=(-0.1, 0.0, 0.1))
        assert table.shape == (3, 3)

    def test_monotonic_in_price(self):
        price = DEFAULT_VARIABLES[0]
        capex = DEFAULT_VARIABLES[2]
        table = two_way_table(ProjectInputs(), price, capex, steps=(-0.1, 0.0, 0.1))
        column = table.iloc[:, 0].tolist()
        assert column == sorted(column)  # fiyat arttıkça NPV artar


class TestBundledSampleData:
    """Depoda gelen örnek CSV'ler gerçekten yüklenebiliyor mu?"""

    def test_sample_files_load(self):
        from solarfi.data_loader import DATA_DIR, available_datasets

        datasets = available_datasets()
        if not datasets:
            pytest.skip(f"{DATA_DIR} içinde örnek veri yok.")
        for _name, path in datasets.items():
            df = load_price_curves(path)
            assert isinstance(df, pd.DataFrame)
            assert not df.empty
