"""SolarFi — güneş santrali proje finansmanı modelleme motoru.

Hızlı kullanım::

    from solarfi import ProjectInputs, run_model

    inputs = ProjectInputs(capacity_mwp=50, flat_price=72.0)
    result = run_model(inputs)
    print(result.kpi_dict())
    result.to_dataframe().to_csv("nakit_akisi.csv", index=False)
"""

from .engine import ModelResult, run_model
from .export import build_workbook, workbook_bytes
from .inputs import (
    CapexMode,
    DebtSizing,
    OpexMode,
    PriceMode,
    ProjectInputs,
    RentMode,
)
from .sensitivity import (
    DEFAULT_VARIABLES,
    SensitivityVariable,
    run_scenarios,
    run_tornado,
    two_way_table,
)

__version__ = "1.0.0"

__all__ = [
    "ProjectInputs",
    "ModelResult",
    "run_model",
    "build_workbook",
    "workbook_bytes",
    "PriceMode",
    "CapexMode",
    "DebtSizing",
    "OpexMode",
    "RentMode",
    "run_tornado",
    "run_scenarios",
    "two_way_table",
    "SensitivityVariable",
    "DEFAULT_VARIABLES",
    "__version__",
]
