# 2026-07-22 ilk A/B sonucu

## Girdi

30,4 saniyelik sentetik sıralı WAV: Ayhan serbest (yaklaşık 2 sn) → Havi
serbest (yaklaşık 18 sn) → Ayhan neşeli (yaklaşık 2 sn) → Havi neşeli
(yaklaşık 5 sn). Aralarda 0,8 sn sessizlik vardır. Dosya yalnız var olan
ifade örneklerinin ardışık birleştirilmiş halidir.

## Anonim diarization

CrispASR 0.8.21 CPU derlemesi, Whisper base çok dilli model ve pyannote GGUF
ile dört bölümü tutarlı iki anonim kümeye ayırdı: Ayhan `speaker 0`, Havi
`speaker 1`. İlk soğuk çalıştırmada indirilen modellerden sonra 30,4 saniyelik
dosyanın işlenmesi 2,88 saniye sürdü (10,5x gerçek zaman).

## Kapalı-roster adlandırma

Testteki serbest/neşeli dosyalardan farklı, kısa üzgün referanslarla profil
çıkarıldı. Varsayılan `0.70` eşiğinde iki küme adsız kaldı:

| Küme | En iyi cosine | Sonuç |
| --- | ---: | --- |
| Ayhan | 0,67 | adsız |
| Havi | 0,64 | adsız |

`--speaker-threshold 0.60` ile iki küme de doğru isimlendi; dört konuşma
bölümünün tamamı beklenen Ayhan/Havi sırasına geldi. Bu yalnız iki kişilik
kapalı-küme ve mevcut kayıtlar üzerinde bir uygunluk sinyalidir; bilinmeyen
seslerle yanlış-adlandırma oranını ölçmediği için canlı kimlik doğrulamasına
geçiş için yeterli değildir.

## Sonuç

CrispASR, bizim asıl sorunumuz olan aynı STT dönüşündeki ardışık konuşmacı
değişimini dosya üzerinde ayırabiliyor. Buna karşın kendi named-profile yolu
yalnız kaydedilmiş dosyalar içindir; canlı/streaming kimlik doğrulaması
desteklemez. Bu yüzden şu aşamada onu canlı worker'ın yerine koymuyoruz.

## 2026-07-22 GPU ve STT yükseltmesi

İlk canlı test, 147 MB'lık Whisper `base` modeliyle açılmıştı. Konuşmacı
ayrımı doğru olmasına rağmen Türkçe metin kalitesi kabul edilebilir değildi.
Sunucudaki CUDA derlemesi (`sm_86`, RTX 3090) tamamlandıktan sonra hizmet,
1,62 GB çok dilli `ggml-large-v3-turbo.bin` ile yeniden başlatıldı. Türkçe'de
bozuk büyük-harf ve çift-nokta üreten FireRed noktalama son-işlemesi de
`--punc-model none` ile kapatıldı.

Aynı 30,4 saniyelik fixture, GPU sunucusunda 1,75 saniyede işlendi ve metin
"Merhaba ben evin babasıyım", "Kendimi anlatayım", "Duş alıyorum" gibi doğru
Türkçe çıktı üretti. Ayırma yine iki anonim küme olarak korundu. Hizmetin boşta
GPU kullanımı yaklaşık 2,06 GB, diarization isteği sırasında yaklaşık 2,36 GB
oldu.
