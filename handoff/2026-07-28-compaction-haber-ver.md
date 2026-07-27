# COMPACTION: haber ver + o sırada komut alma — 28 Tem

Kullanıcının cümlesi:

> "Sistem compaction yaparken haber verse, ben durumdan haberdar olup bitmesini
> beklerim. Hatta compaction'a girdiğinde yeni sesli komut almasın."

Bugüne kadar sıkıştırma **arka planda ve sessizce** çalışıyordu. Kullanıcı bunu
"sistem yavaşladı / cevap vermiyor" diye yaşıyor, sebebini bilmiyordu.

---

## 1. ÖNCE ÖLÇÜM — compaction gerçekte kaç saniye sürüyor?

**Bu ilk kez ölçüldü.** Worker log'unda yalnız BAŞLANGIÇ satırı vardı; bitiş satırı
yoktu (tur sonu devrinde `compaction_end` olayı `_route_output` tarafından
YUTULUYOR — bkz. §4). Ölçüm bu yüzden `candan-brain` tarafından yapıldı: sıkıştırma,
pi'nın llama-server'a attığı **tek** istektir ve süresi `slot print_timing`
satırında birebir yazılıdır.

| tarih/saat | sıkıştırma isteği | prompt eval | üretim | **toplam** |
|---|---|---|---|---|
| 26 Tem 20:50 | 16 884 tk | 12.6 s | 8.1 s / 512 tk | **20.7 s** |
| 27 Tem 22:10 | 15 245 tk | 13.6 s | 7.6 s / 439 tk | **21.2 s** |
| 27 Tem 22:21 | 17 000 tk | 14.7 s | 8.5 s / 619 tk | **23.3 s** |
| 26 Tem 22:23 | 16 503 tk | 14.0 s | 9.6 s / 788 tk | **23.6 s** |
| 27 Tem 13:52 | 17 187 tk | 16.4 s | 16.9 s / 1259 tk | **33.3 s** |
| 27 Tem 12:55 | 15 805 tk | 17.2 s | 18.0 s / 1083 tk | **35.2 s** |
| 27 Tem 14:07 | 16 398 tk | 23.2 s | 38.3 s / 1293 tk | **61.5 s** |

**Ortanca 23.6 sn · tepe 61.5 sn · taban 20.7 sn.**

Worker'ın penceresi bu süreyle örtüşüyor (14:07:17.2 `compaction_start` →
14:08:18.7 sıkıştırma isteği bitişi; 22:09:56.7 → 22:10:17).

Bağlam küçülmesi (aynı loglardan, `gecikme-ve-stall.md`'de de var):
28 787 → 15 305 ve 29 646 → 15 043 token (≈ **−%48**).

**Tasarım kararı bu sayıdan çıktı.** 3 saniye olsaydı "bir saniye" demek yeterdi;
yarım dakika (bazen bir dakika) için kullanıcının **bilerek beklemeyi seçebilmesi**
gerekiyor → süreyi söyleyen bir başlangıç cümlesi + bitiş işareti + o pencerede
komut kabul etmeme.

### Kullanıcının yaşadığı tablo (canlı log, 27 Tem 14:07)

```
14:07:17  tur sonu compaction (61.5 sn sürecek) → ARKA PLANDA, kullanıcıya HABER YOK
14:07:19  kullanıcı konuştu → "Bir saniye, aklımı toparlıyorum."
14:07:35  kullanıcı konuştu → aynı cümle
14:08:14  "Beni duyuyor musun?"            → aynı cümle
14:08:20  "Ses kontrol deneme..."          → aynı cümle
14:08:42  tur stall: 12s ilerleme yok
```

Dört soru, dört kez aynı ara söz, bir stall. Kullanıcı ne olduğunu bilmiyor.

---

## 2. Yeni davranış

Sıkıştırma **ARKA PLANDA KALDI** (senkron yapmak 26 Tem'deki sessizlik hatasını geri
getirirdi — DEVİR §2). Değişen: pencere artık **görünür** ve **kapalı**.

| an | ne olur |
|---|---|
| sıkıştırma başlar | (eşiği aşarsa) **"Hafızamı toparlıyorum, yarım dakika kadar sürebilir. Bitince haber vereceğim."** |
| pencerede kullanıcı konuşur | tur pi'ya GİTMEZ, kısa işaret: **"Bir saniye, hâlâ toparlanıyorum."** |
| sıkıştırma biter | **"Tamam, hazırım."** |

Cümlelerin gerekçesi:

* **Başlangıç** süreyi SÖYLER ("yarım dakika kadar") — ölçülen ortanca 23.6 sn.
  Kullanıcının isteği zaten "haberdar olup bekleyeyim"di; beklemenin ne kadar
  olduğunu bilmeden bekleme seçilemez. "Bitince haber vereceğim" sözü, bitiş
  cümlesini bir söz haline getirir.
* **Bitiş** kısa: her sıkıştırmada tekrarlanacak. Testte kilitlendi —
  bitiş cümlesi başlangıçtan KISA olmalı.
* **Meşgul işareti** ayrı bir cümle: "hâlâ" kelimesi başlangıç cümlesini duymuş
  kullanıcıya "evet, aynı iş sürüyor" der.
* Hepsi **deterministik harness cümlesi** — modele bırakılmadı (`truth_check` ilkesi).

Üçü de `.env`'den değiştirilebilir (`PI_COMPACT_START_TEXT` / `_END_TEXT` /
`_BUSY_TEXT`).

### Eşik: `PI_COMPACT_NOTIFY_MIN_S=2`

Sıkıştırma bu süreden kısaysa **ne haber verilir ne tur reddedilir** — gürültü
olurdu. Aynı değer kapının "nezaket beklemesi"dir: pencereye denk gelen tur önce
bu kadar bekler, sıkıştırma o sırada biterse HİÇBİR ŞEY olmamış gibi normal
cevaplanır. Ölçülen sürelerde (en kısası 20.7 sn) bu neredeyse hiç olmayacak; kol
geleceğe dönüktür (pi hızlanırsa gürültü kendiliğinden susar).

### İki tuzak, ikisi de kapatıldı

1. **Sessizce yutma** — reddedilen tur MUTLAKA kısa bir işaret alır. Hiçbir şey
   duymayan kullanıcı sistemi bozuk sanar; asıl hata buydu.
2. **Kapının yapışması** — pencere üç ayrı yoldan fail-open kapanır:
   `compaction_end` olayı, izleyici görevin `force_settled()`'ı
   (`PI_COMPACTION_STALL_TIMEOUT=120` tavanı), ve tur bitiminde `finally` bloğu
   (tur stall/abort ile ölürse `compaction_end` hiç gelmeyebilir). Üçü de testli.

---

## 3. KAYBOLAN SORU mekanizması — KORUNDU (silinmedi)

DEVİR'deki mekanizma: tur sonu compaction turu arka plana devrederken kullanıcının
sorusu **hiç cevaplanmamış** olabilir (canlı 26 Tem 22:23: hava durumu sorusu
compaction'a denk geldi, tur metinsiz kapandı) → sıkıştırma bitince prompt **bir
kez** yeniden gönderilir.

**Karar: aynen duruyor. Gerekçe — kapı bu vakayı KAPSAMIYOR.**

| | kapı (yeni) | kaybolan soru (mevcut) |
|---|---|---|
| soru ne zaman soruldu | sıkıştırma **başladıktan SONRA** | sıkıştırma **başlamadan ÖNCE** |
| kullanıcı beklemeyi seçti mi | **evet** — işareti duydu, bilerek bekliyor | **hayır** — sorusu sessizce yutuldu |
| doğru davranış | reddet, "hâlâ toparlanıyorum" | cevabı BORÇLUYUZ → yeniden gönder |

Silinseydi 26 Tem 22:23 hatası geri gelirdi. Tek uyum: **bu turda "hazırım"
DENMEZ** — hemen ardından gelen cevabın önüne geçerdi; cevabın kendisi zaten
"döndüm" işaretidir (`_compact_resend` bayrağı).

Testle kilitlendi:
`CompactionGateTest.test_lost_question_is_still_resent_and_end_line_is_skipped`.

---

## 4. Kod — ne değişti

| dosya | ne |
|---|---|
| `worker/pi_brain.py` | `PI_COMPACT_*` sabitleri (+ `reload_settings()`); `PiRpcClient.compact_begin/compact_end/compacting/wait_compact_done`; `PiStream._compact_gate` + `_compact_mark`; `PiBrain.set_announcer/_announce/_watch_compaction/_compaction_watch`; tur bitiminde fail-open |
| `worker/agent.py` | `brain.set_announcer(...)` → `session.say` (tur DIŞI söz) |
| `worker/.env.example` | beş yeni kol + ölçüm tablosu notu |
| `worker/tests/test_compaction_background.py` | +9 test (`CompactionGateTest`); eski iki sınıf artık `PI_COMPACT_GATE_ENABLED=false` altında koşuyor = "bayrak kapalıyken bugünkü davranış" regresyonu |
| `worker/tests/test_env_kollari.py` | +3 test, yeni kolların ÖLÜ olmadığı |

Tasarım notları:

* **Pencere durumu `PiRpcClient`'ta** (PiBrain'de değil): pencere turları AŞAR,
  tur nesneleri gelip geçicidir.
* **Bitiş cümlesini tur DIŞI bir görev söyler.** Sıkıştırma bir turun İÇİNDE başlar
  ama DIŞINDA biter — "hazırım" derken açık bir LLM akışı yoktur. Bu yüzden
  `set_announcer` → `session.say`. Bağlanmazsa (testler, eski çağıranlar) haber
  verme sessizce kapalı kalır.
* **Süre log'u bayraktan bağımsız**: kapalıyken de yazılır, ölçüm kaybolmasın.
  Yeni satır:
  `pi arka plan compaction BİTTİ: 23.4 sn (reason=threshold haber=evet)`
  Tur İÇİ sıkıştırmada: `pi compaction bitti: 23.4 sn (aborted=... willRetry=...)`.
* Kapı, pi'ya giden yolun EN BAŞINDA ama **scripted yolların SONRASINDA**: wake,
  kimlik, kayıt, sıfırlama, kesme-devamı beyne gitmez → sıkıştırma sürerken de
  çalışmaya devam eder.

---

## 5. Testler

| takım | sonuç |
|---|---|
| `python -m unittest discover -s tests` | **399/399 OK** (birleştirme sonrası 387 idi → +12) |
| `python pi_brain.py {name,soul,proactive,policy,enrollorder,waketimer,wake,reset,compaction,rotate}` | 10/10 PASS |
| `python truth_check.py` | 12/12 PASS |
| `tests/test_go_readiness.sh` | 34/34 TEMİZ |
| `./check.sh` | **YENİ BULGU YOK** (aynı 4 bulgu; ruff `.claude/worktrees/` kopyasını da taradığı için 8 görünüyor) |

Kilit testler: pencerede tur reddi + kısa işaret · pencere bitince normale dönüş ·
eşik altında ne haber ne ret · bayrak kapalıyken tek haber bile yok · kaybolan soru
kararı · takılan sıkıştırmada kapının açılması · tur ölürse pencerenin kapanması ·
sürenin ölçülmesi.

---

## 6. `.env` kolu — ÖLÜ DEĞİL, SUNUCUDA ÖLÇÜLDÜ

`agent.py`'nin yükleme sırası birebir tekrarlandı (gerçek `.env`'e YAZILMADAN,
`dotenv.load_dotenv` yamalanıp `/tmp/compact-test.env` okutularak):

```
1) systemd ortaminda var mi : None      ← systemd .env YÜKLEMİYOR (beklenen)
2) import anindaki modul deg : True / 2.0   ← "ölü kol" tuzağı tam burada
3) .env okundu, environ      : false
4) reload_settings ONCESI    : True
5) reload_settings SONRASI   : False / 9.0 / 'Bir dakika.'   ← KOL ÇALIŞIYOR
```

---

## 7. Deploy — YAPILDI (27 Tem 23:33)

**Sadece `candan-worker` restart edildi.** `pi-service`, `higgs-tts`,
`candan-brain` ELLENMEDİ.

Yedek: `/opt/candan-lite/.deploy-backup-20260728-compaction/`
(`pi_brain.py`, `agent.py`, `.env`, `.env.example`).

```bash
scp worker/pi_brain.py worker/agent.py worker/.env.example root@192.168.0.25:/opt/candan-lite/worker/
ssh root@192.168.0.25 'systemctl restart candan-worker'
```

Doğrulama:
- md5 sunucu = yerel: `pi_brain.py` `e7e56e5a…`, `agent.py` `7419b9f9…`
- import: `PI_COMPACT_GATE_ENABLED=True`, `PI_COMPACT_NOTIFY_MIN_S=2.0`,
  `PiStream._compact_gate` / `PiBrain._watch_compaction` / `set_announcer` var
- `registered worker {"agent_name": "candan"}` — açılış temiz, **traceback YOK**
  (`non-zero exit code 255` satırı restart gürültüsü: son 2 günde 27 kez, yeni değil)
- dört servis de `active`

### TEK BLOK GERİ DÖNÜŞ (telefondan çalıştırılabilir)

```bash
ssh root@192.168.0.25 'set -e
D=/opt/candan-lite/.deploy-backup-20260728-compaction
cp -a $D/pi_brain.py $D/agent.py $D/.env.example /opt/candan-lite/worker/
systemctl restart candan-worker
sleep 5; systemctl is-active candan-worker'
```

Daha dar geri dönüş (kod yerinde kalır, yalnız davranış bugünküne döner):

```bash
ssh root@192.168.0.25 'printf "\nPI_COMPACT_GATE_ENABLED=false\n" >> /opt/candan-lite/worker/.env && systemctl restart candan-worker'
```

Ayar (haber ver ama komut kabul etmeye devam et → mümkün değil; ikisi tek bayrak.
Yalnız cümleleri değiştirmek için `PI_COMPACT_START_TEXT` vb., eşiği büyütmek için
`PI_COMPACT_NOTIFY_MIN_S=30`).

---

## 8. Kulakla bakılacak (kullanıcı)

1. Sıkıştırma sırasında **başlangıç cümlesi** duyuluyor mu, doğal mı? Süre iddiası
   ("yarım dakika kadar") rahatsız edici mi — ölçülen tepe 61.5 sn, yani bazen
   yalan gibi duyulabilir.
2. **"Tamam, hazırım."** — sohbeti kesiyor mu, yoksa yerinde mi?
3. Pencerede konuşunca **"Bir saniye, hâlâ toparlanıyorum."** — tek seferde
   anlaşılıyor mu, yoksa tekrar tekrar duyulup sinir bozucu mu oluyor?
4. Sorunun **kaybolmadığından** emin ol: sıkıştırma bitince ya cevabı geliyor
   (kaybolan-soru yolu) ya da yeniden sorman gerekiyor (kapı yolu). İkincisi
   rahatsız ediciyse eşik/karar tekrar konuşulur.

İzleme:
`journalctl -u candan-worker -f | grep -E "compaction BİTTİ|compaction penceresi|HABER"`
