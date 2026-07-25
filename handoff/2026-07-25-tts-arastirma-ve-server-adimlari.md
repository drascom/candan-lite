# TTS / Türkçe ses kalitesi — araştırma sonuçları ve SUNUCUDA yapılacaklar

**Tarih:** 2026-07-25
**Amaç:** OmniVoice'un Türkçesindeki aksan / bozuk kelime / akıcılık sorununu düzeltmek.
**Bu oturumda sunucuya HİÇBİR yazma isteği gönderilmedi.** Ölçümlerin tamamı MacBook M4 Pro'da,
lokal bench'te yapıldı: `experiments/tts-local-bench/` (24 set, 294 wav, `compare.html` ile
dinleme sayfası).

Kod tarafı için ayrıca: `handoff/2026-07-25-trnorm-production.md`.

---

## 1. SUNUCUDA YAPILACAKLAR (kullanıcı kendisi yapacak)

### 1.A — trnorm'u canlıya al  ✅ ONAYLANDI, kod hazır

Değişiklik **serverda değil, worker tarafında.** `.25`'teki OmniVoice'a ve
`bridge_server.py` / `pronounce_tr.json`'a dokunulmuyor.

Akış: `pi beyin → metin → [worker: mood ayıkla → normalize_tr() → gönder] → OmniVoice (.25)`

Repo'da hazır olanlar:
- `worker/trnorm.py` (YENİ, stdlib-only, yeni bağımlılık yok)
- `worker/omnivoice_tts.py` (`normalize_tr()` çağrısı eklendi)

**Yapılacak:** worker sürecini yeniden başlat. Bu oturumda deploy/restart YAPILMADI.

**Deploy'dan önce salt-okuma kontrolü (önerilir):** `.25:/opt/omnivoice/pronounce_tr.json`
içinde (a) sayı/yüzde ile ilgili giriş var mı — artık gereksiz, hatta "yüzde yüzde" gibi çift
uygulama üretebilir; (b) trnorm'un artık ürettiği yeni kelimelerle (`yüzde`, `bin`, `yüz`, `on`,
`otuz`, `lira`, `virgül`, ay adları) zayıf kelime-sınırıyla eşleşen giriş var mı. Ayrıntı ve
gerekçe: `handoff/2026-07-25-trnorm-production.md` §"pronounce_tr.json çakışma riski".
(`handoff/2026-07-16.md`'de tam bu tür bir sınır hatası kayıtlı — `on` gibi kısa tokenlara dikkat.)

### 1.B — Pinned referansı ~4 saniyeye kısalt  ⏳ ONAY BEKLİYOR

Ölçüm: referans uzunluğu maliyeti neredeyse doğrusal belirliyor ve **kısa referans kaliteyi
düşürmüyor, hatta artırıyor.**

| Referans | RTF | Set-içi tutarlılık | Kaynağa sadakat |
|---|---|---|---|
| yok (auto) | 0.86 | 0.481 | 0.340 |
| **3.90 sn** | **1.66** | **0.815** | **0.841** |
| 6.30 sn | 2.25 | 0.764 | 0.829 |
| 11.57 sn | 3.52 | 0.744 | 0.824 |

(Tutarlılık = `worker/models/campplus.onnx` ile ikili kosinüs benzerliği. Metrik doğrulandı:
farklı konuşmacı → 0.159, yani ölçüt konuşmacıyı gerçekten ayırt ediyor.)

Mevcut canlı durum (`GET http://192.168.0.25:8808/api/default`):
```
ref_audio: /opt/omnivoice/default-ref.wav
ref_text:  "Merhaba, bu bir Türkçe seslendirme testidir.
            VoxCPM 2 ile uzun kitapları sesli kitaba dönüştürebilirsiniz."
```
`ref_text` iki cümle → wav ~10 sn civarı olmalı. Yani kısaltma tek başına ~2× hızlanma demek.

**İki seçenek var, karıştırmayın:**

**Seçenek A — sesi KORU, sadece kısalt** (muhafazakâr, önerilen ilk adım)
`default-ref.wav`'ın ilk ~4 saniyesini **cümle sonunda** kes ve `ref_text`'i kesilen kısma göre
düzelt (ör. yalnız "Merhaba, bu bir Türkçe seslendirme testidir."). Ses kimliği aynı kalır,
hız ~2× artar.
⚠️ `ref_text` ile ses birebir örtüşmezse kalite düşer — F5-TTS'te tam bu yüzden referans sızması
yaşadık. Kesme noktasını ve metni mutlaka hizala.

**Seçenek B — sesi DEĞİŞTİR** (bench'te beğenilen ses)
```
ref_audio: experiments/tts-local-bench/refs/omnipick_3s.wav   (3.90 sn, 24 kHz mono)
ref_text:  "Dün akşam eve döndüğümde, kapının önünde bıraktığın notu görünce çok şaşırdım."
```
Bu, OmniVoice'un auto modda kendi seçtiği erkek ses. Kullanıcı bench'te beğendi.
**Candan'ın ses kimliği DEĞİŞİR** — bu bir hız optimizasyonu değil, sesi değiştirme kararı.

**Uygulama (her iki seçenek için aynı):**
```bash
# 1) YEDEK AL (geri dönüş için şart)
ssh <.25>  'cp /opt/omnivoice/default-ref.wav /opt/omnivoice/default-ref.wav.bak-20260725'
curl -s http://192.168.0.25:8808/api/default   # eski ref_text'i kaydet

# 2) YENİ REFERANSI YAZ
curl -X POST http://192.168.0.25:8808/api/set_default \
  -F "ref_audio=@<yeni-referans>.wav" \
  -F "ref_text=<kesite birebir uyan metin>"

# 3) DOĞRULA
curl -s http://192.168.0.25:8808/api/default

# GERİ DÖNÜŞ: aynı POST'u yedek wav + eski ref_text ile tekrarla
```

**Karar öncesi:** `omnipick-clone-3s` setini `compare.html`'den dinle — ölçüm iyi diyor ama
4 sn'lik referansın sesin karakterini koruduğunu kulakla teyit et.

---

## 2. ÖLÇÜLEN SONUÇLAR — tekrar denemeye gerek yok

### İşleyen tek müdahale: kendi metin normalizasyonumuz
| | WER | Atlanan kelime |
|---|---|---|
| OmniVoice dahili `normalize_text=True` | %35.1 | 61 |
| **`trnorm.py`** | **%6.3** | **9** |
| *taban (insan sesi, Whisper hatası)* | *%0.0* | *0* |

Sebep: OmniVoice, Çince/İngilizce dışındaki dillerde `num2words` ile **yalnız çıplak tam
sayıları** çeviriyor → `%25`, binlik ayıraçlı `3.500`, kesme ekli `14:30'da` ham kalıyor.
Hazır Türkçe normalizer kütüphanesi PyPI'da **yok** (`trnorm`, `turkish-normalizer` vb. hepsi
404; VoxCPM2 kartındaki "trnorm" yayınlanmış paket değil). `num2words` çıktısı bitişik
("üçbinbeşyüz") — kullanılamaz. Bu yüzden sıfırdan yazıldı.

### Elenenler — bir daha denemeyin

| Denenen | Sonuç |
|---|---|
| **Referansı değiştirmek** (VoxCPM2 şüpheli ref → beğenilen ses) | **Kalite farkı YOK.** Aksan/akıcılık referanstan gelmiyor; tavan modelin Türkçesi. |
| **`instruct` ile duygu** (mood pitch preset'leri) | **Ölü.** Klon referansı varken yok sayılıyor — F0 ölçümüyle kanıtlandı: etki 7.7 Hz, gürültü tabanı 10.4 Hz, işaret bile ters. Dokümanı da doğruluyor: voice-design sadece Çince/İngilizce eğitilmiş. |
| **`num_step` 32 → 64** | Fark yok, 1.6× yavaş. |
| **Voice-design modu** (klonsuz, sadece instruct) | Hızlı (RTF 0.73) ama yabancı aksan ("kötü Rus gibi"). |
| **Sabit seed ile sabit ses** | Çalışmıyor. `manual_seed` aynı *metni* tekrarlanabilir kılıyor; farklı metinlerde konuşmacı sabitlenmiyor (0.481 vs kontrol 0.476, üst taban 0.787). **Sabit ses için klonlama zorunlu.** |
| **`VoiceClonePrompt` cache** | Sadece %6 kazanç (RTF 3.44 → 3.23). Maliyet tokenizasyonda değil, referans token'larının diffusion'ın 32 adımı boyunca context'te olmasında — yapısal. |
| **`speed=0.9`** ile kelime düşmesini çözme | Çözmedi. |

### `[mood:]` mekanizmasının gerçek durumu
`worker/omnivoice_tts.py` → `MOOD_PRESETS` iki şey gönderiyor: `instruct` (**ölü**) ve `speed`
(1.18 / 0.85 — **çalışan tek kısım**). Yani mevcut "duygu" tamamen konuşma hızı değişimi.
Gerçek duygu isteniyorsa mekanizmanın yeniden tasarlanması gerekiyor.

### Model karşılaştırması (kullanıcı kulakla değerlendirdi)
- **OmniVoice** — en doğru + duygulu hissedilen. Devam edilen model.
- **Piper TR** — telaffuz en doğru (rakam/tarih dahil), RTF 0.04, ama robotik, duygu yok.
- **Freya / Chatterbox / Orpheus** — rakam, tarih veya tonlamada sorun çıkardı.
- **F5-TTS** — elendi: referans sızması + RTF 9.58.
- **VoxCPM2** — daha önce elenmişti (duygu yok), bench'e alınmadı.

Not: Chatterbox'ta yüksek `exaggeration` uzun cümlede çıktıyı şişiriyor (bir cümlede 32.6 sn,
diğer setlerde 9-12 sn).

---

## 3. AÇIK KALANLAR

1. **`dcx514ai/omnivoice_tr_finetune`** — HF'de gated, izin isteği gönderildi, **bekleniyor**.
   Tavanın model olduğunu tespit ettiğimize göre modelin Türkçesini değiştiren tek seçenek bu.
   Runner iskeleti hazır: `experiments/tts-local-bench/runners/run_omnivoice_tr_finetune.py`
   (`revision="v1500"`, `trust_remote_code=True`). Erişim gelince tek komutla aynı bench'e girer.
   Tek konuşmacı (erkek, sesli kitap) — "tek sabit ses yeter" kararı verildiği için bu artık
   risk değil, istenen şey.
2. **Kelime düşmesi** — trnorm sonrası %6.3 WER'de 9 atlanan kelime kaldı. Nedeni bulunamadı;
   süre tahmini (`utils/duration.py` `RuleDurationEstimator`) dile değil yazı sistemine bakıyor
   (Türkçe = Latin = 1.0/karakter, rakam 3.5) — muhtemel şüpheli ama kanıtlanmadı.
3. ~~**Kısa cümle çökmesi**~~ — **KAPANDI** (kod hazır, deploy edilmedi). Ölçüm düzeltmesi:
   `ZeroDivisionError` OmniVoice'tan GELMİYORDU, bench harness'ının RTF hesabıydı
   (`common.py`, süre 0 → bölme) ve orada zaten düzeltilmişti. Gerçek arıza **stokastik boş
   (sıfır uzunluklu) çıktı** ve **karakter eşiği YOK**. Lokalde iki AYRI rejim ölçüldü
   (omnipick klon referansı, n=12/metin):

   | sınıf | noktalamasız | noktalı |
   |---|---|---|
   | tek kelime ("Tamam", "Evet", "Peki", "Olur", "Hayır") | **16/60 boş** | **0/60** |
   | çok kelimeli ~25 karakter ("Elbette, hemen bakıyorum") | 2/12 | 4/12 |

   Yani tek-kelimelik yanıtlarda tetikleyici uzunluk değil **cümle sonu noktalamasının
   olmaması**; nokta eklemek bunu sıfırlıyor ve sesi bozmuyor (ort. süre 0.71 → 0.70 sn).
   ~25 karakterli cümlelerdeki boşluk noktalamadan BAĞIMSIZ → retry gerekli.
   Production'daki asıl tehlike de `ZeroDivisionError` değil: hiç frame push edilmezse
   livekit "no audio frames were pushed" APIError'ıyla **turu öldürüyor**.
   Uygulanan üç katman (`worker/omnivoice_tts.py`): noktalama ekle → tek retry →
   sessizlik. Kalıp cache'i (madde 4) bu sınıfı ayrıca ilk üretimden sonra tamamen
   atlatıyor.
4. **Klon maliyeti** — sabit ses için klonlama zorunlu ve bedeli ~3.4× (4 sn referansla ~1.7×).
   Tamamen kaçmanın tek yolu sesi ağırlıklara gömmek: fine-tune (bkz. madde 1) ya da doğuştan
   sabit sesli bir model.
   **Kısmî çözüm hazır:** `worker/tts_cache.py` — kalıp/kısa cümlelerde (pi_brain scripted
   satırları + ≤48 karakter) ses diske yazılıyor, ikinci kez klon maliyeti SIFIR. Genel LRU
   değil, bilinçli olarak dar. Anahtar pinned referansın parmak izini içerdiği için
   §1.B uygulanırsa cache kendiliğinden geçersizleşir.

---

## 4. Bench'i tekrar kullanmak için
```bash
cd experiments/tts-local-bench
python3 -m http.server 8009      # → http://localhost:8009/compare.html
./run_all.sh <model>             # tek model yeniden üret
python3 trnorm.py --selftest     # 26/26 beklenir
```
Ayrıntı: `experiments/tts-local-bench/README.md` (setler, cümle kategorileri, tüm ölçümler).
Ölçüm dosyaları: `out/timings.json`, `out/asr_eval.json`.

**Uyarı:** tüm RTF değerleri MacBook M4 Pro / MPS. Sunucudaki RTX 3090'da kabaca 3-5'e bölünür
ama **oranlar korunur**.
