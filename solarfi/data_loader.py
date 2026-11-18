"""Fiyat eğrisi yükleme.

Modelin fiyat verisi **kodun içine gömülü değildir**. Bunun iki sebebi var:

1. Ticari fiyat projeksiyonları (AFRY/Pöyry, Aurora, BNEF vb.) lisanslıdır
   ve bir public repoya konulamaz.
2. Veri kodun dışında olunca model İtalya'ya değil, elinde eğri olan her
   piyasaya uygulanabilir.

Depodaki `data/` klasöründe **sentetik** örnek eğriler bulunur; bunlar
yalnızca uygulamanın çalıştığını göstermek içindir, hiçbir gerçek piyasa
görüşünü temsil etmez.

Beklenen CSV formatı
--------------------
İlk sütun ``Year``, diğer sütunlar bölge adları::

    Year,Kuzey,Merkez,Güney
    2026,78.4,77.9,76.1
    2027,76.2,75.4,74.0
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

__all__ = [
    "DATA_DIR",
    "load_price_curves",
    "available_datasets",
    "curve_to_mapping",
    "make_synthetic_curves",
]

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def available_datasets(data_dir: Path | None = None) -> dict[str, Path]:
    """`data/` içindeki CSV dosyalarını {görünen ad: yol} olarak döner."""
    directory = Path(data_dir) if data_dir else DATA_DIR
    if not directory.exists():
        return {}
    return {p.stem: p for p in sorted(directory.glob("*.csv"))}


def load_price_curves(path: str | Path) -> pd.DataFrame:
    """Fiyat eğrisi CSV'sini okur ve doğrular.

    Returns
    -------
    pd.DataFrame
        `Year` indeksli, sütunları bölge olan tablo.

    Raises
    ------
    ValueError
        Dosyada `Year` sütunu yoksa, sayısal olmayan değer varsa veya
        hiç bölge sütunu yoksa.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Fiyat dosyası bulunamadı: {path}")

    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    if "year" not in cols:
        raise ValueError(f"{path.name}: 'Year' sütunu bulunamadı.")

    df = df.rename(columns={cols["year"]: "Year"})
    df["Year"] = pd.to_numeric(df["Year"], errors="coerce")
    if df["Year"].isna().any():
        raise ValueError(f"{path.name}: 'Year' sütununda sayısal olmayan değer var.")
    df["Year"] = df["Year"].astype(int)

    region_cols = [c for c in df.columns if c != "Year"]
    if not region_cols:
        raise ValueError(f"{path.name}: Hiç bölge sütunu yok.")

    for col in region_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[region_cols].isna().all().any():
        empty = [c for c in region_cols if df[c].isna().all()]
        raise ValueError(f"{path.name}: Şu sütunlar tamamen boş/geçersiz: {empty}")

    if df["Year"].duplicated().any():
        raise ValueError(f"{path.name}: Tekrar eden yıl değerleri var.")

    return df.set_index("Year").sort_index()


def curve_to_mapping(df: pd.DataFrame, region: str) -> dict[int, float]:
    """DataFrame'in bir sütununu `ProjectInputs.price_curve` sözlüğüne çevirir."""
    if region not in df.columns:
        raise KeyError(f"'{region}' sütunu veri setinde yok. Mevcut: {list(df.columns)}")
    series = df[region].dropna()
    if series.empty:
        raise ValueError(f"'{region}' sütununda geçerli veri yok.")
    return {int(year): float(value) for year, value in series.items()}


# ----------------------------------------------------------------------
# Sentetik örnek veri üreteci
# ----------------------------------------------------------------------


def make_synthetic_curves(
    start_year: int = 2026,
    end_year: int = 2060,
    regions: dict[str, float] | None = None,
    technology: str = "fixed",
) -> pd.DataFrame:
    """Tamamen uydurma ama makul şekilli fiyat eğrileri üretir.

    Eğri üç bileşenden oluşur ve formülü açıkça belgelidir; kimse bunu
    gerçek bir piyasa tahmini sanmasın diye:

    * yüksek bir başlangıç seviyesinden uzun vadeli dengeye üstel iniş,
    * yenilenebilir payı arttıkça artan bir "cannibalisation" düşüşü,
    * 2045 sonrası hafif nominal toparlanma.

    Parameters
    ----------
    regions
        {bölge adı: uzun vadeli denge fiyatı} eşlemesi.
    technology
        ``"tracker"`` eğriyi hafifçe yukarı kaydırır (izleyici sistemler
        genelde daha iyi fiyat yakalar).
    """
    if regions is None:
        regions = {
            "Kuzey": 46.0,
            "Merkez-Kuzey": 43.0,
            "Merkez-Güney": 39.0,
            "Güney": 37.0,
            "Adalar": 35.0,
        }

    tech_shift = 1.03 if technology.lower() == "tracker" else 1.00
    years = list(range(start_year, end_year + 1))
    data: dict[str, list[float]] = {}

    for region, equilibrium in regions.items():
        start_level = equilibrium * 1.75  # bugünkü yüksek seviye
        values = []
        for y in years:
            k = y - start_year
            # 1) dengeye üstel yakınsama (yarılanma ~6 yıl)
            decay = math.exp(-k / 6.0)
            level = equilibrium + (start_level - equilibrium) * decay
            # 2) 2035-2045 arası cannibalisation çukuru
            if 2035 <= y <= 2045:
                level *= 1.0 - 0.10 * math.sin(math.pi * (y - 2035) / 10.0)
            # 3) 2045 sonrası hafif nominal toparlanma
            if y > 2045:
                level *= (1.0 + 0.004) ** (y - 2045)
            values.append(round(level * tech_shift, 2))
        data[region] = values

    return pd.DataFrame({"Year": years, **data}).set_index("Year")
