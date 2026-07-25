# WhisperLive — tamamen yerel mikrofon deneyi

İki süreç de bu Mac'te çalışır. `server.py` adı ağdaki ev sunucusunu ifade etmez;
yalnız `127.0.0.1` adresine bağlanan bir WhisperLive WebSocket işlemidir. Canlı
Candan servislerine ve veritabanına dokunmaz.

## Kurulum

```bash
cd experiments/whisperlive-local
bash setup.sh
```

İlk model kullanımında model dosyaları bir kez indirilir; sonrasında çıkarım
Mac'te yapılır. M4 Pro'da WhisperLive/Faster-Whisper CUDA veya MPS kullanmaz,
CTranslate2 CPU yolunda çalışır. İlk deneme için `small` uygundur.

## Çalıştırma

Terminal 1:

```bash
.venv/bin/python server.py
```

Sunucu yalnız localhost'a bağlanır ve diarization için varsayılan `0.40` cosine
eşiğini kullanır. Başka veriyle denemek için örneğin
`server.py --diarization-threshold 0.45` verilebilir.

Terminal 2 — yalnız canlı Türkçe STT:

```bash
.venv/bin/python mic_test.py --model small
```

Oturumluk konuşmacı ayırma ile:

```bash
.venv/bin/python mic_test.py --model small --diarization --max-speakers 4
```

Diarization `SPEAKER_00`, `SPEAKER_01` gibi o oturuma ait kümeler üretir. Bunlar
kalıcı Ayhan/Havva kimliği değildir. WhisperLive'ın canlı diarizer'ı pyannote
üzerinden `pyannote/wespeaker-voxceleb-resnet34-LM` kullanır ve cosine eşikli
online ortalama ile kümeler.

## Mevcut iki gerçek kayıtla kalibrasyon

```bash
.venv/bin/python offline_eval.py
```

2026-07-17 ölçümü, kişi başına altı adet 4 saniyelik pencere:

- Ayhan within: min `0.505`, medyan `0.733`
- Havva within: min `0.351`, medyan `0.630`
- çapraz kişi: min `-0.046`, medyan `0.075`, maks `0.146`
- eşik `0.40`: tam iki küme, iki kişi de `6/6` doğru kümede
- eşik `0.45–0.55`: Havva ilk pencere nedeniyle iki kümeye bölündü
