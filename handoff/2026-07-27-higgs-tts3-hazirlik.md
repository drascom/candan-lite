# Higgs TTS 3 (4B) — gece kurulumu, ölçümler, sabah nasıl test edilir

**Tarih:** 2026-07-27 (gece) · **Sunucu:** root@192.168.0.25 (RTX 3090, 24 GB)
**Deney dizini:** `experiments/higgs-tts3/` · **Ayrıntılı teknik not:** aynı dizindeki `README.md`

---

## 0. Sabah tek komut

```bash
cd ~/Documents/work/candan-lite/experiments/higgs-tts3 && ./serve.sh
```

Tarayıcı açılır → `http://localhost:8009/compare.html`. Ses dosyaları **zaten Mac'te**,
sunucuya bağlanmaya gerek yok. Sayfada satır = cümle, sütun = model; aynı cümleyi
yan yana dinle. `İsimleri gizle` ile kör dinleme, `Yalnız zor vakalar` ile sayı/tarih
cümlelerine odaklan.

Dinlerken bakılacak dört sütun:

| sütun | ne |
|---|---|
| `omnivoice-server` | **bugün canlıda duyduğun ses** (köprüden alındı, normalizasyon açık) |
| `higgs-default` | Higgs'in kendi (zero-shot) Türkçe sesi — klonlanmamış |
| `higgs-clone` | Higgs, **aynı Candan referansıyla** klonlanmış, metin ham |
| `higgs-clone-trnorm` | aynısı + `trnorm` — canlıya alınırsa önerilen yapılandırma |

---

## 1. Canlı sistem — durum

Higgs koşumu için `omnivoice-bridge.service` iki kez geçici durduruldu, **her seferinde
geri başlatıldı**. Şu anki durum (koşum sonu, otomatik doğrulama çıktısı):

```
durum: active
/api/default: {"ref_audio":"/opt/omnivoice/default-ref.wav","ref_text":"Merhaba, bu bir
Türkçe seslendirme testidir. VoxCPM 2 ile uzun kitapları sesli kitaba dönüştürebilirsiniz."}

pid, process_name, used_gpu_memory [MiB]
34652, /root/llama.cpp/build/bin/llama-server, 9556 MiB
36394, /opt/whisper-venv/bin/python, 2386 MiB
105370, /opt/omnivoice-venv/bin/python, 4616 MiB
```

Ayrıca canlı sentez testi yapıldı: `POST /api/tts` → `http=200`, 24 kHz, 2.4 s ses.

`candan-worker`, `candan-brain`, `whisper`, `pi-service`, `searxng` hiç durdurulmadı,
hepsi `active`. **Hiçbir canlı dosya değişmedi** — `worker/omnivoice_tts.py`, `worker/.env`,
`candan-worker.service`, `pi-service.service`, `/opt/omnivoice/*` aynen duruyor.

---

## 2. Kurulum — ne nereye kuruldu

| Ne | Yer | Boyut |
|---|---|---|
| venv | `/opt/higgs-venv` (torch 2.13.0+cu130, transformers 5.14.1, torchaudio 2.11.0+cu130) | 4.8 GB |
| ağırlıklar | `/opt/higgs-models/hf` | 9.5 GB |
| deney kodu | `/opt/higgs-exp` | 28 MB |
| loglar | `/opt/higgs-logs/` | küçük |

Disk: 149 GB boş kaldı (öncesi 163 GB). `/opt/omnivoice-venv` ve
`/opt/candan-lite/worker/.venv` **hiç ellenmedi**.

### Beklenmedik çıkan tek şey: düz `transformers` yetmedi

Görevde "düz transformers yeterli" deniyordu; **değildi**:

* `bosonai/higgs-tts-3-4b` **ağırlık + config**, çalıştırma kodu değil.
* Mimarisi `higgs_multimodal_qwen3` — **hiçbir transformers sürümünde yok** (5.14.1 ve
  `main` dalı kontrol edildi; orada yalnız `higgs_audio_v2` ve `higgs_audio_v2_tokenizer` var).
* Ağırlık adları da standart değil (`body.layers.*`, `tied.embedding.*`); dalga formunu
  üreten kodek ayrı repoda.

Model kartının önerdiği yollar SGLang-Omni (docker) ve vLLM-Omni — ikisi de ağır ve
görevin kapsamı dışı. Onun yerine **`multimodalart/higgs-audio-v3-tts-4b-transformers`**
kullanıldı: **aynı ağırlıklar, bayt bayt aynı** (`model.safetensors` = 9 309 834 930 B,
iki repoda da), üstüne 16 KB'lik bir `trust_remote_code` modeling dosyası. Kodek olarak
transformers-yerlisi `bosonai/higgs-audio-v2-tokenizer` yükleniyor.

Yani **çalıştırılan model Higgs TTS 3'ün ta kendisi**, yalnız yükleyicisi farklı.
Docker yok, SGLang yok. `bosonai/higgs-tts-3-4b`'nin ilk indirilen kopyası (aynı baytlar,
kullanılmıyor) sonradan silindi.

Yol boyunca çözülen iki tuzak (README'de ayrıntısı var):
1. venv `--system-site-packages` ile kurulunca zincirleme kırıldı (sistem `scipy` ↔ venv
   `numpy` 2.x, sistem `torchvision` ↔ venv `torch` 2.13). Çözüm: **tam izole venv**.
2. `torchaudio` torch ile **aynı CUDA sürümünden** kurulmalı, yoksa `libtorchaudio.so`
   yüklenmiyor. Çözüm: `--index-url .../cu130`.

---

## 3. Ölçümler — Higgs vs OmniVoice, AYNI sunucu, AYNI 29 cümle, AYNI referans ses

OmniVoice tabanı Mac'ten değil, **canlı köprüden (:8808) yeniden ölçüldü** ki
karşılaştırma adil olsun.

| | omnivoice-server | higgs-default | higgs-clone | higgs-clone-trnorm |
|---|---|---|---|---|
| **RTF ortalama** | **0.305** | 0.491 | 0.516 | 0.516 |
| RTF kısa / orta / uzun | **0.49 / 0.30 / 0.22** | 0.56 / 0.49 / 0.47 | 0.58 / 0.51 / 0.49 | 0.58 / 0.51 / 0.49 |
| toplam: 29 cümle | 146.8 s ses / 40.6 s | 150.4 s / 72.9 s | 142.4 s / 72.1 s | 145.7 s / 74.0 s |
| **ilk sese kadar** (streaming olsa) | — | 0.16 s | 0.25 s | 0.24 s |
| gecikme (streaming YOK, bugünkü hâli) | 1.0–1.7 s / cümle | 1.6–4.1 s / cümle | aynı | aynı |
| **VRAM tepe** (bu süreç, torch) | ~9.9 GB | 8.9 GB | 10.2 GB | 10.2 GB |
| GPU tepe (tüm kart) | 16.7 GB | 20.9 GB | 22.2 GB | 22.2 GB |
| **RAM tepe** (sistem) | 4.3 GB | 4.2 GB | 4.2 GB | 4.2 GB |
| **ASR geri-dönüş WER** | 0.085 | 0.076 | 0.058 | **0.028** |
| atlanan kelime (toplam) | 14 | 11 | 9 | **4** |
| başarısız/boş cümle | 0 | 0 | 0 | 0 |

* **Model yükleme:** 3.2 s · torch ayrılan 8.3 GB · yükleme sırasında **RAM tepesi 3.4 GB**.
  Korkulan swap'a düşme olmadı: `device_map` + safetensors mmap ağırlığı CPU RAM'ine hiç
  tam açmıyor, doğrudan GPU'ya akıtıyor. 19 GB'lık makinede rahat.
* **Referans kodlama:** tek sefer 0.27 s → 180 kare, `refs/default-ref.codes.pt`'ye
  yazılıyor; 29 cümlenin hepsi aynı kodları kullanıyor (cümle ölçümlerinin dışında).
* Ölçüm iki bağımsız koşumda tekrarlandı, sıralama aynı çıktı.

### Türkçe zor vakalar (`%25`, `3.500 lira`, `14:30'da`, kesme işaretli ekler)

Whisper geri-dönüş testi (`asr_eval.py`), taban gürültüsü **WER 0.000**.

**Higgs bunları HAM METİNDEN doğru okuyor** — hiçbir normalizasyon olmadan:
`n01 %25 + 3.500 TL + 2.625 TL`, `n02 14:30'da`, `n03 1994`, `n05 2,5 / 750 gr`,
`n06 12.03.2026`, `n08 1994'te / 2026'ya / 5'ten`, `n09 %25'lik / %8'den` → hepsi **WER 0.000**.

OmniVoice aynı cümleleri ancak köprünün `num2words` normalizasyonu sayesinde doğru
okuyor, ve buna rağmen `n05` (`750 gr` → "geride"), `n06` (`Sözleşme` → "Rösleşme"),
`n10` (`$`/`€` düşüyor) hatalı.

Higgs'in ham hâlde kalan tek gerçek zayıflığı `1.250.000` (binlik ayıraç + milyon):
"bin milyon iki yüz elli bin" diye okuyor. **`trnorm` bunu düzeltiyor** →
`higgs-clone-trnorm`'da kalan üç "hata"nın üçü de ölçüm artefaktı:

| id | ASR transkripti | gerçek durum |
|---|---|---|
| n04 | "1 milyon 250 bin lira kar açıkladı" | **doğru** — Whisper rakama geri yazdı, `kâr`→`kar` şapka farkı |
| n07 | "9.05 trenidir" | **doğru** — Whisper rakama geri yazdı |
| n13 | "Linkten iki dosya" | **doğru** — `link'ten` bitişik yazılmış |

Yani **`higgs-clone-trnorm`'da gerçek kelime düşmesi yok**. 116 wav'ın hiçbiri boş çıkmadı.

---

## 4. Yorum — nerede kazanıyor, nerede kaybediyor

**Higgs kazanıyor:**
* Sayı/tarih/saat/ekli-sayı okumada belirgin üstün — üstelik normalizasyon olmadan da.
* Kelime düşürme sorunu yok (OmniVoice'ta yaşanan `başlıyor` tipi düşmeler,
  `handoff/2026-07-25-trnorm-production.md`).
* Streaming'e geçilirse ilk sese kadar **0.25 s** — bugünkü non-streaming 1.6–4.1 s'ye göre
  çok iyi bir tavan.

**OmniVoice kazanıyor:**
* **~1.7× hızlı** (RTF 0.31 vs 0.52). Higgs yine de gerçek zamanın altında.
* **Bugün streaming var** (WS yolu). Higgs'te yok → kısa turlarda algılanan gecikme
  bugünkü hâliyle Higgs'te daha kötü olur.
* Zaten canlı, denenmiş, cache'i ve mood preset'leri kurulu.

**Karar kulakta:** ikisi de ölçülebilir biçimde çalışıyor; fark ses kalitesi/doğallığı.
Sabah `compare.html`.

---

## 5. Canlıya almak istersek ne gerekir

> **ÖNCE BUNU BİL: Higgs ve OmniVoice AYNI ANDA ÇALIŞAMAZ.**
> 24 GB kartta `llama-server` 9.5 GB + `whisper` 2.3 GB sürekli duruyor → geriye ~12.2 GB.
> OmniVoice 9.9 GB, Higgs ~10.2 GB alıyor. İkisi birden **sığmıyor**.
> Yani bu bir **geçiş** kararı, "yanına ekleme" değil. Geri dönüş kolay (servisi durdur/başlat)
> ama aynı anda A/B yapılamaz.

Geçiş için gereken iş (bu gece **yapılmadı**, deney izole tutuldu):

1. **Köprü uyumluluğu.** Canlı worker `POST /api/tts` (form: `text`, `language`, `mode`,
   `use_pinned`, `instruct`, `speed`) ve WS `speak` bekliyor. Higgs için aynı sözleşmeyi
   konuşan bir `higgs-bridge` servisi yazmak gerekir. `instruct`/`speed` karşılığı Higgs'te
   inline kontrol token'ları (`<|prosody:pitch_high|>`, `<|prosody:speed_fast|>` …) — mood
   preset'leri buna eşlenmeli.
2. **Streaming.** Bugünkü kurulum cümleyi bitirip tek parça wav veriyor. Canlı sohbette
   bu kabul edilemez. İki seçenek:
   * kendi kare-blok streaming'imizi yazmak (ölçüldü: ilk blok 0.25 s — `run_higgs.py`
     içindeki `first_audio_latency()` bunu zaten yapıyor), **veya**
   * SGLang-Omni / vLLM-Omni kurmak (ağır: docker + ~40 GB VRAM "known-good" tabanı,
     24 GB "raporlandı ama doğrulanmadı" bölgesinde).
3. **trnorm.** Higgs ham metinle iyi ama `1.250.000` tipi binlik+milyon için `trnorm`
   şart. Zaten `worker/`'da var, taşınabilir (stdlib).
4. **Lisans.** `boson-higgs-tts-3-research-and-non-commercial-license` — **araştırma /
   ticari olmayan**. Kişisel ev asistanı kapsamda; ürünleştirme/barındırılan API değil.
5. **VRAM marjı.** Higgs 10.2 GB + llama 9.5 + whisper 2.3 = 22.0 GB / 24.5 GB.
   ~2.5 GB marj. Bugünkü OmniVoice'lu durumdan (21.8 GB) çok farklı değil ama dar.

---

## 6. Yeniden koşmak istersen

```bash
cd experiments/higgs-tts3
./sync_to_server.sh                                          # kodu sunucuya gönder
ssh root@192.168.0.25 'cd /opt/higgs-exp && ./run_all.sh'    # köprüyü durdurur+GERİ BAŞLATIR
./fetch_outputs.sh                                           # wav'ları Mac'e çek
../tts-local-bench/venvs/whisper/bin/python asr_eval.py      # WER tablosu
./serve.sh
```

`run_all.sh` içinde `trap ... EXIT` var: koşum patlasa da `omnivoice-bridge` geri
başlatılıyor ve `/api/default` çıktısı kanıt olarak basılıyor. Uçtan uca bir kez
çalıştırılıp doğrulandı.

`.gitignore`: `experiments/higgs-tts3/out/`, `refs/*.wav`, `refs/*.pt` hariç tutuldu —
ses ve referans türevleri commit edilmez, araçlar edilir.

---

## 7. Emin olmadığım tek şey

**Ses kalitesi/doğallığı kararını veremem — o kulak işi.** Ölçebildiğim her şey
(doğruluk, hız, bellek, kelime düşmesi) yukarıda; ama "hangisi daha çok Candan'a
benziyor / daha doğal" sorusunun cevabı `compare.html`'i dinlemekte.

İkincil bir belirsizlik: `multimodalart` paketlemesindeki `modeling_*.py`'nin örnekleme
ve delay-pattern mantığının **resmi SGLang-Omni yolu ile birebir aynı** olduğunu
doğrulayamadım (kaynak okundu, mlx-audio'nun bağımsız uygulamasıyla tutarlı görünüyor
ve çıktı Türkçe olarak temiz). Ağırlıklar kesinlikle aynı; küçük örnekleme farkları
kaliteyi bir miktar etkileyebilir.
