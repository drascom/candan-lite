# piper-emniyet — Higgs ölürse konuşacak yedek motor, ölçüldü

Higgs 28 Tem'de **tek motor** oldu (OmniVoice kaldırıldı, `TTS_ENGINE` dallanması
koddan çıktı). Kalıcı bir arıza Candan'ı tamamen susturur. Bu deney emniyet ağı adayı
**Piper**'ı kurar ve Higgs'le **aynı cümleler, aynı ölçütlerle** karşılaştırır.

**Canlıya BAĞLANMADI.** `worker/` değişmedi, `candan-worker` restart edilmedi, `.env`'e
motor anahtarı eklenmedi, systemd'de piper unit'i yok. Rapor:
`handoff/2026-07-28-piper.md`.

## Sonuç, tek satırda

WER (Whisper geri-dönüşü, n01–n14): **Higgs 0.0281 · Piper (dfki + trnorm) 0.0281** —
ölçüm ikisini ayırt etmedi. RTF 0.51 → **0.031**, ilk ses 0.55 s → **0.15 s**,
9.1 GB VRAM → **225 MB RAM, GPU yok**. Kaybedilen: **ses kimliği** (klonlama yok) ve
**43 duygu/kontrol token'ının tamamı**. Karar kulakta → `./serve.sh`.

## Dosyalar

| dosya | ne yapar | nerede koşar |
|---|---|---|
| `setup_server.sh` | `/opt/piper-venv` + `/opt/piper/voices` (4 Türkçe ses). Idempotent, systemd'ye dokunmaz | sunucu |
| `sync_to_server.sh` | kodu `/opt/piper-exp`'e gönderir (+ `sentences.json`, `worker/trnorm.py`) | Mac |
| `run_piper.py` | 4 ses × (ham \| trnorm) × 29 cümle; RTF, ilk parça, RSS | sunucu (CPU) |
| `piper_duygu.py` | kulak setinin 2 duygu satırının Piper karşılığı | sunucu |
| `fetch_outputs.sh` | wav'ları ve raporu geri çeker | Mac |
| `asr_eval.py` | WER — `tts-local-bench`'in mantığını import eder, kopyalamaz | Mac (`mlx_whisper`) |
| `kulak_set.py` + `kulak.html` + `serve.sh` | Higgs ↔ Piper yan yana dinleme sayfası (:8013) | Mac |

## Tam tur

```bash
./sync_to_server.sh
ssh root@192.168.0.25 'cd /opt/piper-exp && bash setup_server.sh'        # bir kez
ssh root@192.168.0.25 'cd /opt/piper-exp && /opt/piper-venv/bin/python run_piper.py && \
  /opt/piper-venv/bin/python piper_duygu.py'
./fetch_outputs.sh
../tts-local-bench/venvs/whisper/bin/python asr_eval.py
python3 kulak_set.py && ./serve.sh
```

## Neden bu kıyas zemini

Cümleler `../higgs-tts3/sentences.json`'dan **birebir** okunuyor ve WER
`../tts-local-bench/runners/asr_eval.py`'den **import** ediliyor (kopyalanmıyor) —
buradaki sayılar `higgs-tts3/out/asr_eval.json` ile doğrudan aynı eksende.
Higgs sesleri de yeniden üretilmiyor: kulak seti var olan koşumlardan
(`higgs-clone-trnorm/`, `duygu-atlasi/out/wav/`) kopyalıyor, `higgs-tts` servisine
hiç istek atılmıyor.

## Tuzaklar

* **`trnorm` Piper için ŞART.** Ham metinde WER 0.107; trnorm ile 0.028. espeak-ng
  sayı/yüzde/saat/yılı zaten çeviriyor, boşluk **birim / kısaltma / ondalık / para**.
* **Piper `[...]` etiketlerini HARFİ HARFİNE okur.** `trnorm` köşeli parantez içini
  Higgs markup'ı için bilerek KORUYOR — Piper yolunda o koruma kaldırılmalı.
* **"İlk parça" = ilk CÜMLE.** Piper üreteci cümle başına tek parça veriyor; Higgs'in
  blok akışıyla birebir aynı şey değil.
* `onnxruntime` LXC içinde `pthread_setaffinity_np` uyarısı verir — zararsız, ama
  servisleştirilirse `intra_op_num_threads` açıkça verilmeli.
