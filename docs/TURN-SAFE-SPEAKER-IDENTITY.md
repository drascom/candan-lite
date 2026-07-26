# Turn-safe speaker identity

## Güvenlik kuralı

Her kullanıcı konuşması yeni bir kimlik dönüşüdür. Önceki dönüşte doğrulanan
`SpeakerState.current`, yeni dönüş için delil sayılmaz.

Akış:

1. Dönüş, önceki dönüş çözüldükten SONRAKİ ilk `speaking` geçişinde açılır; eski
   görünür kimlik o an sıfırlanır.
2. SpeakerTap yalnız bu dönüş sırasında ve agent yankı kapısından geçen pencereleri
   toplar.
3. Final STT geldiğinde aynı ismin iki ardışık kabul penceresi aranır.
4. Aynı dönüşte Ayhan ve Havi sonuçları karışmışsa karar `Bilinmeyen` olur.
5. Yeterli kanıt yoksa önceki isim/persona taşınmaz; genel oturum kullanılır.
6. Kimlik soruları ve `Ben X'im` iddiaları LLM'e bırakılmaz; worker deterministik
   cevap verir. Sözlü isim iddiası ses kararını değiştiremez.

## Dönüş sınırı = final transkript, VAD parçası DEĞİL (26 Tem düzeltmesi)

`begin_turn()` LiveKit'in `user_state_changed -> speaking` olayına bağlıdır. Tek bir
kullanıcı dönüşünde VAD birden çok kez `speaking↔listening` yapar — cümle içi doğal
duraklar VAD'in sessizlik eşiğini aşar. Eskiden her `speaking` kanıtı SIFIRLIYORDU,
yani karar yalnız SON konuşma parçasından veriliyordu ve o parça çoğu kez ilk
pencerenin oluşmasına yetmiyordu.

Canlı ölçüm (26 Tem, 72 dönüş): **%75 `Bilinmeyen`**, bunun en büyük kalemi
`kabul=0/0` (30 dönüş). Aynı dönüşte pencere seviyesinde tanıma sorunsuzdu
(150 pencere, Ayhan medyan skor 2.09). Örnek — dört pencerede tanındı, karar boş:

```text
20:57:11  speaker-ID tanındı: Ayhan (skor=-0.185)
20:57:12  speaker-ID tanındı: Ayhan (skor=0.855)
20:57:13  speaker-ID tanındı: Ayhan (skor=1.995)
20:57:14  speaker-ID tanındı: Ayhan (skor=3.075)
20:57:14  speaker turn kararı: Bilinmeyen (sebep=... kabul=0/0)
```

Dönüşün gerçek konuşma süresine göre dağılım (93 dönüş, `last_speaking_time -
speech_start_time`):

| karar | <1 sn | 1-2 sn | 2-3 sn | 3-5 sn | ≥5 sn |
|---|---|---|---|---|---|
| tanındı | 0 | 4 | 8 | 10 | 10 |
| 0 pencere (`kabul=0/0`) | 14 | 2 | 2 | 5 | 6 |
| 1 pencere (`1/2`) | 7 | 14 | 1 | 1 | 3 |

**≥2 sn konuşulan 18 dönüş hiç veya tek pencere üretti** — süre yetiyordu, tamponu
VAD yeniden-tetiklemesi siliyordu. `<1 sn` sütunu ("Evet.", "Sessiz olun.")
`SPEAKER_MIN_SECONDS` altındadır ve tasarım gereği `Bilinmeyen` kalır; bu dönüşler
ancak süreklilik kuralıyla (aynı kişi, 12 sn) ad alabilir.

Artık aktif dönüş içindeki `begin_turn()` çağrıları NO-OP'tur; dönüşü yalnız
`resolve_turn()` (final transkript) kapatır. Güvenlik kuralı bozulmaz:

- Önceki dönüşün kimliği hâlâ taşınmaz (dönüş kapanınca kanıt silinir).
- Çelişki kuralı ZAYIFLAMAZ, güçlenir: dönüşün tamamı görüldüğü için ikinci bir
  konuşmacı artık gizlenemez.
- Ek koruma: onay grubunun son penceresi final transkriptten `TURN_MAX_SECONDS`'tan
  eski olamaz (takılı kalmış uzun dönüşte bayat kanıt kullanılmasın).

## Neden "yüksek skorlu tek pencere" kabul EDİLMİYOR

Denendi ve veriyle ELENDİ. Aynı canlı logda, Ayhan konuşurken yanlış kişi (Havi)
pencereleri **0.75 / 1.47 / 2.06 / 2.62** skorlarıyla kabul edildi; Ayhan'ın kendi
medyanı **2.09**. Yani skor, doğru ile yanlış pencereyi ayırmıyor — "skor ≥ 2.0 ise
tek pencereye güven" kuralı en az iki dönüşü yanlış kişiye atardı. Yanlış pozitife
karşı işe yarayan tek şey ARDIŞIK ONAY sayısıdır; çözüm bu yüzden eşik gevşetmek
değil, dönüş başına kanıt SAYISINI artırmak oldu.

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
SPEAKER_TURN_CONFIRM_HITS=2     # kod varsayılanı da 2 (agent.py)
SPEAKER_TURN_MAX_SECONDS=8
SPEAKER_CONTINUITY_SECONDS=12
SPEAKER_MIN_SECONDS=1.0         # hop = ilk pencerenin süresi
SPEAKER_WINDOW_SECONDS=1.5      # sonraki kayan pencerelerin süresi
```

İki pencere güvenlik odaklıdır; tek pencerelik yanlış pozitif kimlik veremez. Çok
kısa sözler (`SPEAKER_MIN_SECONDS` altı) `Bilinmeyen` kalır — bu, eski kişinin
yanlış taşınmasından daha güvenlidir.

**Kanıt zamanlaması.** Tap her `SPEAKER_MIN_SECONDS`'ta bir pencere üretir. İlk
pencere `SPEAKER_MIN_SECONDS` dolar dolmaz çıkar (eskiden `SPEAKER_WINDOW_SECONDS`
beklenirdi), sonrakiler kayan `SPEAKER_WINDOW_SECONDS` uzunluğundadır:

| dönüşteki konuşma | eski kanıt | yeni kanıt |
|---|---|---|
| 1.2 sn | 0 pencere → `kabul=0/0` | 1 pencere |
| 2.1 sn | 1 pencere → `1/2` | 2 pencere → **tanınır** |
| 3.1 sn | 2 pencere | 3 pencere |

Bu süre artık dönüşün TAMAMI üzerinden sayılır (cümle içi duraklar kanıtı silmez).

## Test ve deploy

```bash
worker/.venv/bin/python -m unittest discover -s worker/tests -v
./scripts/deploy-turn-identity.sh
```

Deploy, canlı dosyaları `/opt/candan-lite/.deploy-backups/` altında saklar ve
başlatma/test hatasında worker'ı otomatik geri yükler. STT backend seçimini
değiştirmez; MOSS aktifse MOSS olarak kalır.
