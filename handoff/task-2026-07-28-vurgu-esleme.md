# Görev — VURGU'yu eşlemeye al (`tire-on`)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Karar ve dayanağı

Üç tur ölçüm + üç tur kulak testi sonunda kullanıcı **`tire-on`** yolunu seçti:
**hedef kelimenin YALNIZ ÖNÜNE** işaret, arkasına hiçbir şey.

Kulak sonucu (örnek başına karar, 3 örnek):

| cümle | `kombo` (iki yanlı) | `kombo-on` | **`tire-on`** |
|---|---|---|---|
| affection "olur mu" | 0/3 | 2/3 | **3/3** |
| arousal "on dakikaya" | 3/3 ama **bekleme 3/3** | 0/3 | 0/3 |
| awe "tek başına" | 3/3 ama **bekleme 3/3** | 0/3 (yanlış sınır) | 0/3 (yanlış sınır) |
| confusion "anlamadım" | 3/3 ama **bekleme 3/3** | **3/3** | **3/3** |

Kabul edilen gerçek: **her cümlede tutmuyor, yaklaşık yarısında tutuyor.** Kullanıcı
bunu bilerek seçti çünkü **başarısızlık zararsız**: tutmadığında ses bozulmuyor,
yalnız vurgu gelmiyor. Dört turda da WER 0.000, bekleme yok, cümle başı yenmiyor.

## ⚠️ Yerleşim dersi — `awe` vakası

Önceki tur `awe` cümlesinde sınırı "hem de"nin ÖNÜNE almıştı (gerekçe: Türkçede
sınırdan önceki kelime öne çıkar, "hem de" bağlı pekiştirici). Kullanıcı o hâle
**0/3**, eski sınırla üretilen `kombo-on-dar`a **3/3** verdi. Ölçüm de eski sınırı
üstün görüyordu (Δ perde +2.17 vs −0.72).

**Kural: işaret hedef kelimenin TAM ÖNÜNE konur.** Geriye doğru bağlı öbek/pekiştirici
kapsayacak biçimde genişletilmez. Basit olan kazandı; `bagli` alanı mantığını
canlıya TAŞIMA.

## Yapılacaklar

### 1. Etiket ve dönüşüm

* Modelin yazacağı bir işaret tanımla (mevcut düzene uy: `[laughter]`, `[pause]`,
  `[mood:X]` var — bunlarla tutarlı bir ad seç, ör. `[vurgu]` ya da `[emphasis]`;
  seçimini gerekçelendir, Türkçe prompt'ta hangisi doğal duruyorsa).
* İşaret **hedef kelimenin hemen önünde** durur ve dönüşümde deneydeki **birebir aynı
  dizgeye** çevrilir. `experiments/vurgu/vurgu_set.py::_tireli()` ne üretiyorsa onu
  kullan (öndeki parça), tahminle yazma — oku ve birebir eşle.
* Doğrulama: dönüşümden çıkan metin, ölçümde kullanılan `tire-on` metniyle **birebir
  aynı** olmalı. Bir örnekle kanıtla (testte assert).

### 2. Koruma kuralları

* **Tire seslendirilmiyor** (WER 0.000) ama yine de regresyon testiyle kilitle.
* Cümle başına yakınlık: `pause` için ölçülmüş `_MIN_WORDS_BEFORE_PAUSE = 3` kuralı
  var (erken duraklama ilk kelimeyi yiyor). Vurgu işareti için de aynı risk var mı
  BAK; ölçüm verisi yoksa aynı muhafazakâr kuralı uygula ve gerekçesini yaz.
  **Şüphede kalırsak vurguyu kaybederiz, ilk kelimeyi asla.**
* Bir cümlede **en fazla bir** vurgu işareti. Model birden fazla koyarsa ilki
  kalsın, gerisi silinsin — çok vurgu vurgusuzluktur.
* Tanınmayan/yanlış yerleştirilmiş işaret **silinir** (mevcut garanti bozulmasın).
* `_READABLE`'a karşılığını ekle (anlatılan-etiket durumu: "vurgu").

### 3. Prompt

`pi/AGENTS.md` ve gerekiyorsa `pi/personas/candan.md`: Candan bu işareti **ne zaman**
kullanacağını bilsin — cümlenin anlamının bir kelimeye asıldığı yerler
("**tek başına** yaptın", "**yarın** değil bugün"). Her cümlede kullanmasın.

⚠️ **Prompt satır sayısını şişirme** — ekliyorsan başka yeri kısalt, net değişimi raporla.
⚠️ Yetenek listesi koddaki eşlemeyle **birebir** kalsın (bugünün dersi: uyuşmazlık
"yetenek yalanı" üretiyor).

### 4. Test + deploy

* Dönüşüm testleri, koruma kuralları, "tire okunmuyor" regresyonu.
* Şu an **378 test** geçiyor.
* Deploy: yedek → gönder → import doğrula → **yalnız `candan-worker` restart** →
  log → md5. ⚠️ `pi-service`, `higgs-tts`, `candan-brain`'e DOKUNMA.
  **AMA:** prompt dosyası değiştiyse `systemctl reload pi-service` ŞART — 27 Tem'de
  öğrenildi: `pi/AGENTS.md` sürece doğarken veriliyor, reload olmadan bayat kalıyor
  (`handoff/2026-07-28-...` ve DEVİR'de yazılı). `reload` restart DEĞİL, worker düşmüyor.

## Belgeleme

`handoff/2026-07-28-vurgu-esleme.md`: seçilen etiket ve gerekçesi, yerleşim kuralı
(`awe` dersi dahil), koruma kuralları, "yarı cümlede tutuyor" gerçeği açıkça yazılsın —
ileride biri "neden hep çalışmıyor" diye sormasın. DEVİR'e madde; başka ajanların
maddelerini SİLME. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Seçilen etiket, dönüşümün ölçülen metinle birebir olduğunun kanıtı
* Koruma kuralları ve gerekçeleri
* Prompt'ta net kaç satır değişti
* Test sayısı, deploy sonucu (pi reload dahil), tek blok geri dönüş, commit hash
