# Turn-safe speaker identity

## Güvenlik kuralı

Her kullanıcı konuşması yeni bir kimlik dönüşüdür. Önceki dönüşte doğrulanan
`SpeakerState.current`, yeni dönüş için delil sayılmaz.

Akış:

1. LiveKit VAD `speaking` durumuna geçtiğinde eski görünür kimlik sıfırlanır.
2. SpeakerTap yalnız bu dönüş sırasında ve agent yankı kapısından geçen pencereleri
   toplar.
3. Final STT geldiğinde aynı ismin üç ardışık kabul penceresi aranır.
4. Aynı dönüşte Ayhan ve Havi sonuçları karışmışsa karar `Bilinmeyen` olur.
5. Yeterli kanıt yoksa önceki isim/persona taşınmaz; genel oturum kullanılır.
6. Kimlik soruları ve `Ben X'im` iddiaları LLM'e bırakılmaz; worker deterministik
   cevap verir. Sözlü isim iddiası ses kararını değiştiremez.

MOSS'un `S01/S02` etiketleri bu isim kararına bağlanmaz. Bunlar yalnız tek STT
isteğindeki anonim diarization slotlarıdır.

## Görünür transkript

Worker, final `lk.transcription` stream'ine `candan.speaker` attribute'u ekler.
Terminal istemcisi bunu etiket olarak gösterir:

```text
Ayhan: Beni duyuyor musun?
Bilinmeyen: Kısa bir test.
Havi: Bugün nasılsın?
```

## Ayarlar

```dotenv
SPEAKER_TURN_CONFIRM_HITS=3
SPEAKER_TURN_MAX_SECONDS=8
```

Üç pencere güvenlik odaklıdır. Kısa cümleler `Bilinmeyen` olabilir; bu, eski
kişinin yanlış taşınmasından daha güvenlidir.

## Test ve deploy

```bash
worker/.venv/bin/python -m unittest discover -s worker/tests -v
./scripts/deploy-turn-identity.sh
```

Deploy, canlı dosyaları `/opt/candan-lite/.deploy-backups/` altında saklar ve
başlatma/test hatasında worker'ı otomatik geri yükler. STT backend seçimini
değiştirmez; MOSS aktifse MOSS olarak kalır.
