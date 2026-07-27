# 2026-07-28 — KAYIT SIRASI + iki dürüstlük hatası

Görev: `handoff/task-2026-07-28-kayit-sirasi.md`. Kanıt: 27 Tem 22:23-22:27 Aslı
denemesinin canlı dökümü.

⚠️ **DEPLOY EDİLMEDİ.** Bu iş ayrı bir git worktree'de yapıldı; aynı sırada başka
bir ajan da `worker/pi_brain.py`'a dokunuyordu (gecikme/stall işi). Deploy, iki dal
birleştirildikten SONRA yapılacak. Sunucuya hiçbir şey gönderilmedi, `.env`'e
dokunulmadı, `systemctl` çalıştırılmadı.

---

## A) Kayıt sırası TERS idi — artık "toplamayı başlat"

### Öncesi

```
22:25:40,131  enroll_speaker sinyali yakalandı (isim='Aslı')
22:25:40,668  kayıt havuzu BOŞ (gördü=0): toplayıcı hiç pencere görmedi
22:25:40,668  çekirdek < 3 → KAYIT YAPILMIYOR      → enroll REDDEDİLDİ
```

Sinyalden redde **537 ms**. Aslı daha tek kelime etmeden karar verilmişti.
`enroll_speaker` bir "şimdi karar ver" komutu gibi işliyordu.

### Sonrası

```
1. enroll_speaker çağrılır → havuz yetersizse RET YOK, karar ERTELENİR
   → worker "Şimdi sesini kaydediyorum. Lütfen normal bir sesle şunu söyle:
     Bugün kendimi iyi hissediyorum." der ve TOPLAMAYA devam eder
2. kişi konuşur → pencereler birikir (çekirdek ≥ 3 kuralı aynen)
3. SONRAKİ turun BAŞINDA (_enrollment_line) havuz yeniden yoklanır
   → çekirdek hazırsa YAZ + sonucu söyle
   → değilse üst sınıra kadar bekle; sınır dolarsa "Sesini alamadım."
```

**Üst sınır: 30 sn VEYA 4 tur** (hangisi önce dolarsa).
`SPEAKER_ENROLL_WAIT_S=30`, `SPEAKER_ENROLL_WAIT_TURNS=4`.

**NEDEN tur içinde `await` ile beklenmedi:** kayıt turunda modelin cümleleri
tampona alınıp ATILIYOR (`PiStream._run`, `enroll_buf`). 30 sn bloklamak
kullanıcıyı sessiz bırakır ve "şu cümleyi söyle" talimatını hiç duyurmazdı.
Bu yüzden karar TURLARA YAYILDI, bloklama YOK.

### Yol üstünde bulunan gizli hata

`_start_collect()` "idempotent, birikeni silmez" diye belgelenmişti — **değildi.**
`_select_enroll_core()` toplayıcıyı DURDURUYOR; hemen ardından gelen
`_start_collect()` de `_enroll_embs`i sıfırlıyordu. Yani her ret turunda havuz
çöpe gidiyordu. Artık sıfırlama TEK yerde: `_reset_enroll()`.

### Tek konuşmacı izi

Toplama sırasında `SpeakerState.current` ile çözülen kimlikler `_enroll_speakers`
kümesinde toplanıyor. Birden fazla kimlik görülürse ölçüm log'una uyarı düşüyor:

```
kayıt uyarısı: toplama sırasında 2 farklı kimlik çözüldü (Ayhan, Havi)
→ havuza başkasının sesi karışmış olabilir
```

Kayıt DURDURULMUYOR (çekirdek seçimi aykırıyı zaten atıyor); amaç ölçüm.

---

## B) truth_check yanlış alarmı

### Öncesi

```
[22:25:24] Candan: "Aslı, seni NOT ALDIM. Şimdi ses kaydınızı alacağım. Lütfen
           normal bir sesle: Bugün kendimi iyi hissediyorum, deyin.
           Aslında bunu kaydetmedim, kusura bakma."
```

Model bir şey kaydettiğini iddia etmiyordu — sihirbazın TALİMATINI veriyordu.
"not aldım" kelime listesine takıldı; o turda hiçbir araç çağrılmamıştı bile.

### Sonrası — BAĞLAM ŞARTI

Kayıt iddiaları ikiye ayrıldı:

* **YUMUŞAK** (sohbet dilinde de kurulabilir): `not aldım/ettim`, `kaydettim`,
  `kayıt ettim`, `kaydediyorum`, `ekledim`.
  → Düzeltme için o turda gerçekten bir **yazma aracı çağrılmış** olmalı.
  Araç yoksa **SUS** (log: `truth: yumuşak kayıt iddiası + turda yazma aracı YOK
  → düzeltme EKLENMEDİ`).
* **GÜÇLÜ** (sohbet dolgusu olarak kurulamaz — harness'ın geleceğine dair SÖZ):
  `aklımda tutacağım/tutuyorum/tutarım`, `aklıma yazdım`, `unutmam`,
  `unutmayacağım`, `hatırlatacağım/hatırlatırım`, `hatırlatma/alarm kurdum`,
  `not olarak ekledim`, `yazdım bile`.
  → Araçsız da düzeltilir. Ölçülmüş canlı vaka ("Aklımda tutacağım, merak etme."
  hiç tool çağrılmadan) böylece **KAYBOLMADI**.

Eylem iddiaları (`düzelttim`, `hallettim`…) DEĞİŞMEDİ — kanıt yok, dokunulmadı.

### 27 Tem'de kapatılan hata geri açılmadı

`memory_add` guest'te `isError` OLMADAN `"guest: hafıza yok, kaydedilmedi."`
dönüyor. Bu yol **Katman 2**'dir (metin işareti → `fail_reason` → `failed=True` →
`write_failure_line()`), yani `decide()`in **1. adımı**; bu turda daralttığımız
kelime-listesi dalına (3. adım) hiç ulaşmıyor. Testle kilitlendi:

* `worker/truth_check.py` → `(c) memory_add guest reddi (isError YOK) → Katman 2 devralır`
* `worker/truth_check.py` → `(d) memory_add isError=True → Katman 2 devralır`
* yanlış alarm kilidi: `(f) CANLI YANLIŞ ALARM: araç YOK + 'seni not aldım' → müdahale YOK`

---

## C) Model sahip olduğu aracı inkâr ediyordu

```
[22:24:07] Candan: "Ses tanıma özelliğini şu an için doğrudan bir araç olarak
                    çalıştıramıyorum..."
```

49 saniye sonra `enroll_speaker`ı çağırdı. Yani araç elindeydi.

**Kök sebep:** hiçbir prompt "bu araç sende VAR" demiyordu. `pi/AGENTS.md`'de
kayıt akışından hiç söz edilmiyordu; extension'ın `ENROLL_NOTE`'u ise akışı
**"Tanımadığın biriyle konuşuyorsan"** şartına bağlıyordu. Canlı sahnede tam
tersi vardı: kaydedilecek kişi (Aslı) yanlışlıkla **tanınmış** sayılmıştı ve
isteği **üçüncü bir kişi** (Ayhan) dile getirmişti → şart tutmadı → model
"yapamıyorum" dedi.

**Düzeltme (13 satır):**

* `pi/AGENTS.md` — yeni kısa bölüm: **"Elindeki yeteneği İNKÂR ETME"**.
  `enroll_speaker` SENDE VAR; kaydedilecek kişi sen olmak zorunda değil; sesi
  tanınan biri de kaydedilebilir; sonucu worker söyler, sen sus. (+16 satır)
* `pi/extensions/speaker-enroll/index.ts` — `ENROLL_NOTE`'un açılışı koşuldan
  çıkarıldı: "Ses kaydı yeteneğin VAR; 'yapamıyorum' deme… — sesi zaten tanınıyor
  olsa bile —". (net +2 satır)

`pi/personas/candan.md` DEĞİŞMEDİ: orası konuşma tarzı, yetenek tarifi değil.

---

## Değişen dosyalar (birleştirme için)

| Dosya | Bölüm |
|---|---|
| `worker/truth_check.py` | `_CLAIM_RECORD` → `_CLAIM_RECORD_SOFT` + `_CLAIM_RECORD_STRONG`; yeni `soft_record_claim()`; `TurnLedger.write_attempts()`; `decide()` 3. adım; dosya sonuna `_truth_test()` + `__main__` |
| `worker/pi_brain.py` | sabitler (`SPEAKER_ENROLL_WAIT_*`, 3 cümle); `PiBrain.__init__` (5 yeni alan); `_reset_enroll`; `_start_collect` (artık silmiyor); `_collect_loop` (kimlik izi); `_select_enroll_core` (tek-konuşmacı uyarısı); `_enroll_apply` → `_defer_enroll` + `_resolve_deferred_enroll` + `_enroll_finish`; `_enrollment_line` (ertelenmiş karar kontrolü); yeni `_enroll_order_test()`; `_policy_test()` onarımı; `__main__` |
| `pi/AGENTS.md` | dosya SONUNA yeni bölüm |
| `pi/extensions/speaker-enroll/index.ts` | `ENROLL_NOTE` açılış cümlesi |
| `handoff/2026-07-27-DEVIR.md` | §4 madde 3 |

`worker/speaker_tap.py` DEĞİŞMEDİ.

## Test

```
python worker/truth_check.py          → PASS (12 vaka)
python worker/pi_brain.py enrollorder → PASS (6 vaka)
python worker/pi_brain.py policy      → PASS (8 vaka)   ← daha önce ÇÖKÜYORDU
python worker/pi_brain.py name/wake/waketimer/reset/soul/rotate → hepsi PASS
ruff check .                          → yeni bulgu YOK (3 eski bulgu duruyor)
```

Testler `tempfile.mkdtemp()` içinde geçici `speakers.db` + geçici `memory/`
kullanır; kullanıcının gerçek `worker/data/speakers.db`'sine YAZMAZ.

`_policy_test()` HEAD'de zaten çöküyordu (`FakeState.begin_expression_capture`
yok) → kimse (b) ve (c) vakalarının bayatladığını göremiyordu. Üçü de onarıldı:
stub eklendi, (b) artık policy'ye bakıyor (kayıt cümlesini ifade-corpus'u üretiyor),
(c) otomatik merge yerine "Sen X misin?" açık onayını bekliyor.

## Tek blok geri dönüş

```bash
# 1) Erteleme kolunu kapat → kayıt sırası ESKİ davranışa döner (anında ret).
#    Kod değişikliği GEREKMEZ; testte (e) vakası bunu kilitliyor.
echo 'SPEAKER_ENROLL_WAIT_S=0' >> worker/.env && systemctl restart candan-worker

# 2) Hepsini geri al (commit'i devir):
git revert <commit>
```

## Bu turda BİLEREK dokunulmadı

* Tanıma **eşiği** (`SPEAKER_ID_THRESHOLD`) — gölge ölçüm verisi birikince ayrı tur.
* Ayhan ↔ Havi ↔ profilsiz üçüncü kişi karışması (kapalı-küme hatası). Kayıt
  sırası düzeldi ama **tanıma** hâlâ karıştırıyor; asıl iş orada.
* `higgs-tts`, `pi-service`, `candan-brain`.
