"""Arayüz sunum katmanı: palet, sayı biçimlendirme ve grafik teması.

Bu modül `app.py`'nin görsel kararlarını tek yerde toplar. Ayrı tutulmasının
sebebi, tasarımın uygulama mantığına karışmaması: renk ya da biçim
değiştirmek istediğinde tek dosyaya bakman yeter.

Sayı biçimlendirme neden burada
-------------------------------
Python'un varsayılan `f"{x:,.1f}"` biçimi `1,234.5` üretir; Türkçe'de bu
`1.234,5` olmalı. Yanlış ayraç, finansal bir sunumda profesyonellik algısını
en çok bozan detaylardan biridir ve `locale` modülü platforma göre farklı
davrandığı için elle çevirmek daha güvenilirdir.
"""

from __future__ import annotations

import altair as alt

__all__ = [
    "INK",
    "MUTED",
    "ACCENT",
    "POSITIVE",
    "NEGATIVE",
    "NEUTRAL",
    "GRID",
    "CSS",
    "currency_symbol",
    "tr_number",
    "money",
    "compact_money",
    "percent",
    "multiple",
    "years",
    "style_chart",
]

# ----------------------------------------------------------------------
# Palet
# ----------------------------------------------------------------------
# Tek bir vurgu rengi + üç işlevsel renk. Grafiklerde renk sayısını düşük
# tutmak, "her seri farklı renk" yaklaşımından çok daha okunaklı sonuç verir.

# Renkler konunun kendi dünyasından türetildi. Monokristal silisyum
# hücrenin derin, hafif mor mavisi bütün yapısal rengi verir; kehribar ise
# güneşlenmenin rengidir ve tek vurgu olarak kıt kullanılır. Sıcak krem +
# terrakota paleti bilinçli olarak elendi: bu bir editoryal sayfa değil,
# bir ölçüm aracı, ve o kombinasyon artık her yerde görünen bir varsayılan.
INK = "#10243E"  # panel mavisi — ana metin ve yapı
INK_MID = "#2C4A70"  # ikincil yapı
MUTED = "#5A6B80"  # ikincil metin, eksen etiketleri
ACCENT = "#C8892B"  # kehribar — vurgu, eylem, seçili durum
ACCENT_SOFT = "#E8C48A"
POSITIVE = "#2E6B4F"  # gelir
NEGATIVE = "#A6402E"  # gider
NEUTRAL = "#8A97A8"  # nötr kalemler (vergi vb.)
GRID = "#EEF2F7"  # ızgara çizgileri
HAIRLINE = "#D8E0EA"  # ayraçlar
SURFACE = "#F7F9FC"

#: Ticari kredilerde merchant güneş için yaygın DSCR eşikleri.
DSCR_FLOOR = 1.20
DSCR_COMFORT = 1.35

#: Streamlit'in yapısal sınıf adlarına dokunmadan yapılan hafif rötuşlar.
#: `data-testid` seçicileri Streamlit sürümleri arasında en stabil olanlar;
#: yine de bir seçici tutmazsa yalnızca o rötuş kaybolur, arayüz bozulmaz.
CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap');

/* IBM Plex teknik dokümantasyon için tasarlandı ve Mono kesiti mükemmel
   tabular figürler verir. Sayıların tabular olması burada süs değil işlev:
   bir nakit akışı tablosunda rakamlar sütun sütun hizalanmazsa göz
   karşılaştırma yapamaz. */
  html, body, [class*="st-"], .stMarkdown, button, input, select, textarea {{
    font-family: 'IBM Plex Sans', -apple-system, 'Segoe UI', system-ui, sans-serif;
  }}

  /* Yukarıdaki geniş seçici Streamlit'in ikon fontunu da eziyor; o zaman
     expander başlıklarında ikon yerine "arrow_down" metni görünüyor. */
  [data-testid="stIconMaterial"], [data-testid="stExpanderIcon"] {{
    font-family: 'Material Symbols Rounded' !important;
  }}

  [data-testid="stMetricValue"],
  [data-testid="stDataFrame"],
  .sfi-figure, .sfi-scale {{
    font-family: 'IBM Plex Mono', 'SFMono-Regular', Consolas, monospace;
    font-variant-numeric: tabular-nums;
    font-feature-settings: 'tnum' 1;
  }}

  .block-container {{ padding-top: 2.4rem; max-width: 1320px; }}

  h1 {{ font-weight: 600; letter-spacing: -0.02em; font-size: 1.85rem; color: {INK}; }}
  h2, h3 {{ font-weight: 600; letter-spacing: -0.01em; color: {INK}; }}

  /* --- Başlık bandı --- */
  .sfi-masthead {{
    border-bottom: 2px solid {INK};
    padding-bottom: 0.7rem;
    margin-bottom: 1.4rem;
  }}
  .sfi-eyebrow {{
    font-size: 0.68rem;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: {ACCENT};
    font-weight: 600;
    margin-bottom: 0.2rem;
  }}
  .sfi-name {{
    font-size: 1.9rem;
    font-weight: 600;
    color: {INK};
    line-height: 1.1;
    letter-spacing: -0.02em;
  }}
  .sfi-meta {{ font-size: 0.84rem; color: {MUTED}; margin-top: 0.35rem; }}
  .sfi-meta i {{ color: {HAIRLINE}; font-style: normal; margin: 0 0.55rem; }}

  /* --- Gösterge kartları --- */
  [data-testid="stMetricValue"] {{
    font-size: 1.6rem;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: {INK};
  }}
  [data-testid="stMetricLabel"] {{
    font-size: 0.7rem;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    font-weight: 600;
    color: {MUTED};
  }}
  [data-testid="stMetricDelta"] {{ font-size: 0.78rem; }}

  /* --- DSCR şeridi (imza öğesi) ---
     Bir DSCR sayısı tek başına bir şey söylemez; anlamı kredi eşiğine olan
     mesafesindedir. Şerit üç bölgeyi gösterir: finanse edilemez, sınırda,
     rahat. */
  .sfi-band {{ margin-top: 0.55rem; }}
  .sfi-track {{
    position: relative;
    height: 6px;
    border-radius: 3px;
    background: linear-gradient(to right,
      {NEGATIVE} 0%, {NEGATIVE} 30%,
      {ACCENT} 30%, {ACCENT} 42%,
      {POSITIVE} 42%, {POSITIVE} 100%);
    opacity: 0.30;
  }}
  .sfi-marker {{
    position: absolute; top: -5px;
    width: 3px; height: 16px;
    background: {INK}; border-radius: 1px;
  }}
  .sfi-scale {{
    display: flex; justify-content: space-between;
    font-size: 0.6rem; color: {MUTED}; margin-top: 0.3rem;
  }}

  /* --- Sekmeler: emojisiz, alt çizgili sade şerit --- */
  .stTabs [data-baseweb="tab-list"] {{
    gap: 1.7rem;
    border-bottom: 1px solid {HAIRLINE};
  }}
  .stTabs [data-baseweb="tab"] {{
    height: 2.6rem; padding: 0 0.1rem;
    font-size: 0.86rem; font-weight: 500; color: {MUTED};
  }}
  .stTabs [aria-selected="true"] {{ color: {INK}; font-weight: 600; }}

  /* --- Kenar çubuğu --- */
  section[data-testid="stSidebar"] {{ border-right: 1px solid {HAIRLINE}; }}
  section[data-testid="stSidebar"] .block-container {{ padding-top: 1.2rem; }}
  section[data-testid="stSidebar"] label {{
    font-size: 0.78rem; font-weight: 500; color: {INK};
  }}
  section[data-testid="stSidebar"] [data-testid="stExpander"] summary {{
    font-size: 0.82rem; font-weight: 600; color: {INK};
  }}

  /* --- Bölüm etiketi --- */
  .sfi-section {{
    font-size: 0.7rem; letter-spacing: 0.13em; text-transform: uppercase;
    color: {MUTED}; font-weight: 600; margin: 0.2rem 0 0.6rem;
  }}
  .sfi-note {{ font-size: 0.79rem; color: {MUTED}; }}
</style>
"""


def dscr_band(value: float | None, ceiling: float = 2.0) -> str:
    """Minimum DSCR'ı kredi eşiklerine göre konumlandıran şerit.

    Arayüzün imza öğesi. Bir DSCR rakamı tek başına yorumlanamaz; kredi
    verenler onu eşiklere göre okur. Şerit bu okumayı görselleştirir.
    """
    if value is None:
        return '<div class="sfi-band"><div class="sfi-scale"><span>kredi yok</span></div></div>'
    position = max(0.0, min(1.0, value / ceiling)) * 100
    return (
        '<div class="sfi-band">'
        f'<div class="sfi-track"><div class="sfi-marker" '
        f'style="left: calc({position:.1f}% - 1.5px);"></div></div>'
        '<div class="sfi-scale">'
        f'<span>0,00x</span><span>{tr_number(DSCR_FLOOR, 2)}x</span>'
        f'<span>{tr_number(DSCR_COMFORT, 2)}x</span><span>{tr_number(ceiling, 2)}x+</span>'
        "</div></div>"
    )


_SYMBOLS = {"EUR": "€", "USD": "$", "TRY": "₺", "GBP": "£"}


def currency_symbol(code: str) -> str:
    """Para birimi kodundan sembol; bilinmiyorsa kodun kendisi."""
    return _SYMBOLS.get(code.upper(), code)


def tr_number(value: float, decimals: int = 1) -> str:
    """Türkçe ayraçlarla sayı: binlik nokta, ondalık virgül.

    Örnek: ``tr_number(1234567.8)`` -> ``"1.234.567,8"``
    """
    formatted = f"{value:,.{decimals}f}"
    # Önce iki ayracı geçici bir işaretle takas et ki birbirine karışmasın.
    return formatted.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def money(value: float, code: str, decimals: int = 0) -> str:
    """Tam tutar: ``"1.234.568 €"``"""
    return f"{tr_number(value, decimals)} {currency_symbol(code)}"


def compact_money(value: float, code: str) -> str:
    """Büyük tutarları kısaltır: ``"8,4 M€"``, ``"940 B€"``

    Panoda okunabilirlik için; tam değer her zaman tablolarda mevcut.
    """
    symbol = currency_symbol(code)
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{tr_number(value / 1e9, 2)} Mr{symbol}"
    if magnitude >= 1e6:
        return f"{tr_number(value / 1e6, 1)} M{symbol}"
    if magnitude >= 1e3:
        return f"{tr_number(value / 1e3, 0)} B{symbol}"
    return f"{tr_number(value, 0)} {symbol}"


def percent(value: float, decimals: int = 1) -> str:
    """Kesirden yüzdeye: ``percent(0.1638)`` -> ``"%16,4"``"""
    return f"%{tr_number(value * 100, decimals)}"


def multiple(value: float) -> str:
    """Katsayı: ``multiple(1.53)`` -> ``"1,53x"``"""
    return f"{tr_number(value, 2)}x"


def years(value: float) -> str:
    """Süre: ``years(7.1)`` -> ``"7,1 yıl"``"""
    return f"{tr_number(value, 1)} yıl"


# ----------------------------------------------------------------------
# Grafik teması
# ----------------------------------------------------------------------


def style_chart(chart: alt.Chart) -> alt.Chart:
    """Bütün grafiklere aynı sade görünümü uygular.

    Varsayılan Altair teması kalın eksenler, koyu ızgara ve büyük yazı
    kullanır; veri yoğun bir panoda bu gürültü yaratır. Burada ızgara
    inceltilir, eksen çizgileri kaldırılır ve etiketler geri çekilir.
    """
    return (
        chart.configure_view(strokeWidth=0)
        .configure_axis(
            grid=True,
            gridColor=GRID,
            gridWidth=1,
            domain=False,
            tickColor=GRID,
            tickSize=4,
            labelColor=MUTED,
            labelFontSize=11,
            labelPadding=6,
            titleColor=MUTED,
            titleFontSize=11,
            titleFontWeight="normal",
            titlePadding=12,
        )
        .configure_legend(
            orient="top",
            direction="horizontal",
            title=None,
            labelColor=MUTED,
            labelFontSize=11,
            symbolType="square",
            symbolSize=110,
            offset=8,
        )
        .configure_title(color=INK, fontSize=13, fontWeight=600, anchor="start")
    )
