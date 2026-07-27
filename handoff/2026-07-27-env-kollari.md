# `.env` KOLLARI — sessiz arıza, denetim ve düzeltme (27 Temmuz)

**Bu bir belge hatası değildi.** Kullanıcıya "şu satırı `.env`'e ekle, kapanır" diye
verilen geri dönüş komutlarının bir kısmı **hiçbir şey yapmıyordu** — ve yapmadığı
ancak acil bir durumda, yani **en kötü anda** anlaşılırdı.

---

## 1. Kök sebep

`candan-worker.service` `.env`'i **bilerek** `EnvironmentFile=` ile yüklemiyor
(unit'te yazılı: `PI_COLD_NOTICE_TEXT="Bir saniye, ..."` gibi **boşluklu Türkçe
değerler** systemd ayrıştırıcısını kırıyor). Onun yerine `agent.py` kendi
`load_dotenv()`'ini çağırıyor — ama **import blokundan SONRA**:

```python
import barge, pi_brain, truth_check, reminders   # ← 24-38. satırlar
load_dotenv(...)                                  # ← 40. satır
```

Sonuç: bu modüller import edilirken `.env` **henüz okunmamış** oluyor. **Modül
seviyesinde** okunan her ayar `.env` değerini hiç görmez, kod içindeki varsayılanda
**kilitli** kalır. Fonksiyon/metot İÇİNDE okunanlar sorunsuz (çağrı anında `.env`
yüklüdür) — `SPEAKER_THRESHOLD` bu yüzden çalışıyordu ve arıza bugüne dek fark
edilmedi.

Ek tuzak: **varsayılan argümanlar tanım anında bağlanır**
(`def __init__(self, enabled=WAKE_ENABLED)`) → tazeleme yapılsa bile o değer donuk kalır.

---

## 2. Denetim tablosu

`worker/*.py` içindeki **modül seviyesi** env okumaları (AST ile tarandı; fonksiyon
gövdeleri hariç — onlar çağrı anında okunur, sorunsuz).

| modül | modül-seviyesi okuma | durum |
|---|---|---|
| `agent.py` | 24 | ✅ **sağlam** — okumaları 48+. satırda, yani `load_dotenv`'den SONRA |
| `pi_brain.py` | 57 | ⚠️ 33'ü kol → **düzeltildi**; 24'ü altyapı → §4 |
| `truth_check.py` | 5 | ❌ **kırıktı** → düzeltildi |
| `reminders.py` | 5 | ❌ **kırıktı** → düzeltildi |
| `barge.py` | 5 | ✅ 27 Tem'de düzeltilmişti (regresyon testi eklendi) |
| `cli_client.py` · `enroll.py` · `pi_broker.py` | 2 · 2 · 0 | ayrı süreç, kendi `load_dotenv`'i / kol değil |
| `speaker_id` · `speaker_tap` · `higgs_tts` · `omnivoice_tts` · `speech_speed` · `tempo` · `wake_stt` · `whisper_stt` · `tts_cache` · `log_utils` · `trnorm` · `name_parser` | **0** | ✅ hepsi fonksiyon içinde okuyor — hiç etkilenmedi |

### Kol kol doğrulama — SUNUCUDA, gerçek `.venv` ile ÖLÇÜLDÜ

Gerçek `.env`'e **yazılmadan** ölçüldü: `dotenv.load_dotenv` geçici olarak yamalandı,
`agent.py` aynen çalıştırıldı ama `/tmp/env-test.env` okundu (salt-okuma).

| kol (kullanıcıya verilen satır) | ÖNCE | SONRA | söz verilen davranış |
|---|---|---|---|
| `SPEECH_SPEED=0` | ✅ çalışıyordu | ✅ | tempo uygulanmaz |
| `HIGGS_STREAM=0` | ✅ çalışıyordu | ✅ | streaming kapanır |
| `TTS_ENGINE=omnivoice` | ✅ çalışıyordu | ✅ | (artık kullanılmıyor — §5) |
| `BARGE_RESUME_ENABLED=false` | ✅ (27 Tem'de düzeltildi) | ✅ | kesme devamı kapanır |
| `BARGE_RESUME_NEAR_END_RATIO` | ✅ | ✅ | tekrar eşiği |
| **`SPEAKER_CONFIRM_ASK_ENABLED=false`** | ❌ **ÖLÜ** | ✅ | "sen Ayhan mısın?" sorulmaz |
| **`SPEAKER_OFFER_ENROLL_ON_DENY=false`** | ❌ **ÖLÜ** | ✅ | tanışma teklifi kapanır |
| **`WAKE_ENABLED=false`** | ❌ **ÖLÜ** (3 ayrı yerde) | ✅ | wake gate kapanır |
| **`CLAIM_CHECK_ENABLED=false`** | ❌ **ÖLÜ** | ✅ | truth_check yargıcı kapanır |
| **`PROACTIVE_TICK_SECONDS`** | ❌ **ÖLÜ** | ✅ | hatırlatma kalp atışı |
| `SPEAKER_CONFIRM_MIN_RATIO` / `_COOLDOWN_S` / `_MIN_WINDOWS` | ❌ ÖLÜ | ✅ | onay sıklığı eşikleri |
| `SPEAKER_ENROLL_*` (6 ayar) | ❌ ÖLÜ | ✅ | kayıt sihirbazı eşikleri |
| `RESET_ENABLED` / `RESET_PHRASES` / `RESET_*` | ❌ ÖLÜ | ✅ | "yeni sohbet başlat" |
| `WAKE_WORD` / `WAKE_WINDOW_SECONDS` / `WAKE_VARIANTS` | ❌ ÖLÜ | ✅ | wake ayarları |
| `DEV_MODE_ENABLED` · `PI_ISOLATED` · `PI_NO_BUILTIN_TOOLS` · `WEB_SEARCH_LEGACY_QWANT` · `CANDAN_TZ` | ❌ ÖLÜ | ✅ | özellik bayrakları |
| `SPEAKER_ID_ENABLED` / eşikler | ✅ | ✅ | `build_speaker_id()` **fonksiyon içinde** okuyor — hiç etkilenmemişti |

**ÖLÇÜM ÇIKTISI (sunucu, deploy sonrası): 13/13 kol OK, kırık kol 0.**
(Deploy öncesi aynı ölçüm: 7/12 kırık.)

---

## 3. Düzeltme — `reload_settings()` deseni

`barge.reload_settings()` deseni üç modüle daha uygulandı. `load_dotenv`'i import'ların
üstüne **taşımadık** (bkz. §4 gerekçesi).

* `pi_brain.reload_settings()` — wake · speaker confirm/enroll · reset · özellik bayrakları
* `truth_check.reload_settings()` — `CLAIM_CHECK_*` (`barge.classify_llm` de bunu okur → tek yerden ikisi de düzelir)
* `reminders.reload_settings()` — `PROACTIVE_*`
* `pi_brain.WakeGate.__init__` — varsayılan argüman tuzağı: `enabled=WAKE_ENABLED` → `enabled=None` + gövdede çözüm
* `pi_brain._RESET_PHRASES_DEFAULT` — varsayılan ifade listesi tek yere alındı (iki kopya olsaydı biri güncellenip diğeri unutulurdu)

**Çağrı sırası tek yerde ve görünür**, `agent.py`'de `load_dotenv()`'in hemen altında:

```python
load_dotenv(Path(__file__).resolve().parent / ".env")
barge.reload_settings()          # BARGE_RESUME_* / BARGE_CHECK_*
pi_brain.reload_settings()       # WAKE_* / SPEAKER_CONFIRM_* / SPEAKER_ENROLL_* / RESET_*
truth_check.reload_settings()    # CLAIM_CHECK_*
reminders_mod.reload_settings()  # PROACTIVE_*

# `from X import NAME` DEĞERİ kopyalar → tazeleme kopyaya yansımaz; yeniden bağla:
WAKE_ENABLED = pi_brain.WAKE_ENABLED
HEARTBEAT_SECONDS = reminders_mod.HEARTBEAT_SECONDS
```

⚠️ İkinci tuzak buydu: `agent.py` `WAKE_ENABLED`'i `from pi_brain import WAKE_ENABLED`
ile **değer kopyalayarak** alıyordu → modülü tazelemek `agent.py`'nin kopyasını
düzeltmezdi. Artık modül olarak import ediliyor ve tazelemeden **sonra** yeniden bağlanıyor.

---

## 4. `.env`'DEN AYARLANAMAYANLAR (bilerek — dokunulmadı)

Bunlar **kol değil, süreç açılış yapılandırması**. `reload_settings()` kapsamına
BİLEREK alınmadılar: tazelemek canlı davranışı tek hamlede değiştirirdi (broker
yoluna geçmek, ortak-oda hafızasını açmak…). **Bugün `.env`'e yazmak bir şey yapmaz.**

| değişken | `.env`'de yazan | süreçte ETKİN olan | sonucu |
|---|---|---|---|
| `PI_BROKER_SOCKET` | `/run/candan/pi-broker.sock` | `''` (boş) | ⚠️ **worker broker'a HİÇ bağlanmıyor** — `pi-service` ayakta ama 6 saatte tek bağlantı yok; Pi süreçleri hâlâ job içinde doğuyor |
| `PI_SHARED_ROOM_MODE` | `true` | `False` | ⚠️ ortak-oda modu KAPALI (tur ömürlü kimlik / `MEM_TURN_FILE` yolu devrede değil) |
| `PI_MODEL` | `llama-cpp/gemma-4-12B-it-qat-q4_0` | `openai-codex/gpt-5.6-terra` | pratikte görünmüyor: web `{"brain":"local"}` gönderiyor → `BRAINS["local"]` aynı modeli veriyor. **Metadata gelmezse yanlış modele düşer.** |
| `PI_THINKING` | `default` | `minimal` | aynı sebeple maskeleniyor |
| `PI_SHARED_ROOM_SESSION_ID` · `PI_BIN` · `PI_*_DIR` · `PI_TOOLS_ALLOWLIST` · `PI_*_TIMEOUT` · `PI_COLD_NOTICE_*` · `MEM_*` · `DEV_*` | — | kod varsayılanı | `.env`'den ayarlanamaz |

**Bu ayrı bir iş.** Devreye alınacaksa ölçülerek ve tek tek yapılmalı: `PI_BROKER_SOCKET`
dolduğunda `_start_broker_client()` **sessiz yerel fallback YAPMAZ**, bağlanamazsa
oturum patlar.

---

## 5. TTS geri dönüşü — OmniVoice ARTIK YOL DEĞİL

Kullanıcının kararı (27 Tem): *"Eğer TTS çalışmazsa OmniVoice'a dönmeyeceğiz.
OmniVoice çok büyük ve ağır bir model, RAM'de o kadar yerimiz yok. Gerekirse Piper
kurarız, Piper'a döneriz."*

* `TTS_ENGINE=omnivoice` kolu **teknik olarak çalışıyor** (ölçüldü) — ama **kullanılmayacak**.
* Higgs kalıcı motor. Streaming sorun çıkarırsa **tek geri dönüş kolu `HIGGS_STREAM=0`**
  (ölçüldü, çalışıyor; sunucudaki tam-WAV ucu `POST /api/tts` duruyor).
* TTS'te kalıcı arıza olursa yol: **Piper kurmak** — henüz kurulu DEĞİL, ayrı iş.

`2026-07-27-DEVIR.md` §6'daki "Higgs → OmniVoice" bloğu bu yüzden **geçersiz** işaretlendi
(silinmedi ki ileride yanlışlıkla çalıştırılmasın).

---

## 6. Testler

`worker/tests/test_env_kollari.py` — **18 yeni test**. Toplam **357 test geçiyor** (339 → +18).

Her test **tahmin etmez, farkı ölçer**: anahtarsız taban değeri (kod varsayılanı beklenen
değerden FARKLI olmalı, yoksa test hiçbir şey kanıtlamaz) → anahtar konur →
`reload_settings()` → etkin değer gerçekten değişti mi.

Ayrıca kilitlenenler:
* `agent.py` **çağrı sırası** (kaynak okunarak): `load_dotenv` < her `reload_settings()` < yeniden bağlama.
* `from pi_brain import WAKE_ENABLED` biçiminde **değer kopyalama geri gelirse** test düşer.
* `WakeGate` varsayılan-argüman tuzağı (+ açıkça verilen argümanın env'i ezmesi).
* **Kalıcı muhafız** (`ModuleLevelEnvGuardTest`): `worker/` içinde modül seviyesinde env
  okuyan **her modülün** `reload_settings()`'i olmalı. Yeni modül eklenirse test düşer ve
  yazarı desene yönlendirir. Şu an izlediği modüller: `barge` · `pi_brain` · `reminders` · `truth_check`.

⚠️ `worker/tests/` **`.gitignore`'da** (satır 45) → test dosyası commit'e girmez, yalnız
lokalde durur. Sunucuda `worker/tests/` dizini zaten YOK (testler orada koşmuyor).

⚠️ `worker/.venv`'de `pytest` YOK; testler `unittest` ile koşar:
```bash
cd worker && ./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
```
`ruff` de kurulu değil (ne lokalde ne sunucuda) → `./check.sh` bu adımı ATLAR.

---

## 7. Deploy ve doğrulama (27 Tem 21:34)

Yedek → gönder → **ölç** → yalnız `candan-worker` restart → log → md5.
`pi-service` ve `higgs-tts`'e **dokunulmadı** (ikisi de restart öncesi/sonrası `active`).

* Yedekler: `/opt/candan-lite/worker/{agent,pi_brain,truth_check,reminders}.py.bak-envkol-20260727`
* Deploy öncesi sunucudaki dosyalar `HEAD` ile **md5 olarak birebir** aynıydı (kimsenin
  el değişikliği üzerine yazılmadı); sonrası lokal ile birebir aynı.
* Restart sonrası log temiz: `registered worker {"agent_name": "candan"}`, hata yok.
* Sunucudaki gerçek `.env`'e **hiç yazılmadı** (ölçüm yamalı loader ile yapıldı).

### ⚠️ Bu deploy'un TEK canlı davranış değişikliği: wake gate

`.env`'de **`WAKE_ENABLED=false`** yazıyor ama etkin değer bugüne kadar `True`'ydu —
yani **wake gate açıktı ve kullanıcı "Candan" diyerek uyandırıyordu.** Artık `.env`
gerçekten okunuyor → **gate KAPALI**: Candan uyumaz, her tur işlenir, "Candan" demeye
gerek kalmaz (ama odadaki her söz de işlenir).

Bugünkü davranışı geri istersen **tek satır**:
```bash
ssh root@192.168.0.25 'sed -i "s/^WAKE_ENABLED=false/WAKE_ENABLED=true/" \
  /opt/candan-lite/worker/.env && systemctl restart candan-worker'
```
Diğer tüm ayarlarda `.env` değeri ile eskiden etkin olan değer **zaten aynıydı** →
başka hiçbir davranış değişmedi.

---

## 8. Geri dönüş (tek blok)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && \
  for f in agent pi_brain truth_check reminders; do cp -a $f.py.bak-envkol-20260727 $f.py; done && \
  systemctl restart candan-worker'
```
