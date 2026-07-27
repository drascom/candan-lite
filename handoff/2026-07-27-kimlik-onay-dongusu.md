# Kimlik ONAY DÖNGÜSÜ — belirsizken sor, onaylanırsa öğren (27 Temmuz)

271 test geçiyor. **CANLIYA ALINDI 17:53** (`SPEAKER_CONFIRM_ASK_ENABLED=true`) — §7.
Geri dönüş: §6. `pi-service` ve `higgs-tts`'e dokunulmadı, üçü de `active`.

---

## 1. TEŞHİS

### 1a. Tanıma (görev dosyasındaki ölçüm)

Son 2 saatte **85 konuşma turu**: **43 Bilinmeyen (%51)**, 37 Ayhan, 5 Havi.
Sebep dağılımı: güvenli pencere yok 16 · yetersiz ardışık onay 17 · çelişen pencere 10.

O 5 "Havi" turu aslında **Çiğdem'e** ait — evdeki üçüncü kişi, `speakers.db`'de profili
YOK. Sistem "tanımadığım biri" diyemiyor, hep en yakın bilinen profili seçiyor
(**kapalı küme davranışı**). asnorm eşiği -1.00, Havi -0.748 ile kabul edilmiş.

### 1b. KÖK SEBEP — kayıt akışı hiç çalışmıyordu (yeni bulgu)

Canlı log (13:56:29-30, `AJ_n75NsPdu8JjJ`): `enroll_speaker` çağrıldı →
`kayıt havuzu BOŞ (gördü=0)` → `çekirdek < 3 → KAYIT YAPILMIYOR` → `enroll REDDEDİLDİ`.
`speakers.db` 22 Temmuz'dan beri değişmemiş; sebebi bu.

Kod okumasıyla bulunan sebep (`pi_brain._enrollment_line`):

```python
if current is None and (self._enroll_active or _wants_enroll(text)):
    self._enroll_active = True     # ← toplayıcı YALNIZ burada başlıyordu
```

Toplama latch'i **`current is None` şartına bağlıydı**. Kapalı-küme hatası tam burada
ısırıyor: profili olmayan Çiğdem, Havi diye eşleşince `current` DOLU oluyor → latch hiç
açılmıyor → `_start_collect` çağrılmıyor → `gördü=0` → havuz boş → enroll reddediliyor.
**Yani kaydolmak isteyen kişi, yanlış tanındığı için kaydolamıyordu.** İki hata
birbirini besliyor.

Aynı latch `_enroll_hint`'i ve `enroll_turn` tamponunu da besliyor → **1c** de buradan.

### 1c. truth_check enroll'ü kapsamıyordu

Aynı turda model *"Harika Çiğdem, ses kaydını aldım... hafızamda üç kişi var"* dedi;
enroll REDDEDİLMİŞTİ. Sebep: kayıt anlatısının bastırılması `enroll_turn`e, o da
**tur başındaki `_enroll_active`e** bakıyordu — latch hiç açılmadığı için bayrak False
kaldı ve modelin yalan cümlesi canlıya çıktı. Ayrıca `truth_check` kelime listesinde
"ses kaydını aldım" kalıbı YOKTU.

---

## 2. YAPILAN — tasarım kararları

### Aday (karar Bilinmeyen olsa bile)

`SpeakerState.resolve_turn()` artık kabul edilmiş pencerelerden bir **aday** çıkarır:
`candidate`, `candidate_ratio`, `candidate_score`, `candidate_windows`
(`TurnSpeakerDecision`de yeni alanlar, hepsi varsayılanlı).

- **Seçim skor ağırlıklı**: skorlar AS-norm ölçeğinde negatif olabildiği için ham skor
  ağırlık olamaz (negatif ağırlık çoğunluğu ters çevirir). Turun en düşüğüne göre
  kaydırılmış pozitif ağırlık `w = s - s_min + 1` kullanılır; tüm skorlar eşitse saf
  pencere sayımına indirgenir.
- **Oran bilerek sayım tabanlı**: eşik ("kabul edilenlerin ≥ %60'ı") log'dan
  doğrulanabilir kalsın.
- **`candidate_windows` yalnız GÜNCEL pencereleri sayar** (transkript anına
  `turn_max_seconds` içinde) — bayat kanıtla soru sorulmaz.

⚠️ **Aday KİMLİK DEĞİLDİR.** `_identity_note()`, `_target()`, persona swap'i, hafıza
kimliği ve `_personal_memory_note()` hâlâ **yalnız `speaker_state.current`e** bakar.
Belirsizlikte kesin isim enjeksiyonu ve persona swap'i YOK — 18 Temmuz güvenlik
davranışı bire bir korundu. Aday tek bir yerde kullanılır: "sormaya değer mi".

### Soru — NEDEN SOĞUMA

**Sıklık bu özelliğin en kritik tasarım kısıtı.** Belirsizlik seri hâlde gelir: 43
belirsiz turun her birinde "sen Ayhan mısın?" diye soran bir ev asistanı çekilmez,
kullanıcı sistemi kapatır. Bu yüzden hepsi birden sağlanmadan sorulmaz:

- tur kararı **Bilinmeyen** (`decision.name is None` ve `state.current is None`), ve
- aday var ve daha önce "hayır" denmemiş (`_confirm_denied`), ve
- `candidate_ratio >= SPEAKER_CONFIRM_MIN_RATIO` (0.6), ve
- `candidate_windows >= SPEAKER_CONFIRM_MIN_WINDOWS` (2) ve öğrenilecek embedding var, ve
- **soğuma doldu**: son sorudan bu yana ≥ `SPEAKER_CONFIRM_COOLDOWN_S` (600 sn), ve
- kullanıcı zaten bir soruya cevap vermiyor (kayıt sihirbazı / sıfırlama onayı /
  bekleyen kimlik onayı / tanışma teklifi açıkken sorulmaz).

Şüphede kalırsak **SORMAYIZ**. Soğuma `time.monotonic()` ile ölçülür ve bağlantı
ömürlüdür (LiveKit her oda oturumu için yeni süreç açar → yeni bağlantıda sıfırlanır).

Soru **deterministik harness cümlesi**dir, modele bırakılmaz (`truth_check` ilkesi):
`"Pardon, sesinden emin olamadım — sen Ayhan mısın?"` — soru eki ünlü uyumuna göre
seçilir (`_misin`: mısın/misin/musun/müsün); "Ayhan misin" kulağı tırmalıyordu.

### Cevap

| Cevap | Ne olur |
|---|---|
| **Evet** | O turun en iyi skorlu en çok `SPEAKER_LEARN_MAX_PER_TURN` (3) penceresi profile yazılır, `source='confirmed-learn'`. `sample_count`/`updated_at` tazelenir, centroid yeniden kurulur. Candan: *"Tamam, artık sesini daha iyi tanıyacağım."* Yazma başarısızsa bu cümle SÖYLENMEZ (yalan olurdu). |
| **Hayır** | Hiçbir şey yazılmaz. Aday düşer, tur Bilinmeyen kalır, o oturumda aynı aday için bir daha sorulmaz. `SPEAKER_OFFER_ENROLL_ON_DENY` ile **bir kez** tanışma teklifi: *"Seni tanımıyorum galiba. İstersen sesini kaydedip tanıyabilirim."* Kabul → MEVCUT açık kayıt akışı (yeni profil). Ret → konu kapanır, ikinci kez açılmaz. |
| **Farklı isim** ("hayır, ben Havi'yim") | **Otomatik merge YOK** — mevcut kural aynen. Açık kayıt akışına yönlendirilir. |
| **Belirsiz/cevapsız** | Hiçbir şey yazılmaz, sessizce devam (üsteleme yok). |

Cevap işleme **kimlik guard'ından ÖNCE** çağrılır (`_confirm_answer_line`): "hayır, ben
Havi'yim" guard'a kimlik İDDİASI gibi görünüp orada yutuluyordu. Soru sorma
(`_confirm_ask_line`) ise **en sonda** — komutlar ve kayıt akışı önceliklidir.

### Kök sebep düzeltmesi (1b)

Latch artık `current`ten bağımsız: **açık kayıt isteği** (`_wants_enroll`) her hâlükârda
toplamayı başlatır. Yazma kapısı DEĞİŞMEDİ (çekirdek tutarlılığı + `best_match` belirsiz
bandı → "Sen X misin?"). Bu durumda ayrı bir log satırı düşer:
`enrollment: açık kayıt isteği — ses %r olarak eşleşmiş olsa da toplama BAŞLIYOR`.

⚠️ **Kalan risk (ölç, sonra karar ver):** iki kişi odadayken A konuşurken B'yi kaydetme
girişiminde havuz A'nın pencerelerini toplayabilir. Bugüne kadar bu senaryo **kesin
başarısızlıkla** (havuz boş) bitiyordu; artık yazma mümkün. Üç kapı hâlâ devrede:
çekirdek-içi tutarlılık (`SPEAKER_ENROLL_CORE_MIN=0.60`, en az 3 pencere), `best_match`
belirsiz bandı ("Sen Ayhan mısın?") ve isim doğrulaması. Yine de **kaydolacak kişinin
kendisi konuşmalı**. Enroll'ü "konuşan kişi ≠ kaydedilen kişi" durumunda tamamen
reddetmek ayrı bir karar — bu turda YAPILMADI (yeni eşik/ret yolu = yeni hata riski).

### truth_check enroll kapsaması (1c) — mevcut katman-2 deseni

1. **Dinamik kayıt kapısı**: anlatı tamponlaması artık tur başındaki bayrağa değil,
   `enroll_turn or self._brain._pending_enroll is not None`e bakar. Kayıt tool'u sinyali
   görüldüğü ANDAN itibaren modelin metni canlıya çıkmaz; sonucu yalnız kod söyler.
2. **Katman 2b**: tool sinyalinden ÖNCE çıkmış metin geri alınamaz. Kayıt YAPILMADIYSA
   (`_enroll_last_ok` — gerçek yazma bayrağı, `_store_samples`ta set edilir) ve o metin
   bir "kaydettim" iddiası taşıyorsa harness `truth_check.UNBACKED_LINE` ekler.
3. **Kelime listesi**: `kaydını aldım` / `kayıt aldım` kalıpları eklendi ("not aldım"
   deseni "ses kaydını aldım"ı kaçırıyordu).

Not: latch düzeltmesi sayesinde tipik akışta `enroll_turn` zaten True olur ve anlatı
BAŞTAN bastırılır; (1) ve (2) latch açılmayan hâller için emniyet.

### Gölge ölçüm — eşiğe DOKUNULMADI

`SpeakerID._log_shadow`: her KABUL kararında skor, ikinci skor, marj ve
`SPEAKER_SHADOW_THRESHOLDS` (-0.5 / 0.0 / +0.5) barajlarının verdiği KABUL/RED yazılır.
Karar DEĞİŞMEZ, hiçbir alan güncellenmez. Ayrıca her onay sorusu/cevabı
`SPEAKER_CONFIRM_LOG` JSONL'ine yazılır (aday, oran, ort. skor, tüm pencere skorları,
cevap, eklenen örnek). **Kullanıcının "hayır"ı etiketli veridir.** Birkaç gün sonra
asnorm eşiği bu iki kaynakla TAHMİNLE değil VERİYLE seçilecek.

Pasif öğrenme AÇILMADI: `SPEAKER_LEARN_ENABLED` false kalır, öğrenme YALNIZ açık onaydan
sonra olur.

---

## 3. DEĞİŞEN DOSYALAR

| Dosya | Ne |
|---|---|
| `worker/speaker_tap.py` | `TurnSpeakerDecision`e aday alanları, `_candidate_of`, `observe(embedding=...)`, `last_turn_candidate_windows` |
| `worker/speaker_id.py` | `shadow_thresholds` + `_log_shadow` + `_shadow_thresholds()` env ayrıştırıcı |
| `worker/agent.py` | "speaker turn kararı" log'una `aday/oran/ort_skor/pencere` |
| `worker/pi_brain.py` | onay döngüsü (env + durum + `_confirm_answer_line` / `_confirm_ask_line` / `_confirm_answer` / `_confirm_learn` / `_confirm_log` / `_misin`), latch düzeltmesi, dinamik kayıt kapısı, `_enroll_last_ok` |
| `worker/truth_check.py` | "kaydını aldım" / "kayıt aldım" iddia kalıpları |
| `worker/.env.example` | yeni bayraklar + gerekçeleri |
| `worker/tests/test_speaker_confirm.py` | **yeni**, 23 test |
| `worker/tests/test_truth_check.py` | `EnrollTruthTest` (3 test) + `_say(prepare=...)` |

**Testler: 271 geçiyor** (öncesi 243, +28). `./check.sh` yeni bulgu üretmiyor
(4 ruff bulgusu değişiklikten ÖNCE de vardı: `bench/ab_bench.py`, `pi_brain.py` self-test).

Testler kullanıcının gerçek `speakers.db`'sine YAZMAZ — her test geçici dizinde taze
`SpeakerStore` kurar, onay log'u da `tempfile` altına yönlendirilir.

---

## 4. ENV DEĞİŞKENLERİ

```
SPEAKER_CONFIRM_ASK_ENABLED=true      # false => BUGÜNKÜ davranış, bire bir
SPEAKER_CONFIRM_MIN_RATIO=0.6
SPEAKER_CONFIRM_COOLDOWN_S=600        # 10 dk — sıklık kısıtı
SPEAKER_CONFIRM_MIN_WINDOWS=2
SPEAKER_LEARN_MAX_PER_TURN=3
SPEAKER_OFFER_ENROLL_ON_DENY=true
SPEAKER_CONFIRM_LOG=data/speaker_confirm_log.jsonl
SPEAKER_SHADOW_THRESHOLDS=-0.5,0.0,0.5   # boş = gölge ölçüm kapalı
```

---

## 5. CANLIDA NE YAPILIR / NE DUYULUR

1. **Tetikleme:** kısa/kararsız cümlelerle konuş (sistem seni tanıyamasın ama aday
   üretsin). Log'da `speaker turn kararı: Bilinmeyen ... | aday=Ayhan oran=1.00 pencere=2`
   satırını gör. Koşullar tutarsa Candan **bir kez** sorar:
   *"Pardon, sesinden emin olamadım — sen Ayhan mısın?"*
2. **"Evet"** de → *"Tamam, artık sesini daha iyi tanıyacağım."* Log: `kimlik onayı
   ÖĞRENME: 'Ayhan' için 3/3 örnek yazıldı (kaynak=confirmed-learn, skorlar=...)`.
3. **"Hayır"** de → hiçbir şey yazılmaz, *"Seni tanımıyorum galiba. İstersen sesini
   kaydedip tanıyabilirim."* "Olur" dersen normal kayıt sihirbazı başlar.
4. **Aynı oturumda ikinci soru 10 dk dolmadan GELMEZ** — gelirse soğuma bozulmuş demektir.
5. Çiğdem'i kaydetmeyi tekrar dene: **Çiğdem'in kendisi** "beni kaydet" desin ve
   sihirbaz boyunca **o konuşsun**. Log'da `gördü=` sayısının 0'dan büyük olması
   kök sebep düzeltmesinin çalıştığının kanıtıdır.

İzleme:
```bash
journalctl -u candan-worker -f | grep -E "kimlik onayı|gölge ölçüm|aday=|gördü="
```

---

## 6. GERİ DÖNÜŞ — TEK BLOK

```bash
# (a) Davranışı anında kapat (kod kalır, restart yeter)
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && \
  sed -i "s/^SPEAKER_CONFIRM_ASK_ENABLED=.*/SPEAKER_CONFIRM_ASK_ENABLED=false/" .env && \
  systemctl restart candan-worker'

# (b) Öğrenilen örnekleri sil — ilk kayıt (voice-enroll) örneklerine DOKUNMAZ
ssh root@192.168.0.25 'sqlite3 /opt/candan-lite/worker/data/speakers.db \
  "DELETE FROM speaker_samples WHERE source='"'"'confirmed-learn'"'"'; \
   UPDATE speakers SET sample_count = (SELECT COUNT(*) FROM speaker_samples s WHERE s.speaker_id = speakers.id);" && \
  systemctl restart candan-worker'

# (c) Kod + .env'i tamamen geri al
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/pi_brain.py.bak-onay-20260727 worker/pi_brain.py && \
  cp worker/speaker_tap.py.bak-onay-20260727 worker/speaker_tap.py && \
  cp worker/speaker_id.py.bak-onay-20260727 worker/speaker_id.py && \
  cp worker/agent.py.bak-onay-20260727 worker/agent.py && \
  cp worker/truth_check.py.bak-onay-20260727 worker/truth_check.py && \
  cp worker/.env.bak-onay-20260727 worker/.env && \
  systemctl restart candan-worker'

# (d) DB'yi tamamen geri al (deploy öncesi yedek)
ssh root@192.168.0.25 'systemctl stop candan-worker && \
  cp /opt/candan-lite/worker/data/speakers.db.bak-onay-20260727 /opt/candan-lite/worker/data/speakers.db && \
  systemctl start candan-worker'
```

---

## 7. DEPLOY — YAPILDI (27 Tem 17:53)

| Adım | Sonuç |
|---|---|
| Yedek | `speakers.db.bak-onay-20260727`, `.env.bak-onay-20260727`, 5 × `*.py.bak-onay-20260727` |
| Ön kontrol | Sunucudaki 5 dosyanın md5'i değişiklik ÖNCESİ halimle birebir aynıydı → başkasının değişikliği ezilmedi |
| Gönderim | `pi_brain.py`, `speaker_tap.py`, `speaker_id.py`, `agent.py`, `truth_check.py` |
| `.env` | 8 yeni değişken eklendi (`SPEAKER_CONFIRM_ASK_ENABLED=true`) |
| Import doğrulama | `import OK` · bayrak=True, oran=0.6, soğuma=600.0, gölge eşikler=(-0.5, 0.0, 0.5), `TurnSpeakerDecision` aday alanları geldi |
| Restart | **yalnız `candan-worker`**. `candan-worker`/`pi-service`/`higgs-tts` üçü de `active` |
| Log | traceback/ImportError/SyntaxError **0**; `registered worker {"agent_name": "candan", "id": "AW_UEVitjskdgBN"}` |
| md5 | yerel = sunucu, 5/5 |
| DB başlangıç durumu | `Ayhan 6 voice-enroll` · `Havi 6 imported-expression-enroll` · `confirmed-learn 0` |

### Kullanılan komutlar

⚠️ `pi-service`'e DOKUNMA (`candan-worker` ona `Requires=` ile bağlı).
⚠️ `higgs-tts`'e DOKUNMA (duygu atlası sesleri üretiliyor). Sadece `candan-worker` restart.

```bash
# 1) YEDEK (DB + kod + .env)
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && \
  cp data/speakers.db data/speakers.db.bak-onay-20260727 && \
  cp .env .env.bak-onay-20260727 && \
  for f in pi_brain.py speaker_tap.py speaker_id.py agent.py truth_check.py; do \
    cp $f $f.bak-onay-20260727; done && ls -la *.bak-onay-20260727 data/*.bak-onay-20260727'

# 2) GÖNDER
cd /Users/drascom/Documents/work/candan-lite
scp worker/pi_brain.py worker/speaker_tap.py worker/speaker_id.py \
    worker/agent.py worker/truth_check.py root@192.168.0.25:/opt/candan-lite/worker/

# 3) .env'e yeni bayraklar
ssh root@192.168.0.25 'cat >> /opt/candan-lite/worker/.env <<"EOF"

# Kimlik onay dongusu (27 Tem) — bkz. handoff/2026-07-27-kimlik-onay-dongusu.md
SPEAKER_CONFIRM_ASK_ENABLED=true
SPEAKER_CONFIRM_MIN_RATIO=0.6
SPEAKER_CONFIRM_COOLDOWN_S=600
SPEAKER_CONFIRM_MIN_WINDOWS=2
SPEAKER_LEARN_MAX_PER_TURN=3
SPEAKER_OFFER_ENROLL_ON_DENY=true
SPEAKER_CONFIRM_LOG=data/speaker_confirm_log.jsonl
SPEAKER_SHADOW_THRESHOLDS=-0.5,0.0,0.5
EOF'

# 4) IMPORT DOĞRULA (restart'tan ÖNCE)
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && \
  .venv/bin/python -c "import pi_brain, speaker_tap, speaker_id, truth_check; print(\"import OK\")"'

# 5) RESTART (yalnız worker)
ssh root@192.168.0.25 'systemctl restart candan-worker && sleep 5 && \
  systemctl is-active candan-worker && \
  journalctl -u candan-worker --since "-2 min" | grep -iE "traceback|error" | head'

# 6) MD5
md5 worker/pi_brain.py worker/speaker_tap.py worker/speaker_id.py worker/agent.py worker/truth_check.py
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && md5sum pi_brain.py speaker_tap.py speaker_id.py agent.py truth_check.py'
```

---

## 8. SONRAKİ TUR

1. **Eşiği VERİYLE seç.** `SPEAKER_CONFIRM_LOG` + `gölge ölçüm` satırları birikince
   yabancı (Çiğdem) ile ev halkının skor dağılımına bak; asnorm eşiği o zaman değişsin.
2. **Kalan risk (§2 sonu):** "konuşan ≠ kaydedilen" durumunda enroll ne yapmalı?
3. Onay sorusunun gerçek sıklığını ölç: `grep -c "kimlik onayı SORULUYOR"`. Günde 1-2'den
   fazlaysa oran/soğuma sıkılaştırılmalı.
