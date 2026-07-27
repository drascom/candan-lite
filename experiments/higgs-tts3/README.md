# Higgs TTS 3 (4B) — Türkçe deneyi

**Soru:** `bosonai/higgs-tts-3-4b`, Türkçe ev asistanı sesi olarak OmniVoice'tan daha mı iyi?

**Cevap (2026-07-27, ölçüldü):** Doğruluk/anlaşılırlıkta **evet** (WER 0.028 vs 0.085,
üstelik zor sayıları normalizasyon olmadan da doğru okuyor). Hızda **hayır** — RTF ~1.7×
daha yavaş ama hâlâ gerçek zamanın altında (0.52 vs 0.31). Kalite kararı kulakla:
`./serve.sh` → `compare.html`.

> **Bu bir DENEY.** Canlı worker/beyin/STT'ye dokunulmadı. `worker/omnivoice_tts.py`,
> `worker/.env`, `candan-worker.service`, `pi-service.service` — hiçbiri değişmedi.
> Tek dokunulan: `omnivoice-bridge.service` iki kez geçici durdurulup geri başlatıldı
> (VRAM takası, aşağıda).

---

## 1. Neden düz `transformers` "ile" değil de bir ara repo üzerinden?

`bosonai/higgs-tts-3-4b` **ağırlık + config**, çalıştırma kodu DEĞİL. İki somut engel:

1. Mimarisi `higgs_multimodal_qwen3`. Bu model tipi **hiçbir transformers sürümünde yok**
   — 5.14.1'de ve `main` dalında yalnız `higgs_audio_v2` ve `higgs_audio_v2_tokenizer` var
   (kontrol edildi). Yani `AutoModel.from_pretrained("bosonai/higgs-tts-3-4b")` çalışmaz.
2. Ağırlık adları da standart değil (`body.layers.*`, `tied.embedding.*`), ve dalga formunu
   üreten kodek ayrı bir repoda (`bosonai/higgs-audio-v2-tokenizer`).

Model kartının önerdiği yollar SGLang-Omni (docker) ve vLLM-Omni — ikisi de ağır.
Onun yerine **`multimodalart/higgs-audio-v3-tts-4b-transformers`** kullanılıyor:

* aynı ağırlıklar, **bayt bayt aynı** (`model.safetensors` = 9 309 834 930 B, ikisinde de),
* üstüne 16 KB'lik bir `modeling_*.py` (`trust_remote_code`) + `auto_map`,
* kodek olarak transformers-yerlisi `bosonai/higgs-audio-v2-tokenizer`'ı çağırıyor,
* `generate_speech(text, tokenizer, reference_audio=..., reference_text=...)` → 24 kHz mono.

Yani ağırlık olarak Higgs TTS 3'ün ta kendisi; yalnız yükleyici farklı. Docker yok,
SGLang yok, streaming yok.

## 2. Sunucu kurulumu

```bash
./sync_to_server.sh                     # kodu /opt/higgs-exp'e gönder
ssh root@192.168.0.25 'cd /opt/higgs-exp && ./setup_server.sh'
```

| Ne | Nerede | Boyut |
|---|---|---|
| venv | `/opt/higgs-venv` (torch 2.13.0+cu130, transformers 5.14.1) | 4.8 GB |
| ağırlıklar | `/opt/higgs-models/hf` | 9.3 GB + 0.77 GB kodek |
| deney kodu | `/opt/higgs-exp` | < 1 MB |
| çıktı wav'ları | `/opt/higgs-exp/out` → Mac'te `out/` | ~40 MB |

**İzolasyon kuralları (bilerek):**

* venv **`--system-site-packages` OLMADAN**. İlk denemede sistem paketleriyle paylaşıldı
  ve zincirleme kırıldı: sistem `scipy`'si venv `numpy` 2.x ile (`numpy.Inf` kalktı),
  sonra sistem `torchvision`'ı venv `torch` 2.13 ile uyuşmadı. Tam izolasyon 4.8 GB'a mal
  oluyor, disk zaten bol.
* `torchaudio` **torch ile aynı CUDA sürümünden** kurulmalı (`--index-url .../cu130`),
  yoksa `libtorchaudio.so` yüklenmiyor. Sadece referans sesi 48 kHz→24 kHz indirmek için
  gerekli ama `_encode_reference` onu koşulsuz import ediyor.
* `/opt/omnivoice-venv` (torch 2.8) ve `/opt/candan-lite/worker/.venv`'e **dokunulmadı**.

## 3. VRAM takası — Higgs ve OmniVoice AYNI ANDA ÇALIŞAMAZ

24 GB kartta sürekli duranlar: `llama-server` 9.5 GB + `whisper` 2.3 GB = 11.9 GB.
Kalan ~12.2 GB'ı **ya** OmniVoice (9.9 GB) **ya** Higgs (~10.5 GB) alabilir, ikisi birden değil.

Bu yüzden koşum sırası şu — `run_all.sh` bunu yapar:

```bash
systemctl stop omnivoice-bridge.service      # ~12.2 GB açılır
... Higgs koşumu ...
systemctl start omnivoice-bridge.service     # canlı sistem geri
curl -s http://127.0.0.1:8808/api/default    # KANIT
```

`whisper.service` ve `candan-brain.service` (llama-server) **asla durdurulmaz** — STT ve
beyin ölür.

## 4. Koşum

```bash
./sync_to_server.sh
ssh root@192.168.0.25 'cd /opt/higgs-exp && ./run_all.sh'   # köprüyü durdurur/başlatır
./fetch_outputs.sh                                          # wav'ları Mac'e çek
./serve.sh                                                  # dinleme sayfası
```

Tek tek:

| Betik | Nerede | Ne yapar |
|---|---|---|
| `run_omnivoice_server.py` | sunucu | TABAN: canlı köprüden (:8808) aynı 29 cümle. **Köprü AÇIKKEN** koşar. |
| `run_higgs.py` | sunucu | 3 Higgs seti. **Köprü KAPALIYKEN** koşar. |
| `merge_manifest.py` | ikisi de | `out/manifest.json` (compare.html'in verisi) |
| `asr_eval.py` | Mac | Whisper geri-dönüş testi (WER + atlanan kelime) |
| `make_sentences.py` | Mac | `sentences.json`'u tts-local-bench'ten yeniden üretir |

## 5. Setler

| Set | Referans | Metin | Ne sorusuna cevap |
|---|---|---|---|
| `omnivoice-server` | pinned clone | köprünün num2words'ü | **taban** — bugün canlıda ne duyuyoruz |
| `higgs-default` | yok (zero-shot) | ham | modelin kendi Türkçe sesi ne kadar iyi |
| `higgs-clone` | `default-ref.wav` + transkript | ham | aynı sesle, normalizasyon **olmadan** |
| `higgs-clone-trnorm` | aynı | `trnorm` | canlıya alsak trnorm'a hâlâ gerek var mı |

Cümleler `tts-local-bench`'ten geliyor (15 genel + 14 normalizasyon) — kullanıcı OmniVoice
çıktılarını zaten bunlarla dinledi, karşılaştırma ancak aynı cümlelerle adil olur.

Referans **bir kez** kodlanıp `refs/default-ref.codes.pt`'ye yazılıyor (0.27 s, 180 kare);
29 cümlenin hepsi aynı kodları kullanıyor. Bu maliyet cümle ölçümlerinin **dışında**.

## 6. Ölçüm sonuçları (RTX 3090, 2026-07-27)

| | omnivoice-server | higgs-default | higgs-clone | higgs-clone-trnorm |
|---|---|---|---|---|
| RTF ortalama | **0.305** | 0.491 | 0.516 | 0.516 |
| RTF kısa / orta / uzun | **0.49 / 0.30 / 0.22** | 0.56 / 0.49 / 0.47 | 0.58 / 0.51 / 0.49 | 0.58 / 0.51 / 0.49 |
| ilk sese kadar (320 ms blok) | — (streaming ayrı yol) | 0.16 s | 0.25 s | 0.24 s |
| süreç VRAM tepesi (torch) | — (ayrı süreç) | 8.9 GB | 10.2 GB | 10.2 GB |
| GPU tepesi (tüm kart) | 16.7 GB | 20.9 GB | 22.2 GB | 22.2 GB |
| RAM tepesi (sistem) | 4.3 GB | 4.2 GB | 4.2 GB | 4.2 GB |
| ASR geri-dönüş WER | 0.085 | 0.076 | 0.058 | **0.028** |
| atlanan kelime | 14 | 11 | 9 | **4** |
| başarısız cümle | 0 | 0 | 0 | 0 |

Model yükleme: **3.2 s**, torch ayrılan 8.3 GB, yükleme sırasında RAM tepesi **3.4 GB**
(19 GB'lık makinede rahat — `device_map` + safetensors mmap ağırlıkları CPU RAM'ine hiç
tam açmıyor, doğrudan GPU'ya akıtıyor). Referans kodlama tek sefer 0.27 s / 180 kare.

İki bağımsız koşumda sıralama aynı çıktı (WER 0.085/0.077/0.058/0.034 → 0.085/0.076/
0.058/0.028); örnekleme stokastik olduğu için ondalıklar birebir tekrar etmiyor.

**Türkçe zor vakalar** (`%25`, `3.500 lira`, `14:30'da`, kesme işaretli ekler): Higgs bunları
**ham metinden** doğru okuyor (n01/n02/n03/n05/n06/n08/n09 → WER 0.000), OmniVoice ise
ancak köprünün num2words'ü sayesinde doğru okuyor. Higgs'in ham hâlde kalan iki zayıf
noktası `1.250.000` (binlik ayıraç + milyon) ve `09:05`; `trnorm` birincisini düzeltiyor.

> **ASR taban gürültüsü 0.000** (metni kesin bilinen klip). Yine de "atlanan kelime"
> listesindeki `kilogram→kilo`, `link'ten→linkten` gibi kalemler **ölçüm artefaktı** —
> Whisper konuşulan sayıyı/kısaltmayı geri yazarken böyle yapıyor, model kelimeyi
> düşürmüş değil. Gerçek düşme yok: 116 wav'ın hiçbiri boş çıkmadı.

## 7. Lisans

**Araştırma / ticari olmayan** (`boson-higgs-tts-3-research-and-non-commercial-license`).
Kişisel ev asistanı bu kapsamda. Ürünleştirme / barındırılan API / yeniden satış ayrı
ticari lisans ister.

## 8. Bilinen sınırlar

* **Streaming yok.** Bu kurulum cümleyi bitirip tek parça wav veriyor. `run_higgs.py`
  içindeki `first_audio_latency()` "streaming olsaydı" 0.23 s ölçüyor, ama onu gerçekten
  kullanmak için SGLang-Omni gerekir — ayrı ve ağır iş.
* **Toplu istek (batching) yok.** Ölçümler eşzamanlılık = 1.
* Yükleme raporunda `audio_head.weight | MISSING` uyarısı **kozmetik**: ağırlık
  `audio_embedding.weight` ile bağlı (`tie_weights`), aynı tensör olduğu doğrulandı
  (`data_ptr` eşit).
