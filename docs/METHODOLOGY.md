# Metodoloji

Bu belge modelin ne yaptığını, hangi varsayımlara dayandığını ve **neyi
yapmadığını** açıklar. Bir finansal modelin en tehlikeli tarafı sessizce yanlış
olmasıdır; bu yüzden sınırlar da formüller kadar açık yazılmıştır.

---

## 1. Zaman ekseni ve iskonto konvansiyonu

Model yıllık dönemlerle çalışır:

```
t = 0 .. C-1     inşaat dönemi   (CAPEX harcanır, üretim yok)
t = C .. C+L-1   işletme dönemi  (COD sonrası)
```

`C = construction_years`, `L = operating_life`. `t` dönemi `base_year + t`
takvim yılına karşılık gelir.

**İskonto:** `t` dönemindeki nakit akışı `(1 + r)^t` ile bölünür. Yani `t = 0`
iskonto **edilmez**. Bu, Excel'in `NPV()` fonksiyonundan farklıdır — Excel ilk
elemanı da iskonto eder, bu yüzden Excel'de aynı sonucu almak için
`= CF0 + NPV(r; CF1:CFn)` yazmak gerekir.

Bütün tutarlar **nominaldir**. Reel bir analiz istiyorsanız enflasyonu ve bütün
eskalasyon oranlarını sıfırlayıp iskonto oranlarını reel değerlerle girin.

---

## 2. Üretim

$$
E_j = P \times Y \times A \times f_P \times (1 - d)^j
$$

| Sembol | Anlam | Girdi |
|---|---|---|
| $E_j$ | j. işletme yılındaki üretim (MWh) | — |
| $P$ | kurulu güç (MWp) | `capacity_mwp` |
| $Y$ | spesifik verim (kWh/kWp) | `specific_yield` |
| $A$ | kullanılabilirlik | `availability` |
| $f_P$ | olasılık senaryosu çarpanı | `p_factor` |
| $d$ | yıllık degradasyon | `degradation` |

Birim notu: MWp × kWh/kWp doğrudan MWh verir (1000 çarpanı ile 1000 bölümü
sadeleşir).

`p_factor` P50 için 1,00'dir. Tipik değerler: P75 ≈ 0,96, P90 ≈ 0,92. Bu
çarpanlar kabaca yaklaşımdır; gerçek bir P90 saatlik üretim verisi ve kaynak
belirsizliği analizinden gelir.

---

## 3. Fiyat

Bir işletme yılında gerçekleşen fiyat:

- **İhale/PPA süresi içindeyse:** `auction_price × (1 + auction_escalation)^j`
- **Dışındaysa:** `piyasa_fiyatı(yıl) × capture_rate`

Piyasa fiyatı iki şekilde belirlenir:

- `FLAT`: `flat_price × (1 + price_escalation)^(yıl − base_year)`
- `CURVE`: CSV'den okunan yıl→fiyat eşlemesi. Eğrinin **bittiği yıldan sonrası**
  `price_escalation` ile uzatılır; **başlamadığı yıllardan öncesi** ilk değerde
  sabit tutulur.

**Yakalama oranı (capture rate)**, güneşin ürettiği saatlerdeki ortalama fiyatın
yıllık baz yük ortalamasına oranıdır. Güneş penetrasyonu arttıkça düşer;
modelde tek bir sabit katsayıdır (bkz. Bilinen Sınırlar).

---

## 4. Gelir kalemleri

| Kalem | Formül |
|---|---|
| Elektrik | $E_j \times p_j$ |
| Depolama | `storage_energy_mwh × cycles × RTE × spread × (1+i)^j` |
| Agri-PV | `agri_revenue_per_ha × land_area_ha × (1+i)^j` |

Depolama modeli kasıtlı olarak basittir: sabit bir yıllık çevrim sayısı ve sabit
bir arbitraj marjı varsayar. Şebeke kısıtı, kapasite piyasası geliri veya
dispatch optimizasyonu içermez.

---

## 5. İşletme giderleri

`DETAIL` modunda:

$$
\text{OPEX}_j = \big(F + m \cdot P + v \cdot E_j\big)(1 + e)^j + R_j
$$

$F$ sabit gider, $m$ MWp başına O&M, $v$ MWh başına değişken gider, $e$ OPEX
eskalasyonu.

**Arazi kirası** $R_j$ iki modda hesaplanır:

- `SIMPLE`: $R_j = R_0 (1 + e_R)^j$
- `MAX_BASE_OR_ROYALTY`: $R_j = \max\big(R_0 (1 + e_R)^j,\; \rho \cdot \text{Elektrik geliri}_j\big)$

İkinci mod İtalya ve İspanya'daki arazi sözleşmelerinde yaygındır: arazi sahibi
taban bir kira garantisi alır ama fiyatlar yükselirse cirodan pay alır.

---

## 6. CAPEX ve finansman

**Toplam CAPEX** birim maliyetlerden hesaplanır ve beklenmeyen gider payıyla
çarpılır. İnşaat yıllarına `capex_schedule` ağırlıklarıyla dağıtılır.

Her inşaat yılı için:

```
çekim        = harcama × debt_ratio
komisyon     = çekim × upfront_fee
IDC          = (açılış bakiyesi + çekim × 0,5) × faiz      ← yıl ortası konvansiyonu
```

`capitalize_idc` açıksa IDC anaparaya eklenir ve özkaynak yalnızca
`harcama − çekim + komisyon` öder. Kapalıysa IDC'yi özkaynak nakden öder.

### 6.1 Sabit kaldıraç (`GEARING`)

Klasik yaklaşım: borç tutarı CAPEX'in sabit bir yüzdesidir ve vade boyunca
**eşit taksitli anüite** ile geri ödenir:

$$
\text{PMT} = D \cdot \frac{r(1+r)^n}{(1+r)^n - 1}
$$

Basit ama gerçekçi değil. Üretimi bozunan ve fiyatı düşen bir varlıkta sabit
taksit, ilk yıl minimum DSCR verip sonraki yıllarda gereksizce yükselir. Yani
kredi, ilk yılın kısıtına göre boyutlanmış olur ve sonraki yılların taşıyabileceği
borç kapasitesi boşa gider.

### 6.2 Hedef DSCR'a göre şekillendirme (`SCULPTED_DSCR`)

Modern proje finansmanının fiilî standardı. Mantık terse çevrilir: **kaldıraç bir
girdi değil, DSCR kısıtından çıkan bir sonuçtur.**

Her yıl için hedef borç servisi:

$$
DS_j = \frac{\text{CFADS}_j}{\text{DSCR}_{\text{hedef}}}
$$

Kredi tutarı bu ödemelerin kredi faiziyle bugünkü değeridir:

$$
D = \sum_{j=0}^{n-1} \frac{DS_j}{(1+r)^{j+1}}
$$

Bu tanım gereği plan krediyi vade sonunda **tam olarak** kapatır. Kanıt: bakiye
özyinelemesi $B_{j+1} = B_j(1+r) - DS_j$ olduğundan

$$
B_n = (1+r)^n \left[ D - \sum_j \frac{DS_j}{(1+r)^{j+1}} \right] = 0
$$

#### Döngüsellik ve çözüm

CFADS vergiden sonraki nakittir; vergi faiz giderine bağlıdır; faiz kredi
tutarına bağlıdır; kredi tutarı da CFADS'e. Üstelik kapitalize inşaat faizi ve
komisyonlar amortisman tabanını, o da yine vergiyi değiştirir. Bu döngü kapalı
formda çözülemez.

Model bunu **sabit nokta iterasyonuyla** çözer: bir kaldıraç tahminiyle modeli
çalıştır, çıkan CFADS'ten yeni kredi tutarını ve planını hesapla, tekrarla.
Vergi kalkanı etkisi ikinci mertebeden olduğu için tipik olarak 5–7 adımda
yakınsar. Yakınsama testi hem kredi *tutarına* hem de geri ödeme *profiline*
bakar; yalnızca tutara bakmak, kaldıraç tavanına takılan senaryolarda erken
durmaya ve DSCR'ın düz çıkmamasına yol açar.

#### Kaldıraç tavanı

`max_gearing`, DSCR ne kadar müsaade ederse etsin aşılmayacak bir üst sınırdır
(COD'daki kredi bakiyesinin toplam CAPEX'e oranı). Tavan bağlayıcı olduğunda
plan orantılı olarak küçültülür; DSCR profili düz kalır ama seviyesi hedefin
**üstüne** çıkar. Bu doğru davranıştır: kısıt artık DSCR değil, tavandır.

#### Nasıl doğrulanıyor

Testler şunları sınar: kapaklanmamış her hedefte DSCR'ın 1e-6 toleransla düz ve
hedefte olması, borcun vade sonunda tam sıfırlanması, kredi tutarının borç
servisinin bugünkü değerine eşit olması, `debt_ratio` girdisinin sonucu
değiştirmemesi (çünkü artık yalnızca başlangıç tahmini), ve aynı minimum DSCR'da
sculpting'in anüiteden daha fazla borç taşıması.

---

## 6.3 Bakım yatırımı ve rezerv hesabı

Paneller 30+ yıl dayanır, invertörler tipik olarak 12–15 yılda değişir. Bu
yüzden model, belirlenen işletme yıllarında bir **yenileme yatırımı** işler:

$$
\text{Yenileme}_j = c_{\text{inv}} \times P \times (1 + i)^{C + j}
$$

Maliyet baz yıl parasıyla girilir ve gerçekleşme yılına enflasyonla taşınır.
Yatırım **aktifleştirilir** (gider yazılmaz) ve kalan ömür ile
`inverter_depreciation_years` süresinin kısası boyunca doğrusal itfa edilir.

### Neden rezerv hesabı gerekiyor

Toplu bir harcama, gerçekleştiği yıl CFADS'i siler. Varsayılan projede
rezervsiz senaryoda o yılın DSCR'ı **-0,20x**'e düşer — yani nakit akışı borç
servisini karşılamaz. Gerçek kredi sözleşmeleri bu yüzden bir **bakım rezerv
hesabı** (MRA, Maintenance Reserve Account) şart koşar: para harcamadan yıllar
önce biriktirilir.

Model bunu şöyle işler: maliyet, harcamadan önceki `maintenance_reserve_years`
yıla eşit olarak yayılır ve nakit akışını **rezerve katkı** olarak etkiler.
Harcamanın kendi yılında nakit çıkışı olmaz; para rezervden karşılanır.

$$
\text{CFADS}_j = \text{EBITDA}_j - \text{vergi}_j - \text{rezerv katkısı}_j
$$

Toplam tutar değişmez, yalnızca zamanlaması değişir — testler katkıların
toplamının harcamaya kuruşu kuruşuna eşit olduğunu doğrular. Nakit daha erken
çıktığı için NPV bir miktar düşer; bu doğru sonuçtur.

Yenileme çok erken bir yıla düşerse (biriktirecek önceki yıl yoksa) harcama
kendi yılında nakitten çıkar.

### Sculpting ile etkileşim

İki özellik birlikte doğru davranır: rezerv yıllarında CFADS düştüğü için
sculpting o yıllarda daha az borç servisi planlar ve DSCR hedefte **düz**
kalır. Rezerv dönemindeki borç servisi, sonraki yıllardakinden belirgin
şekilde düşüktür. Bu, modelin kredi kapasitesini gerçekçi biçimde
sınırlandırdığı anlamına gelir.

**LCOE'ye etkisi:** Yenileme maliyeti LCOE'ye girer (ekonomik bir maliyettir),
ama rezervin zamanlaması girmez (finansman mekaniğidir). Test bunu doğrular:
rezerv süresi 0 ile 8 arasında değişirken LCOE sabit kalır.

---

## 7. Vergi

$$
\text{EBT}_j = \text{EBITDA}_j - \text{Amortisman}_j - \text{Faiz}_j
$$

**Amortisman tabanı** = toplam CAPEX − arazi + kapitalize IDC + komisyonlar.
Arazi amortismana tabi değildir; bu, orijinal prototipte gözden kaçan bir
noktaydı ve toplam CAPEX üzerinden amortisman ayırmak vergiyi olduğundan düşük
gösterir.

**Zarar mahsubu** açıksa negatif EBT bir havuza yazılır ve sonraki kârlı
yıllarda FIFO sırasıyla kullanılır. `loss_carryforward_limit > 0` ise belirtilen
yıldan eski zararlar kullanılamaz hale gelir (birçok ülkede böyle bir sınır
vardır; Türkiye'de 5 yıl, İtalya'da süresiz ama %80 tavanlı).

> **Sınır:** Model %80 tavan gibi *oransal* mahsup kısıtlarını uygulamaz,
> yalnızca süre sınırını uygular.

---

## 8. Nakit akışı ve göstergeler

```
EBITDA  = Toplam gelir − OPEX
CFADS   = EBITDA − vergi − bakım rezervi katkısı
DSCR    = CFADS / borç servisi
FCFE    = CFADS − borç servisi + ömür sonu kalemleri
```

İnşaat yıllarında `FCFE = −özkaynak katkısı`.

| Gösterge | Tanım |
|---|---|
| **Özkaynak IRR** | FCFE serisini sıfırlayan iskonto oranı |
| **NPV** | FCFE'nin **özkaynak maliyeti (Ke)** ile bugünkü değeri |
| **Min. DSCR** | Bütün borçlu yıllar içindeki en düşük DSCR |
| **Geri ödeme** | Kümülatif FCFE'nin sıfırı geçtiği dönem (yıl içi interpolasyonlu) |

**Neden Ke, WACC değil?** FCFE zaten borç servisi düşülmüş, yani yalnızca
özkaynağa ait bir akıştır. Bunu WACC ile iskonto etmek borcun etkisini iki kez
saymak olur. WACC, borç servisi düşülmemiş FCFF ile birlikte kullanılır.

---

## 9. LCOE

$$
\text{LCOE} = \frac{\sum_t \dfrac{\text{CAPEX}_t + \text{OPEX}_t + \text{Söküm}_t}{(1+r)^t}}{\sum_t \dfrac{E_t}{(1+r)^t}}
$$

Burada $r$ = `lcoe_discount_rate` (genelde WACC).

Üç noktaya dikkat:

1. **Faiz, komisyon ve vergi LCOE'ye girmez.** Sermaye maliyeti zaten iskonto
   oranının içindedir; ayrıca eklemek çifte sayım olur.
2. **Üretim de iskonto edilir.** Bu sezgiye aykırı görünür ama LCOE'nin
   tanımının bir parçasıdır — enerjiye değil, enerjinin gelir yaratma zamanına
   göre ağırlık verilir.
3. **Nominal LCOE.** Nominal maliyet ve nominal iskonto oranı kullanıldığı için
   sonuç nominaldir ve reel LCOE'den yüksek çıkar.

Testler bu üç özelliği doğrular: LCOE fiyattan ve kaldıraçtan bağımsızdır.

---

## 10. Duyarlılık analizi

**Tornado**, her parametreyi tek başına yukarı ve aşağı oynatıp seçilen
göstergedeki değişimi ölçer. Değişkenler toplam mutlak etkiye göre sıralanır.

Oynatma büyüklükleri kasıtlı olarak farklıdır: oransal parametreler ±%10,
oranlar ise ±100 baz puan. "Faizi %10 arttırmak" (%6,5 → %7,15) finansal olarak
anlamsız bir cümledir; kredi faizi baz puanla konuşulur.

**Senaryolar** çok değişkenli: Base / High / Low fiyat, P90 üretim ve
"düşük fiyat + P90" stres birleşimi.

---

## 11. Bilinen sınırlar

Bu model bilinçli olarak aşağıdakileri **yapmaz**:

| Eksik | Neden önemli |
|---|---|
| Yarı yıllık / üç aylık periyot | Gerçek modeller altı aylık çalışır; borç servisi altı aylıktır ve DSCR her hesap döneminde ayrı ölçülür |
| Saatlik dispatch ve yakalama oranı | Tek yıllık katsayı, güneş penetrasyonu arttıkça bozulan bir yaklaşımdır |
| Zamanla eriyen capture rate | 2045'teki yakalama oranını 2027'yle aynı varsaymak iyimserdir |
| Birinci yıl LID degradasyonu | Pratikte 1. yıl ~%2, sonrası ~%0,45; model tek bir sabit oran kullanır |
| DSRA ve nakit süpürme | Kredi sözleşmelerinin standart parçaları; özkaynağa dağıtımın zamanlamasını değiştirir |
| İşletme sermayesi ve alacak/borç dönüşü | Yıllık modelde etkisi küçük ama sıfır değil |
| KDV, damga vergisi, yerel vergiler | Ülkeye çok bağımlı |
| Enflasyon oynaklığı, kur riski | Tek bir sabit enflasyon oranı varsayılır |
| Şebeke kısıtı (curtailment) | Bazı piyasalarda üretimin %2-10'unu silebilir |
| Monte Carlo | Duyarlılık analizi tek değişkenli; korelasyonları yakalamaz |
| Repowering / ömür uzatma opsiyonu | Ömür sonu tek bir hurda değeri olarak modellenir |
| Amortismanın hızlandırılmış yöntemleri | Yalnızca doğrusal amortisman |

Ayrıca **IRR uyarısı:** Nakit akışı birden fazla kez işaret değiştiriyorsa
(örneğin sonda büyük bir söküm maliyeti varsa) matematiksel olarak birden çok
IRR olabilir. `finance.irr()` bulduğu ilk kökü döner. Böyle durumlarda NPV daha
güvenilir bir göstergedir.

---

## 12. Excel çıktısı

Rapor üç sekmelidir: **Özet**, **Nakit Akışı**, **Varsayımlar**.

### Ne formül, ne değil

| Satır tipi | Nasıl yazılır | Neden |
|---|---|---|
| Gelir, EBITDA, EBT, CFADS, DSCR, FCFE, kümülatif | **Formül** | Aralarındaki ilişki basit aritmetiktir; kullanıcı bir girdiyi düzeltince aşağısı takip etmeli |
| IRR, NPV, LCOE, min/ort DSCR | **Formül** | Excel'in kendi `IRR`/`NPV` fonksiyonlarıyla, iskonto oranı bir hücreden okunarak |
| Üretim, fiyat, OPEX, amortisman, vergi, borç servisi | **Değer** | Zarar mahsubu havuzu, ciro payı kirası ve DSCR'a göre iterasyonla çözülen borç planı kapalı formda tablo formülüne çevrilemez |

Bu ayrım dosyanın içinde de yazılıdır. Kitap, hesaplanmış bir senaryonun
raporudur; motorun Excel'e yeniden yazılmış hâli değildir.

### İskonto konvansiyonunun Excel karşılığı

Excel'in `NPV()` fonksiyonu ilk elemanı da iskonto eder, bizim konvansiyonumuz
t=0'ı iskonto etmez. Bu yüzden formül şu şekilde yazılır:

```
= <t=0 hücresi> + NPV(oran; <t=1 .. t=n aralığı>)
```

Aynı yaklaşım LCOE'nin pay ve paydası için de kullanılır.

### Biçim konvansiyonları

Standart finansal model pratiği: girdiler **mavi**, formüller **siyah**,
sekmeler arası bağlantılar **yeşil**, kilit varsayımlar **sarı dolgulu**.
Negatifler parantezli, sıfırlar tire, binlik ayraçlı. Yüzdeler kesir olarak
saklanır (0,09 → %9,0), katsayılar `0,00x`, yıllar metin olarak yazılır ki
`2.026` diye biçimlenmesin.

### Nasıl doğrulanıyor

openpyxl formülleri düz metin olarak yazar ve hiçbir şey doğruluklarını
kontrol etmez. Bir sütun kayması ya da yanlış satıra referans, tamamen
hatasız görünen ama yanlış sayılar üreten bir dosya verir.

Bu yüzden testler dosyayı **LibreOffice ile gerçekten hesaplatır** ve çıkan
her sayıyı motorunkiyle karşılaştırır. Testlerden biri iskonto oranını
%9'dan %14'e çekip yeniden hesaplatır ve yeni NPV'nin, motoru %14 ile
çalıştırmakla aynı sonucu verdiğini doğrular. Bir diğeri LCOE'nin özkaynak
maliyetinden etkilenmediğini sınar.

---

## 13. Doğrulama

`solarfi/finance.py` içindeki fonksiyonlar `numpy-financial` ile 8 ondalık
basamağa kadar karşılaştırılarak doğrulanmıştır. Kendi uygulamamızı kullanmamızın
sebebi bağımlılığı azaltmak ve IRR çözülemediğinde `nan` yerine `None` dönerek
arayüzde "nan%" yazmasını engellemektir.

Test paketi (`tests/`) 295 test içerir. Motor testleri (185) şunları sınar:

- kapalı form anüite değerleri,
- IRR'de NPV'nin sıfır olması,
- elle hesaplanmış LCOE,
- kredi bakiyesinin vade sonunda tam sıfırlanması,
- `CFADS = EBITDA − vergi` ve `FCFE = CFADS − borç servisi` özdeşliklerinin her dönemde tutması,
- arazinin amortismana girmemesi,
- LCOE'nin fiyattan ve kaldıraçtan bağımsız olması,
- girdi doğrulamasının imkânsız değerleri reddetmesi.

Arayüz testleri (53 adet, `tests/test_app.py`) uygulamayı Streamlit'in `AppTest`
aracıyla tarayıcısız çalıştırır ve her modun, her onay kutusunun, her fiyat
kaynağının ve her üretim senaryosunun hatasız açıldığını doğrular. Ayrıca
davranışsal beklentileri de sınar: ihale açılınca getirinin artması, ciro payı
kirasının getiriyi düşürmesi, P90'ın P50'den kötü çıkması ve bozuk bir CAPEX
takvimi girildiğinde uygulamanın uyarı verip eşit dağılıma dönmesi.

Excel çıktısı testleri (57 adet, `tests/test_export.py`) yapıyı ve biçimi
kontrol eder; LibreOffice mevcutsa dosyayı hesaplatıp bütün sayıları motorla
karşılaştırır. LibreOffice yoksa bu grup atlanır, yapı testleri yine koşar.

---

## 14. Arayüzün görsel dili

Renk ve tipografi kararları konunun kendi dünyasından türetildi:

* **Panel mavisi** (`#10243E`) — monokristal silisyum hücrenin derin, hafif
  mor mavisi. Bütün yapısal renk buradan gelir.
* **Kehribar** (`#C8892B`) — güneşlenmenin rengi. Tek vurgu rengidir ve kıt
  kullanılır: eylem düğmeleri, seçili durum, DSCR göstergesi.
* Zemin soğuk gri-mavi; sıcak krem bilinçli olarak elendi. Bu bir ölçüm
  aracı, editoryal bir sayfa değil.

Yazı tipi **IBM Plex**: teknik dokümantasyon için tasarlandı ve `Plex Mono`
kesiti tabular figürler verir. Sayıların tabular olması burada süs değil
işlev — bir nakit akışı tablosunda rakamlar sütun sütun hizalanmazsa göz
karşılaştırma yapamaz.

Bütün sayılar Türkçe biçimlenir: binlik ayracı nokta, ondalık virgül, yüzde
işareti başta (`%16,4`, `7,0 M€`, `1,53x`). Bu biçimlendirme testlidir,
çünkü hataları sessizdir: yanlışlıkla iki kez ölçeklenmiş bir yüzde
(`%1638,0`) ya da `2.026` diye biçimlenmiş bir yıl, kimse fark etmeden
rapora girer.

### İmza öğesi: DSCR şeridi

Minimum DSCR göstergesinin altında, rakamın kredi verenlerin eşiklerine
(1,20x ve 1,35x) göre nerede durduğunu gösteren yatay bir ölçek vardır.

Sebebi şu: bir DSCR sayısı tek başına anlamsızdır. `1,22x` iyi mi kötü mü,
ancak eşiğe olan mesafesiyle anlaşılır — ve bu projenin bütün meselesi de
odur. Şerit üç bölgeyi ayırır: finanse edilemez, sınırda, rahat.
