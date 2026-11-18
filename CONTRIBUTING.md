# Katkı rehberi

## Kurulum

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                  # tamamı (~30 sn)
pytest -m "not ui"      # sadece motor, hızlı geri bildirim için
ruff check .
```

## Değişiklik yaparken

**Finansal mantık `solarfi/` içinde kalır, `app.py` içine sızmaz.** Arayüz yalnızca
girdi toplar ve sonucu çizer. Bir hesabı `app.py` içine yazmak zorunda kaldığını
düşünüyorsan, o hesap muhtemelen motora ait.

**Her davranış değişikliği bir testle gelir.** Testler "çalışıyor mu" değil "doğru
sayıyı mı veriyor" sorusunu sorar: mümkünse beklenen değeri elle veya kapalı formla
hesaplayıp karşılaştır.

**Yeni bir girdi eklerken üç yeri güncelle:**

1. `ProjectInputs` içinde alan + docstring + gerekiyorsa `validate()` kontrolü
2. `app.py` içinde ilgili moda uygun bir widget
3. `docs/METHODOLOGY.md` içinde formül veya varsayım açıklaması

Arayüze yeni bir dal (onay kutusu, mod, seçenek) eklediyseniz `tests/test_app.py`
içine de bir satır ekleyin. Bu testler uygulamayı `AppTest` ile gerçekten çalıştırır
ve yalnızca o dal açıldığında ortaya çıkan tanımsız değişkenleri yakalar — Streamlit'te
en sık yapılan hata budur.

## Konvansiyonlar

- Yüzdeler motorda **ondalık kesir** (%7,5 → `0.075`). Dönüşümü arayüz yapar.
- Giderler **negatif işaretli** serilerdir.
- `cashflows[0]` t=0'dadır ve iskonto edilmez.
- Docstring'ler ve yorumlar Türkçe; kod tanımlayıcıları (değişken, fonksiyon) İngilizce.

## Lisanslı veri

Depoya **hiçbir koşulda** ticari fiyat tahmini eklemeyin. `data/` yalnızca sentetik
örnekler içindir.
