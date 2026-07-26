# GÖREV: semantik turn-detector'ı devreye al (konuşma ortası bölünme)

Sen bir WORKER'sın. İşi kendin yap. Bitince KISA rapor ver.

**Durum: SIRADA.** Referans kısaltma + compaction işleri bitince başlatılacak.
`worker/agent.py`'ye dokunuyor — compaction görevi de aynı dosyaya bakabilir, çakışmasın.

## SORUN
Kullanıcı cümle ortasında nefes alınca / duraklayınca tur kesiliyor, kalan kısım AYRI
kullanıcı turu olarak işleniyor. Canlı kanıt (2026-07-26):
```
20:49:35  "...konuşmamı kesmeden bekle heceleri birleştireceksin  ra on"   eot=0.433
20:49:39  "Speech Chat 9B"                                    ← 4 sn sonra AYRI tur
20:49:10  "Yanlış tercüme ettim.  Ra-un."   eot=0.2588 vs eşik 0.255  ← kıl payı
```

## KÖK SEBEP (tespit edildi, tekrar araştırma)
`worker/agent.py:248-256` — `AgentSession(...)` içinde `turn_detection` **hiç ayarlanmamış**.
Satır 255'teki yorum durumu zaten itiraf ediyor:
`# turn_detection: framework multilingual model (Faz 3) — şimdilik VAD tabanlı`

Framework varsayılanı LiveKit Cloud EOT modelini deniyor → **401 Unauthorized** →
zayıf yerel mini modele düşüyor:
```
WARNING livekit.agents - cloud turn detector failed
  (message='Invalid response status (401 Unauthorized)', retryable=False)
  → falling back to local mini model
```
Sunucuda `livekit-plugins-turn-detector` **kurulu DEĞİL**
(`pip list` → yalnız `livekit-plugins-silero` 1.6.5, `livekit-local-inference` 0.2.6).

VAD (`silero.VAD.load()`) suçlu DEĞİL — o yalnız ses var/yok diyor.
`endpointing_delay` şu an 0.3 sn; asıl telafi katmanı semantik model olmalı.

## YAPILACAK
1. `livekit-plugins-turn-detector`'ı **lokal venv'de** kur, sürümü `livekit-agents 1.6.5`
   ile uyumlu olsun. `worker/requirements.txt`'e ekle.
2. `worker/agent.py`'de `turn_detection=MultilingualModel()` ayarla (satır 255'teki ölü
   yorumu da güncelle/kaldır).
   ⚠️ Model **CPU'da (ONNX)** çalışmalı — GPU'da 22/24 GB dolu, karta yük BİNMEMELİ.
   Doğrula: model GPU'ya düşüyorsa CPU'ya zorla.
3. Model ağırlığı indirme adımı varsa (`python -m livekit.plugins.turn_detector download-files`
   benzeri) deploy notuna yaz.
4. `endpointing_delay`'e DOKUNMA — önce semantik model tek başına ölçülsün. Yetmezse
   ayrı tur olarak ayarlanır.

## DOĞRULAMA
- `python3 -m compileall worker/` temiz
- Import + session kurulumu hata vermeden çalışıyor (uygulamayı ÇALIŞTIRMADAN)
- Turn-detector'ın CPU'da olduğunu kanıtla
- Mevcut testler kırılmıyor

## KURALLAR
- **Sunucuya (192.168.0.25) yazma YOK.** Kurulum/deploy kullanıcıya bırakılacak, komutları YAZ.
- Uygulama çalıştırma, sesli/görsel test YOK.
- `git commit`/`push` YOK.
- Değişikliği DAR tut — turn detection dışına taşma.

## RAPOR
- Kurulan paket + sürüm, `requirements.txt` değişikliği
- `agent.py` değişikliği (satır)
- CPU'da çalıştığının kanıtı
- Deploy komutları (kullanıcı çalıştıracak): pip install + model indirme + restart
- Kullanıcının konuşarak teyit etmesi gereken TEK şey
