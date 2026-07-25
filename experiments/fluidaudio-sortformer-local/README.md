# FluidAudio Sortformer — Yerel Mikrofon Deneyi

Bu deney, konuşmacı diarization işlemini FluidAudio `0.15.5` ve NVIDIA
Sortformer v2.1 Core ML modeliyle tamamen Mac üzerinde çalıştırır. Bir sunucuya
ses göndermez.

## Gereksinimler

- Apple Silicon Mac
- macOS 14 veya üstü
- Xcode Command Line Tools / Swift 6
- İlk model indirmesi için internet bağlantısı
- Terminal veya Codex için macOS mikrofon izni

Model: <https://huggingface.co/FluidInference/diar-streaming-sortformer-coreml>

FluidAudio kaynak kodu: <https://github.com/FluidInference/FluidAudio>

Model ilk çalıştırmada `.models/` klasörüne indirilir. Sonraki çalıştırmalar
tamamen çevrimdışıdır.

## Kurulum kontrolü ve model indirme

```bash
cd "/Users/drascom/work/candan-lite/experiments/fluidaudio-sortformer-local"
swift run -c release SortformerMic doctor
```

## Mikrofon testi

```bash
cd "/Users/drascom/work/candan-lite/experiments/fluidaudio-sortformer-local"
swift run -c release SortformerMic mic --duration 120
```

Kesinleşmemiş düşük gecikmeli segmentleri de görmek için:

```bash
swift run -c release SortformerMic mic --duration 120 --tentative
```

## WAV dosyası testi

```bash
swift run -c release SortformerMic file \
  --input /path/to/test.wav \
  --json /path/to/sortformer.json
```

Varsayılan `balanced` modeli yaklaşık 1.04 saniyelik yayın gecikmesine ve dört
konuşmacı yuvasına sahiptir. Alternatifleri karşılaştırmak için `--preset fast`
veya `--preset efficient` kullanılabilir.

## Beklenen çıktı

```text
[KESİN] [3.20–7.04] SPEAKER_00  aktivite=0.83
[KESİN] [7.20–10.56] SPEAKER_01  aktivite=0.79
```

`SPEAKER_00` ve `SPEAKER_01` oturuma özgü diarization etiketleridir. Bunların
Ayhan veya Havva adına kalıcı biçimde bağlanması ayrı speaker identification
katmanıdır.

## İlk karşılaştırma senaryosu

1. İlk 15 saniye yalnız Ayhan konuşsun.
2. Sonraki 15 saniye yalnız Havva konuşsun.
3. Birkaç kısa sırayla konuşma yapın.
4. Bir kez aynı anda konuşun.
5. Son 10 saniyede TV veya normal oda gürültüsü ekleyin.

Bakılacak ana belirtiler: aynı kişinin yeni bir konuşmacı numarasına bölünmesi,
iki kişinin tek numarada birleşmesi, sessiz/gürültülü bölümün konuşmacı sayılması
ve konuşma değişimlerinin gecikmesi.
