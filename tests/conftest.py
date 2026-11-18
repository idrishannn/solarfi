"""Pytest yapılandırması.

Paket kurulmamış olsa bile testlerin depo kökünden çalışabilmesi için
proje kökünü içe aktarma yoluna ekler.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
