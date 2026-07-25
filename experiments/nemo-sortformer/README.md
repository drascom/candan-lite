# Whisper + NVIDIA Streaming Sortformer deneyi

Bu deney üretim worker'ına bağlanmaz. Aynı 16 kHz mono sesi:

1. mevcut Wyoming/faster-whisper servisine gönderir,
2. NVIDIA `diar_streaming_sortformer_4spk-v2.1` ile konuşmacılara ayırır,
3. her konuşmacı segmentini yeniden Whisper'a gönderip etiketli metin basar.

İlk amaç `SPEAKER_00/01` ayrımının Türkçe ev ortamında ve RTX 3090 üzerinde
doğru/kararlı olup olmadığını ölçmektir. Ayhan/Havi kalıcı kimlik eşlemesi bu
deneyin sonraki aşamasıdır.

## Sunucuda kurulum

```bash
cd /opt/candan-lite/experiments/nemo-sortformer
./setup.sh
```

Model ilk çalıştırmada Hugging Face üzerinden indirilir. Model NVIDIA Open Model
License kapsamındadır ve Hugging Face hesabının model erişimini kabul etmiş olması
gerekebilir.

## Mevcut ses dosyası

```bash
.venv/bin/python pipeline.py \
  --input /path/to/two-speaker.wav \
  --whisper-host 127.0.0.1 \
  --profile low-latency \
  --output results/file-test.json
```

## Sunucu mikrofonu

Sunucuya doğrudan bağlı bir mikrofon varsa:

```bash
.venv/bin/python pipeline.py --list-devices
.venv/bin/python pipeline.py \
  --mic 30 \
  --device 0 \
  --whisper-host 127.0.0.1 \
  --keep-wav recordings/mic-test.wav \
  --output results/mic-test.json
```

Mikrofon MacBook'taysa önce orada 16 kHz WAV kaydedip dosyayı sunucuya göndermek
gerekir; NeMo/CUDA deneyi sunucuda çalışır.

`low-latency`, NVIDIA'nın yaklaşık 1.04 saniyelik giriş tamponu ayarlarını kullanır.
`quality` yaklaşık 30.4 saniyelik tamponla daha yüksek bağlam kullanır. `diarize()`
streaming modeli parça parça simüle eder fakat bu ilk araç sonucu kayıt bittikten
sonra basar; gerçek zamanlı LiveKit bağlantısı sonraki üretim aşamasıdır.

