# Fiyat verisi

## Bu klasördeki dosyalar sentetiktir

`sample_prices_fixed.csv` ve `sample_prices_tracker.csv` dosyaları
`solarfi/data_loader.py` içindeki `make_synthetic_curves()` fonksiyonuyla
üretilmiştir. Uygulamanın çalıştığını göstermek dışında hiçbir amaçları yoktur ve
**hiçbir gerçek piyasa görüşünü temsil etmezler.**

Eğri üç bileşenden oluşur; formül `data_loader.py` içinde açıkça yazılıdır:

1. yüksek bir başlangıç seviyesinden uzun vadeli dengeye üstel iniş,
2. 2035–2045 arası "cannibalisation" çukuru,
3. 2045 sonrası hafif nominal toparlanma.

Bu şekil gerçek Avrupa fiyat tahminlerine kabaca benzer ama sayılar uydurmadır.

---

## Neden gerçek veri yok

AFRY (eski adıyla Pöyry), Aurora Energy Research, BNEF ve benzeri sağlayıcıların
fiyat tahminleri **lisanslı ticari ürünlerdir**. Bir aboneliğe veya danışmanlık
sözleşmesine dayanırlar ve halka açık bir depoda yeniden dağıtılmaları hemen her
zaman sözleşmeye aykırıdır.

Elinde böyle bir veri varsa:

- dosyayı `data/private/` altına koy (bu klasör `.gitignore`'da),
- ya da dosya adının sonuna `_licensed` ekle (o da yok sayılır),
- ya da uygulamanın kenar çubuğundaki yükleme alanını kullan — dosya diske hiç yazılmaz.

---

## CSV formatı

İlk sütun `Year` olmalı, geri kalan her sütun bir bölge/piyasa:

```csv
Year,Kuzey,Merkez-Kuzey,Merkez-Güney,Güney
2026,82.9,77.5,70.3,66.7
2027,77.5,72.4,65.7,62.3
2028,72.8,68.1,61.8,58.6
```

Kurallar:

| Kural | Davranış |
|---|---|
| `Year` sütunu zorunlu | Yoksa yükleme hata verir (büyük/küçük harf duyarsız) |
| Yıllar tekrarlanamaz | Tekrar varsa hata |
| Sayısal olmayan yıl | Hata |
| Tamamen boş bölge sütunu | Hata |
| Boş tek hücreler | O yıl atlanır |
| Sütun sırası | Önemsiz, otomatik yıla göre sıralanır |

**Birim:** Modelin geri kalanı hangi para birimini seçtiysen onu varsayar. Fiyatlar
`para_birimi/MWh` cinsinden ve **nominal** olmalıdır. Reel bir eğrin varsa ya
enflasyonla nominale çevir ya da modelde bütün eskalasyon oranlarını sıfırla.

**Kapsam dışı yıllar:** Eğrinin bittiği yıldan sonrası, arayüzde girdiğin
eskalasyon oranıyla uzatılır. Eğrinin başlamadığı önceki yıllar ilk değerde sabit
tutulur.

---

## Kendi eğrini üretmek

Sentetik üreteci kendi piyasan için de kullanabilirsin:

```python
from solarfi.data_loader import make_synthetic_curves

df = make_synthetic_curves(
    start_year=2026,
    end_year=2055,
    regions={"Marmara": 48.0, "Ege": 45.0, "Akdeniz": 43.0, "İç Anadolu": 42.0},
    technology="tracker",
)
df.to_csv("data/turkiye_sentetik.csv")
```
