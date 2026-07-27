# Duygu atlası — her Higgs token'ı, kendi duygusuna uyan cümlede

Ayrı ve **uzun soluklu** bir proje; yavaş geliştiriliyor. Burası **1. tur**.

## Neden var

Bugüne kadarki ölçüm (`experiments/higgs-tts3/token_probe.py` + `token_eval.py`,
43 token · 528 wav) **"anlaşılıyor mu"** sorusunu cevapladı: WER, boş çıktı, baş
yeme. Cevaplamadığı soru: **"doğru DUYULUYOR mu?"**

Şaşırma vakası bunu kanıtladı: `<|emotion:surprise|>` ölçümde 12/12 tertemizdi
(WER 0.000) ama kulakta hiç şaşkın duyulmuyordu — kullanıcı dinleyince canlıda
`<|emotion:awe|>`e geçildi (`handoff/2026-07-27-duygu-katmani.md` §7b).

İkinci sorun: şimdiye kadar her token **aynı nötr cümlede** ("Bugün harika bir
haber var.") dinlendi. Bir duygu, ona uymayan bir cümlede zaten kendini
gösteremez. Bu atlasta her token **kendi duygusuna uyan** bir Türkçe cümlede
duyuluyor; duygu cümlenin İÇERİĞİNDE de var ki token'ın katkısı ayırt edilebilsin.

## Nasıl koşulur

```bash
cd experiments/duygu-atlasi
python3 atlas_set.py            # 44 satır × 2 (etiketli + düz) = 88 wav
```

* Ses **canlı streaming ucundan** alınır: `POST /api/tts/stream`, `192.168.0.25:8809`.
  Takım yeniden yazılmadı, içe aktarıldı (`../higgs-tts3/token_probe.py::synth`, `_wav`).
  Deney koşumu yanıltıyor — eski `elation` "bozuk" teşhisi tam bundan çıkmıştı.
* **Sunucuya hiçbir dokunuş yok**, yalnız HTTP isteği.
* Koşum **kaldığı yerden devam eder**: var olan wav yeniden üretilmez
  (`--yenile` ile zorlanır). Tek satır için: `--only emotion_awe`.
* `out/` git'te yok (kök `.gitignore`), wav'lar yerelde durur (~14 MB).

## Nasıl dinlenir

```bash
./serve.sh          # http://localhost:8010/duygu-atlasi.html
```

Her satırda **etiketli** ve **düz** (etiketsiz, aynı cümle) iki oynatıcı yan yana —
fark yalnız token'dan geliyor. Süre ve Δsüre yazılı; rozet "canlıda kullanılıyor /
kullanılmıyor / ölçülmedi" diyor.

**Kulak notu:** her satırda *iyi / idare eder / kötü* + serbest not var. Notlar
`localStorage`'a yazılır (sunucuya/dosyaya YAZILMAZ). Sayfanın altındaki
**"notları JSON kopyala"** düğmesiyle alınıp bir sonraki tura getirilir.

## Yerleşim kuralları (ölçülmüş)

`handoff/2026-07-27-duygu-katmani.md` §4:

* `emotion` / `style` / `prosody`-önek → cümle **başında, bitişik** (boşluksuz).
* `sfx` → taklide bitişik: `<|sfx:laughter|>Haha, …`.
* `pause` / `long_pause` → cümlenin **ortasında**, iki yanı **boşluksuz**, en az
  **3 kelimeden sonra**. Boşluklu hâli 12'de 3 kez ilk kelimeyi yedi; cümle başına
  yakın hâli 24'te 2 kez. Ortada ve bitişik: 24/24 temiz.

## Kapsam

| kategori | adet | not |
|---|---|---|
| emotion | 21 | katalogda ne varsa; `anger/disgust/fear/shame` gibi canlıda kullanılmayanlar da var — bu bir **atlas**, canlı eşleme değil |
| prosody | 10 | 8 cümle başı + `pause`, `long_pause` (satır içi) |
| style | 3 | `singing`, `shouting`, `whispering` |
| sfx | 2 | `laughter`, `sigh` — canlıda zaten kullanılıyor |
| **kombo** | 8 | ⚠️ **ÖLÇÜLMEDİ** |

⚠️ **Kombolar ölçülmemiştir.** Yalnız bu atlasta dururlar. Beğenilen bir kombo
canlıya girmeden önce `token_probe.py` / `token_eval.py` turu şart —
kural: **ölçülmemiş token canlıya girmez**. Bu turda `worker/higgs_tts.py`
eşlemesine hiçbir şey eklenmedi.

## Turların kaydı

### 1. tur (27 Tem 2026)

* 44 satır × 2 = **88 wav** üretildi, hepsi canlı streaming ucundan. Boş/kırpık yok.
* Katalogdaki 43 token'ın tamamı + 8 tadımlık kombo.
* Sayfa: kategorilere ayrılmış, açılır başlıklı, kategori içi numaralı,
  kulak notu + JSON dışa aktarma.
* Üretim sırasında **1 kez HTTP timeout** oldu (`sfx:laughter`, 120 s); koşum
  kaldığı yerden tekrar başlatıldı, sorun tekrarlamadı. Sunucuya müdahale yok.
* Kulak kararı **verilmedi** — dinlemeyi kullanıcı yapıyor.

### Sırada ne var (2. tur)

1. Kullanıcı boş zamanında hepsini dinler, her satırı işaretler, **notları JSON
   olarak** getirir.
2. O JSON'a göre: hangi token gerçekten duygusunu taşıyor, hangisi tabandan
   farksız? "İyi" çıkan ama canlıda kullanılmayanlar aday listesine girer.
3. Beğenilen **kombo** varsa önce `token_probe.py` turu, sonra karar.
4. Zayıf çıkan satırlarda **cümle suçu mu token suçu mu** ayrılır: aynı token
   ikinci bir uygun cümlede tekrar üretilir.
