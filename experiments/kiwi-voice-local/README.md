# Kiwi Voice — yerel mikrofon speaker-ID deneyi

Bu klasör Kiwi Voice'un yalnız `kiwi.speaker_id.SpeakerIdentifier` çekirdeğini
ölçer. OpenClaw, Candan worker, ev sunucusu, TTS ve canlı konuşmacı veritabanı
kullanılmaz. Deney profilleri yalnız `profiles/` altında tutulur ve Git'e girmez.

Upstream sabitlenmiş revizyon: `1da13fdaccb99ad32b72fc0d6cb5619953a5b468`.
Kiwi'nin paketleme backend'i güncel pip/setuptools ile kurulamadığı için `setup.sh`
bu revizyonu Git-ignore edilen `.upstream/` dizinine alır; canlı projeye vendor etmez.

## Kurulum

```bash
cd experiments/kiwi-voice-local
bash setup.sh
.venv/bin/python mic_test.py devices
.venv/bin/python mic_test.py doctor
```

`setup.sh` önce açık `pyannote/wespeaker-voxceleb-resnet34-LM` uyumluluk modelini,
hesap erişimi varsa ayrıca Kiwi'nin birebir `pyannote/embedding` modelini indirir.
Upstream model mevcutsa deney onu otomatik seçer; yoksa açık WeSpeaker'a döner.
Bundan sonra çıkarım yerel çalışır. Kiwi'nin basit spektral fallback'i geçerli
sonuç gibi görünmesin diye test aracı tarafından engellenir.

Upstream Kiwi'nin seçtiği eski `pyannote/embedding` Hugging Face'te gated modeldir.
Bu makinede erişim onaylandı ve gerçek upstream checkpoint başarıyla doğrulandı.
Model pyannote.audio `0.0.1` ve Torch `1.8.1` döneminden olduğu için güncel çalışma
zamanında uyumluluk uyarıları basar; buna rağmen 512 boyutlu embedding üretmiştir.

Erişim daha sonra verilirse farklı checkpoint şu şekilde seçilebilir; profillerin
embedding boyutlarını karıştırmamak için ayrı `KIWI_PROFILE_DIR` kullanılmalıdır:

```bash
KIWI_EMBEDDING_CHECKPOINT=/tam/yol/pytorch_model.bin \
KIWI_PROFILE_DIR=/tam/yol/ayri-profiller \
.venv/bin/python mic_test.py doctor
```

Deneyin varsayılan cosine kabul eşiği `0.40` olarak kalibre edilmiştir. Değiştirmek
için örneğin `KIWI_THRESHOLD=0.45 .venv/bin/python mic_test.py identify` kullanılır.

Mevcut Ayhan/Havva kayıtlarındaki 2026-07-17 upstream-model ölçümü:

- Ayhan centroid → kendi testleri: `0.668`, `0.637`, `0.700`
- Havva centroid → kendi testleri: `0.539`, `0.585`, `0.535`
- çapraz kişi: medyan `0.060`, maksimum `0.211`
- upstream `0.55` eşiği Havva için fazla sert; `0.40` iki kişide güvenli ilk eşik

Not: Kiwi bu haliyle diarization sistemi değildir; kayıtlı kişi profillerini sabit
cosine eşiğiyle tanımaya çalışır. Güncel hedef yalnız diarization ise önce
`../whisperlive-local/` deneyi kullanılmalıdır.

## Kullanım

En az iki kişiyi, farklı cümlelerle üçer kez kaydedin:

```bash
.venv/bin/python mic_test.py enroll Ayhan --owner --samples 3
.venv/bin/python mic_test.py enroll Havva --samples 3
.venv/bin/python mic_test.py identify
.venv/bin/python mic_test.py profiles
```

Belirli mikrofon için komutlara `--device N` eklenebilir. Bu araç Kiwi'nin profil
saklama ve cosine/eşik kararını açık WeSpeaker checkpoint'iyle gözlemlemek içindir;
sonuçlar daha sonra aynı WAV seti üzerinde Candan WeSpeaker + AS-Norm ile
karşılaştırılmalıdır.
