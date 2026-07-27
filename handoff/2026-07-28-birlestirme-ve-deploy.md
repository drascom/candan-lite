# Birleştirme + tek seferde deploy — 27 Tem 23:08

İki paralel iş tek ağaçta birleştirildi, kullanıcının bir kararıyla birlikte
**tek** `candan-worker` restart'ında canlıya alındı.

| # | İş | Commit |
|---|----|--------|
| 1 | Gecikme/stall — prefill'e duyarlı watchdog | `195a782` (zaten main'de) |
| 2 | Kayıt sırası + truth_check daralması + prompt düzeltmesi | `da5d8b1` (worktree) → cherry-pick |
| 3 | **Kullanıcı kararı:** kesme sınıflandırıcısının LLM kademesi KAPALI | `.env` |

---

## 1. Birleştirme

`da5d8b1` **cherry-pick** ile alındı (merge değil: tek commit, main lineer kalsın).

⚠️ **Worktree 11 commit GERİDEN dallanmıştı** (`merge-base` = `82d6d19`, main ucu
`195a782`). Yani kayıt ajanı şunları HİÇ görmemişti: konuşma hızı kolu (`ede4ca0`),
`.env` kolları/`reload_settings` (`c9d0d27`), sözünü-kesme (`d395bb9`, `cb2408f`),
kimlik onay döngüsü (`48999ef`), duygu atlası. Çakışma haritası bu yüzden
beklenenden geniş çıktı.

### Çıkan çakışmalar (2 adet, ikisi de EKLEME çakışması)

| Dosya | Ne | Çözüm |
|-------|----|-------|
| `worker/truth_check.py` | `__all__` — HEAD `speed_claim`/`speed_line`, dal `soft_record_claim` | **İkisi de** tutuldu (alfabetik) |
| `worker/pi_brain.py` | Sabitler bloğu — HEAD `SPEAKER_CONFIRM_*` (onay döngüsü), dal `SPEAKER_ENROLL_WAIT_*` | **İkisi de** tutuldu, art arda |

### Semantik denetim (otomatik merge "temiz" dese de ELLE okundu)

- **`truth_check.decide()` — asıl risk buradaydı, İKİ MANTIK DA DURUYOR:**
  1. yazma hatası → 2. mod iddiası → **2b. hız iddiası (`speed_line`, gecikme
  turundan önceki hız işi)** → **3. kayıt iddiası + YUMUŞAK/GÜÇLÜ ayrımı (kayıt
  ajanı)** → 4. eylem iddiası → 5. yargıç. Adımlar birbirini ezmiyor; docstring
  de ikisini birden anlatacak şekilde birleşti.
- **Watchdog yolu bozulmadı:** `prefill_grace()`, `GET /slots` yoklaması,
  `PI_PREFILL_GRACE` ve "stall SAYILMADI" dalı yerinde.
- **Kayıt akışı ↔ onay döngüsü sırası doğru:** `PiStream` içinde
  `_enrollment_line` (ertelenmiş kararı çözer) `_confirm_ask_line`'dan ÖNCE
  çağrılıyor; `_confirm_ask_line` ayrıca `_enroll_active`/`_enroll_stage`
  guard'ıyla kayıt sırasında soru sormuyor. Yani "kayıt sürerken 'sen Ayhan
  mısın?' diye sorma" davranışı korunuyor.

### Birleştirmenin AÇTIĞI tek gerçek hata

`_enrollment_line` artık her turun başında `_resolve_deferred_enroll`'e uğruyor.
`tests/test_speaker_confirm.py::EnrollLatchTest` PiBrain'i `__new__` ile ELDE
kuruyor (kısmi nesne) ve yeni üç alanı bilmiyordu → `AttributeError`.
Üretim kodu doğru (`__init__` alanları kuruyor); **stub'a üç alan eklendi**
(`_enroll_wait_name`/`_enroll_wait_until`/`_enroll_wait_turns`).

---

## 2. Testler — HEPSİ GEÇTİ

| Takım | Sonuç |
|-------|-------|
| `python -m unittest discover -s tests` | **387/387 OK** |
| `python truth_check.py` | 12/12 PASS |
| `python pi_brain.py {name,soul,proactive,policy,enrollorder,waketimer,wake,reset,compaction,rotate}` | 10/10 PASS |
| `tests/test_go_readiness.sh` | 34/34 TEMİZ |

`./check.sh` — **YENİ BULGU YOK.** 4 bulgu (`B007` ab_bench, `RUF006` +
2× `PLW0603` pi_brain test yardımcıları) `195a782`'de de birebir vardı;
pristine dosyalarla yeniden ölçülerek doğrulandı. (`check.sh` çıktısında 8
görünüyor çünkü ruff `.claude/worktrees/` altındaki kopyayı da tarıyor.)

---

## 3. Deploy — YAPILDI (27 Tem 23:08)

**Sadece `candan-worker` restart edildi.** `candan-brain`, `higgs-tts`,
`pi-service` ELLENMEDİ; `--parallel` DEĞİŞTİRİLMEDİ.

Yedek: `/opt/candan-lite/.deploy-backup-20260727-2320/`
(`pi_brain.py`, `truth_check.py`, `.env`, `speakers.db` — `sqlite3 .backup` ile).

```bash
scp worker/pi_brain.py worker/truth_check.py root@192.168.0.25:/opt/candan-lite/worker/
ssh root@192.168.0.25 'sed -i "s/^BARGE_CHECK_ENABLED=.*/BARGE_CHECK_ENABLED=false/" /opt/candan-lite/worker/.env'
ssh root@192.168.0.25 'systemctl restart candan-worker'
```

⚠️ `.env`'de satır **zaten `BARGE_CHECK_ENABLED=true` olarak VARDI** (103. satır) →
sona ekleme YAPILMADI, satır yerinde değiştirildi (çift satır bırakmamak için).

Doğrulama:
- `md5sum` sunucu = yerel: `pi_brain.py` `fdecad53…`, `truth_check.py` `a978d105…`
- import: `truth_check` (`soft_record_claim` + `speed_line` ikisi de var),
  `pi_brain` (`prefill_grace`, `SPEAKER_ENROLL_WAIT_S=30.0`,
  `SPEAKER_CONFIRM_ASK_ENABLED=True`, `PiBrain._resolve_deferred_enroll` var)
- `registered worker {"agent_name": "candan"}` — açılış temiz
- `journalctl -u candan-worker` → **traceback/error YOK**
- dört servis de `active`

### `BARGE_CHECK_ENABLED` gerçekten etkin mi — ÖLÇÜLDÜ

Bugün ".env kollarının yarısı ölü" çıktığı için tahminle yetinilmedi, sunucuda
`agent.py`'nin YÜKLEME SIRASI birebir tekrarlandı:

```
1) systemd ortaminda env var mi : None      ← systemd .env YÜKLEMİYOR (beklenen)
2) import anindaki modul degeri : True      ← "ölü kol" tuzağı tam burada
3) .env okundu, environ         : false
4) reload_settings ONCESI       : True
5) reload_settings SONRASI      : False     ← KOL ÇALIŞIYOR
```

`agent.py` 41. satırda `load_dotenv`, 57. satırda `barge.reload_settings()`
çağırıyor (sunucudaki kopyada doğrulandı) → çalışan süreçte değer `False`.

**İşlevsel ölçüm** (`urllib.request.urlopen` sayaçla sarılarak):

```
KAPALI  -> sonuc: None | beyne giden istek: 0
ACIK    -> sonuc: new  | beyne giden istek: 1
```

Yani sınıflandırıcı artık `candan-brain`'e **hiç** istek atmıyor — 8 stall'ın
7'sinin kaynağı olan KV-önbellek tahliyesi kesildi.

---

## 4. TEK BLOK GERİ DÖNÜŞ (telefondan çalıştırılabilir)

```bash
ssh root@192.168.0.25 'set -e
D=/opt/candan-lite/.deploy-backup-20260727-2320
cp -a $D/pi_brain.py $D/truth_check.py /opt/candan-lite/worker/
cp -a $D/.env /opt/candan-lite/worker/.env
systemctl restart candan-worker
sleep 5; systemctl is-active candan-worker'
```

Daha dar geri dönüşler (kod yerinde kalır, yalnız davranış kapanır):

```bash
# yalnız kesme sınıflandırıcısını geri aç
ssh root@192.168.0.25 'sed -i "s/^BARGE_CHECK_ENABLED=.*/BARGE_CHECK_ENABLED=true/" /opt/candan-lite/worker/.env && systemctl restart candan-worker'
# yalnız prefill farkındalıklı watchdog'u kapat (sabit 12s eşiğe dön)
ssh root@192.168.0.25 'printf "\nPI_PREFILL_GRACE=0\n" >> /opt/candan-lite/worker/.env && systemctl restart candan-worker'
# yalnız kayıt ertelemesini kapat (havuz yetersizse anında ret — eski davranış)
ssh root@192.168.0.25 'printf "\nSPEAKER_ENROLL_WAIT_S=0\n" >> /opt/candan-lite/worker/.env && systemctl restart candan-worker'
```

---

## 5. DEPLOY EDİLMEYEN — bilerek

`da5d8b1`'in **C maddesi** (model elindeki aracı inkâr ediyordu) `pi/AGENTS.md`
ve `pi/extensions/speaker-enroll/index.ts` içindeki PROMPT değişiklikleridir.
Bunlar `pi-service` tarafına aittir ve bu turda **pi-service'e dokunulmaması**
söylendi → git'te duruyor, canlıya **gitmedi**.

**Sonuç:** "beni kaydet" dendiğinde modelin "ses tanımayı doğrudan
çalıştıramıyorum" deme ihtimali canlıda HÂLÂ VAR. Kayıt sırası düzeltmesi
(A maddesi) modelin tool'u çağırdığı durumu düzeltir, çağırmadığı durumu değil —
o durumda emniyet ağı (`SPEAKER_ENROLL_NET_TURNS`) devrede.

Gerekince tek adımda alınır:

```bash
scp pi/AGENTS.md root@192.168.0.25:/opt/candan-lite/pi/AGENTS.md
scp pi/extensions/speaker-enroll/index.ts root@192.168.0.25:/opt/candan-lite/pi/extensions/speaker-enroll/index.ts
ssh root@192.168.0.25 'systemctl restart pi-service'   # ⚠️ bu tur YASAKTI
```

---

## 6. Kulakla bakılacak (kullanıcı)

1. **Sözünü kesme** — sınıflandırıcı kapalı. Deterministik kapının kararsız
   kaldığı kesmelerde artık her zaman "yeni komut" sayılıyor. Sohbet
   geri-bildirimi ("hı hı", "peki") yanlışlıkla cevabı kesiyor mu?
2. **Gecikme** — stall yerine `pi tur: beyin prompt'u İŞLİYOR … stall SAYILMADI`
   satırı görülmeli: `journalctl -u candan-worker -f | grep -E "İŞLİYOR|tur stall"`.
3. **Kayıt** — "beni kaydet" dendiğinde artık anında ret YOK; nötr cümle
   talimatı söylenip karar sonraki turda veriliyor (üst sınır 30 sn / 4 tur).
