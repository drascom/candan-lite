# Canlı doğrulama — trnorm + kısa-metin guard + ses cache'i

**Tarih:** 2026-07-26, canlı oturum (`room=candan-lite-dev`, `job=AJ_uZP7gqW5EXCk`)
**Sonuç:** 25 Tem deploy'unun üç parçası da canlıda ÇALIŞIYOR. Doğrulama kapandı.

## Durum: deploy 2026-07-25 16:27'de yapılmıştı

`trnorm.py`, `tts_cache.py`, `omnivoice_tts.py` sunucuda; md5'leri lokalle birebir aynı.
`go.sh` bilerek gönderilmedi (Mac'e özel; systemd zaten `agent.py dev` çalıştırıyor).
Sunucuya rsync'lenen `worker/tests/` 26 Tem'de silindi.

## Doğrulananlar

| Parça | Kanıt |
|---|---|
| **Ses cache'i** | `worker/data/tts-cache/` oluştu, 16 `.pcm` birikti. Log: `TTS cache HIT (151680 bayt): 'Sesin kaydedildi, artık seni tanıyorum.'` ve `HIT (120960 bayt): 'Tanıştığımıza memnun oldum!'` — ikisi de `pi_brain` scripted kalıbı, cache'in hedef sınıfı. |
| **Kısa-metin guard** | 20:51:38 — "Tek kelimeyle merhaba der misin?" → `Merhaba.` **sesli geldi** (kullanıcı kulakla teyit etti). Bench'te bu sınıfta %27 boş çıktı vardı. |
| **Çökme yok** | 30 dk log taraması: `ZeroDivisionError`, `no audio frames`, `APIError`, traceback → **hiçbiri yok**. |
| **`pronounce_tr.json`** | 6 giriş, hepsi saf harf (`bugün`, `rica`, `gerçek`, `şu an`, `şimdi`, `işine`). Sayı/yüzde girişi ve `on`/`bin`/`yüz` gibi kısa token YOK → trnorm ile çakışma yok. Uyarılan "yüzde yüzde" riski gerçekleşmiyor. |

**Guard doğrulamasının sınırı:** beyin metni zaten `"Merhaba."` diye noktalı üretti, yani
"noktalama ekle" katmanının tetiklendiği kanıtlanmadı — tehlikeli sınıf noktalamasız tek
kelimeydi. Pratik sonuç yine de olumlu: tek kelimelik yanıt sessiz kalmadı.

**GÜNCELLEME 21:00 — guard katmanı doğrudan kanıtlandı.** Logda tetiklendiği görüldü:
```
DEBUG omnivoice_tts - TTS: kısa metne cümle sonu noktası eklendi:
  '(Candan kelimesi geçmediği için yanıt vermiyor.)' → '(Candan kelimesi geçmediği için yanıt vermiyor.).'
```
Yan not: metin `.)` ile bitiyordu, guard yine de nokta ekleyip `.).` üretti — son karakter
kapanış parantezi olduğu için "noktalama yok" sayılıyor. Zararsız ama gereksiz.

## YENİ HATA — wake-gate notu SESLİ okunuyor

Wake-word kapısı devredeyken beyin şu metni **assistant cevabı olarak** üretiyor ve TTS
onu seslendiriyor (21:00-21:02 arası **12 kez**, cache HIT ile bedava tekrar):
```
21:01:36  assistant: "(Candan kelimesi geçmediği için yanıt vermiyor.)"
21:01:51  assistant: "(Candan kelimesi geçmediği için yanıt vermiyor.)"
21:02:02  assistant: "(Candan kelimesi geçmediği için yanıt vermiyor.)"
```
Parantez içi bu ifade sessiz kalması gereken bir İŞARET, seslendirilmemeli.

- Metin **kod tabanında YOK** (`grep` → yalnız `worker/logs/transcript.log`'ta) → beynin
  kendi ürettiği yanıt, muhtemelen prompt kaynaklı.
- 25 Tem deploy'unun sebep olduğu bir gerileme DEĞİL; guard yalnız sonuna nokta ekliyor.
  Ama cache'lendiği için artık anında ve bedavaya tekrarlanıyor → daha görünür.
- Olası çözüm iki yerden biri: (a) prompt "yanıt verme" durumunda BOŞ döndürsün,
  (b) `omnivoice_tts` bu kalıbı tanıyıp TTS'i atlasın. (a) daha doğru görünüyor.
- **Not:** kalıp cache'e girdiği için düzeltmeden sonra `data/tts-cache/` temizlenmeli.

## Açık kalan üç iş (TTS'ten bağımsız)

### 1. `pi_brain` compaction turu yutuyor  ← kullanıcıyı doğrudan etkileyen tek sorun
```
20:50:23  kullanıcı: "Türkçe desteği var mı bu modelin?"
20:50:25  pi compaction BAŞLADI (reason=threshold) → "sessiz (cevap zaten akmıştı)"
20:50:36  kullanıcı: "Anladın mı?"      ← compaction sürerken düştü
20:50:46  pi compaction BİTTİ           (21 saniye)
```
O pencerede **hiç TTS çağrısı yok** — beyin metin üretmedi. Yani sessizliğin sebebi TTS
değil, compaction. `→ sessiz (cevap zaten akmıştı)` kararı 20:50:23'teki soruyu yuttu.

### 2. Kısa turlarda kimlik "Bilinmeyen"e düşüyor
Tur-güvenli kimlik 2 ardışık onay istiyor; kısa cümle tek pencere üretiyor:
```
"Anladın mı?"    → yetersiz ardışık onay (1/2), kabul=1/1
"Burada mısın?"  → bu dönüşte güvenli ses penceresi yok, kabul=0/0
```
Tanıma çalışmıyor değil (aynı anda `Ayhan skor=0.395` / `-0.836` okundu) — eşik kısa turlar
için katı. Uzun cümlelerde sorunsuz. Bkz. `docs/TURN-SAFE-SPEAKER-IDENTITY.md`.

### 3. GPU çekişmesi
Tek RTX 3090'da dört tüketici, **22029 / 24576 MiB**:

| Süreç | VRAM |
|---|---|
| `llama-server` gemma-4-12b-qat-q4_0 (beyin) | 9556 MiB |
| `bridge_server.py` OmniVoice (TTS) | 9806 MiB |
| `wyoming_faster_whisper` large-v3-turbo (STT) | 2386 MiB |
| worker | 256 MiB |

Üçü zaten AYRI süreç; süreç eklemek kazanç sağlamaz — tek GPU farklı CUDA context'lerini
varsayılan olarak zaman dilimler, eşzamanlı koşturmaz. Ayrıca bir turun içinde
STT → beyin → TTS zaten sıralı olmak zorunda. `nvidia-smi`'deki %100 yanıltıcı ("aralıkta en
az bir kernel çalıştı" demek). Gerçek kaldıraç: TTS'in işini azaltmak
(§1.B referans kısaltma, ~2×) veya bir servisi başka makineye almak.

## Düzeltme: bellek sızıntısı YOK

İlk bakışta "654 MB büyüme" alarm verdi, yanlış okumaydı:
```
uptime 275 sn → büyüme 653.3 MB
uptime 395 sn → büyüme 654.7 MB      (120 sn'de +1.4 MB)
```
Tek seferlik model yüklemesi, sonrası düz. Yapılacak bir şey yok.

## Hâlâ onay bekleyen

**Referansı ~4 sn'ye kısaltma** (`handoff/2026-07-25-tts-arastirma-ve-server-adimlari.md` §1.B)
— ölçülmüş ~2× TTS hızlanması, kalite düşmüyor. Karar verilmedi.
Not: cache anahtarı pinned referansın parmak izini içeriyor → uygulanırsa cache kendiliğinden
geçersizleşir, yeniden dolar.
