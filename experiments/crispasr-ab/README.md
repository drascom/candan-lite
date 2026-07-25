# CrispASR A/B deneyi

Bu klasör, canlı `worker/` hattına dokunmadan CrispASR ile uçtan uca
ASR + VAD + diarization denemesi yapar. Amaç, aynı mikrofon kaydındaki
Ayhan/Havi sıra değişimini CrispASR'ın tek başına ayırıp ayıramadığını ölçmektir.

## Kapsam

1. `run_diarization.sh`: CrispASR'ın anonim, oturum-içi `A`/`B` konuşmacı
   segmentlerini üretir. Profil veya kalıcı biyometrik veri yazmaz.
2. `run_closed_roster.sh`: yalnız kayıtlı bir ses dosyası için, CrispASR'ın
   kendi kapalı-roster profil özelliğini dener. Bu, `Ayhan,Havi` dışında
   kimseyi taramaz ve canlı/streaming tanımlama yapmaz.

Üretimdeki WeSpeaker, Wyoming Whisper ve OmniVoice bu deneyin parçası değildir.

## Kurulum (uzak test makinesi)

`setup_remote.sh`, CrispASR'ı deney klasörünün altındaki `vendor/` dizinine
klonlar. Sunucuda `nvcc` varsa CUDA, yoksa CPU arka ucuyla derler. Bu işlem
model indirmez ve systemd servisi kurmaz.

```bash
cd /opt/candan-lite/experiments/crispasr-ab
./setup_remote.sh
```

Ardından model seçimini önce kontrol edin:

```bash
vendor/CrispASR/build/bin/crispasr --backend whisper -m auto --dry-run-resolve
```

`auto`, çok dilli `ggml-base.bin` Whisper modeline çözülür; `-l tr` ile Türkçe
çalışır. Kalite denemesinde daha büyük çok dilli bir Whisper GGUF modelini
`CRISPASR_MODEL=/mutlak/yol/model.gguf` ile aşağıdaki betiklere verebilirsiniz.

## Test sırası

Yerel Mac'te mevcut ifade örneklerinden sıralı bir fixture üretin:

```bash
./prepare_fixture.sh
scp fixtures/ayhan-havi-sequential.wav root@192.168.0.25:/opt/candan-lite/experiments/crispasr-ab/fixtures/
```

Uzak makinede anonim diarization:

```bash
export CRISPASR_BIN=/opt/candan-lite/experiments/crispasr-ab/vendor/CrispASR/build/bin/crispasr
export CRISPASR_MODEL=auto
./run_diarization.sh fixtures/ayhan-havi-sequential.wav
```

Kapalı-roster profili denemesi (yalnız Ayhan/Havi örnekleri için):

```bash
# Fixture'da kullanılan serbest/neşeli örnekler yerine ayrı (üzgün) referanslar:
export AYHAN_REFERENCE=/opt/candan-lite/worker/data/offline-speaker-eval-20260722/ayhan/20260718T193500/02-uzgun.wav
export HAVI_REFERENCE=/opt/candan-lite/worker/data/offline-speaker-eval-20260722/havi/20260718T204605/02-uzgun.wav
./run_closed_roster.sh fixtures/ayhan-havi-sequential.wav
```

`CRISPASR_SPEAKER_THRESHOLD` varsayılan olarak `0.70`'tir. Daha düşük bir
eşik, farklı duygu/mesafedeki aynı kişiyi daha kolay eşler; buna karşılık
bilinmeyen bir konuşmacıyı yanlış adlandırma olasılığını artırır. A/B için
ayrıca verin, üretim varsayımı yapmayın:

```bash
CRISPASR_SPEAKER_THRESHOLD=0.60 ./run_closed_roster.sh fixtures/ayhan-havi-sequential.wav
```

Sonuçlar `runs/` altında JSON olarak kalır. Başarı ölçütü: tek dosyada sıralı
konuşmacı değişimlerinde en az iki ayrı segment/etiket üretmesi; ikinci aşamada
etiketlerin kapalı roster içinde doğru isimlere dönmesidir.

## Bilinen sınır

CrispASR'ın kendi dokümantasyonu kapalı-roster adlandırmayı yalnız kayıtlı
dosyalarda destekler; canlı veya streaming kimlik doğrulamasını desteklemez.
Bu nedenle başarılı sonuç, canlı worker'a geçiş kararı değil; yalnız teknik
uygunluk kanıtıdır.

## Mac'ten canlı test

GPU ikilisi derlendikten sonra sunucuda yalnız bu deneye ait
`crispasr-ab.service` başlatılır. Eski Candan/Whisper/OmniVoice servisleri bu
servisin parçası değildir.

Mac'te, proje kökünden aşağıdaki istemciyi çalıştırın:

```bash
worker/.venv/bin/python experiments/crispasr-ab/live_client.py turn
```

İstemci önce Enter ile kaydı başlatır, ikinci Enter ile kapatır ve oluşan tek
WAV'i sunucuya yollar. Ayhan ve Havi'nin aynı kayıtta sırayla konuşması gerekir;
çıktıda anonim `A` / `B` / `C` segmentleri görünür. Varsayılan üst sınır dört
konuşmacıdır; daha kalabalık bir kayıt için `--max-speakers 6` ekleyebilirsiniz.
Bu mod, tüm konuşma dönüşünü gönderdiği için diarization testi içindir.

Yerel Gemma beyni çalışıyorsa, anonim etiketleri sabit tutarak yalnız bariz
STT hatalarını konuşma bağlamıyla düzeltmeyi de deneyebilirsiniz:

```bash
worker/.venv/bin/python experiments/crispasr-ab/live_client.py turn --context
```

Bu ikinci çıktı kaynak transkript değil, LLM önerisidir; kişi kimliği ve
konuşmacı ayrımı için CrispASR çıktısı esas alınır.

Yalnız anlık metin akışını görmek için şunu kullanın:

```bash
worker/.venv/bin/python experiments/crispasr-ab/live_client.py stream
```

`stream` Whisper'ın WebSocket akışıdır ve anlık Türkçe metin verir; CrispASR'ın
bu uç noktası konuşmacı diarization'ı yapmaz. Adla Ayhan/Havi eşleme de canlı
protokolde desteklenmez; CrispASR bunu yalnız kayıtlı dosya ve kapalı roster
için sunar.
