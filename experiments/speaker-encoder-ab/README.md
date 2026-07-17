# Speaker-encoder A/B

İZOLE deney. Canlı sistemi (`worker/models/`, `worker/data/speakers.db`, `memory/`)
BOZMAZ — buradaki her şey sadece okur/kopyalamaz.

## Neden
2026-07-17 ölçümü: canlı `campplus.onnx` (CAM++, 192-dim) gerçek ortamda **aynı-kişi**
kosinüs benzerliği ~0.57 (yankı temizlendikten sonra, çekirdek-içi ort=0.568). CAM++
için normalde 0.7-0.9 beklenir → campplus darboğaz. Aynı ses üstünde 2 alternatifi
ölçüyoruz. İLK TUR: yalnız aynı-kişi benzerliği (tek kişi). Ayrım (2 kişi) sonra.

## Adaylar
| encoder | runtime | dim | dosya |
|---|---|---|---|
| campplus (baseline) | sherpa-onnx | 192 | `worker/models/campplus.onnx` (salt-okuma) |
| WeSpeaker ResNet34-LM | sherpa-onnx | 256 | `models/wespeaker_en_voxceleb_resnet34_LM.onnx` |
| ECAPA-WavLM (OmniVoice) | PyTorch+CUDA | 256 | sunucu: `k2-fsa/TTS_eval_models` (s3prl ister) |

WeSpeaker bizim mevcut sherpa runtime'ında ÇALIŞIR (drop-in aday). ECAPA-WavLM PyTorch +
s3prl + CUDA ister → yalnız sunucuda (192.168.0.25) koşar.

## Ses nasıl toplanır
`worker/cli_client.py --dump-audio` (opt-in). Mikrofona giden ham PCM'in kopyası
`worker/logs/dump-<sayaç>.wav`'a (16k mono) yazılır. ~20 sn konuş, Ctrl+C → WAV kalır.
O WAV(lar)ı `audio/` altına koy, `ab.py audio/` çalıştır.

## Kullanım
    # worker venv'inde (sherpa_onnx burada):
    cd worker && .venv/bin/python ../experiments/speaker-encoder-ab/ab.py <wav-dizini>

    # boru hattı kanıtı (sentetik WAV üretir + çalıştırır):
    .venv/bin/python ../experiments/speaker-encoder-ab/ab.py --self-test

Çıktı: her encoder için aynı-kişi ikili kosinüs matrisi + özet (min/ort/medyan/maks) ve
karşılaştırma tablosu.

## Not
- `models/` ve `audio/` git'e girmez (`.gitignore`).
- ECAPA-WavLM sunucu tarafı: `--ecapa-model-dir /root/tts_eval_models` verildiğinde ve
  torch+s3prl+CUDA mevcutsa yüklenir; değilse atlanır (mesajla).
