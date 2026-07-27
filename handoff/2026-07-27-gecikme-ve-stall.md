# GECİKME ve TUR DÜŞMESİ — ölçüm, teşhis, düzeltme (27 Temmuz, 23:xx)

Görev: `handoff/task-2026-07-27-gecikme-ve-stall.md`.
Kaynak veri: `journalctl -u candan-brain` + `journalctl -u candan-worker`,
27 Tem 21:55–22:32 (kullanıcının 46 turluk uzun testi).

**Tek cümlelik sonuç:** Tur düşmelerinin sebebi bağlamın büyümesi DEĞİL,
**tek slotlu `candan-brain`'e giden YABANCI bir istek** — sözünü-kesme
sınıflandırıcısı. 85–142 token'lık o istek 15–29 bin token'lık konuşma
önbelleğini slottan atıyor, sıradaki kullanıcı turu bağlamı BAŞTAN işlemek
zorunda kalıyor (13–15 s) ve 12 s'lik watchdog o **sağlıklı** turu öldürüyor.

---

## 0. Neyin ne olduğu — ölçülen zincir

Kullanıcının duyduğu tek örnek (canlı, 22:24, job `AJ_mm3KUcgUFjTH`):

| saat | nerede | ne oldu |
|---|---|---|
| 22:24:23 | brain | `task 28547` — **83 token**, slot LRU ile seçildi |
| 22:24:23 | worker | (kesme sınıflandırıcısı → `barge.classify_llm`) |
| 22:24:23 | brain | `task 28552` — slot yine **LRU** (ortak ön ek YOK) → 15 251 token BAŞTAN |
| 22:24:36 | brain | `cancel task, id_task = 28552` ← bizim abort'umuz |
| 22:24:35 | worker | `pi tur stall: 12s ilerleme yok → tur kapatılıyor (got_delta=False)` |
| 22:24:37 | kullanıcı | *"Kusura bakma, bir an aklım dağıldı — tekrar sorar mısın?"* |

Beyin çalışıyordu. Turu düşüren **bizim sabrımızdı**.

Prefill hızı ölçüldü: **~1120 token/s** (`13587 ms / 15245 token`,
`14745 ms / 17000 token`, `8408 ms / 8520 token`). Yani 15 bin token'lık bir
bağlam TEK BAŞINA 13–15 s prefill demek — 12 s'lik eşiğin üstünde.

---

## 1. Stall watchdog — DÜZELTİLDİ

### Kaç stall, hangisi neden (8 stall, hepsi eşleşti)

| stall (worker) | hemen öncesinde brain'de | sebep |
|---|---|---|
| 22:10:30 | 22:09:56 compaction, LRU, 15 245 token | **compaction** |
| 22:11:22 | 22:11:10 `task 27443` — 85 token, LRU | sınıflandırıcı |
| 22:14:41 | 22:14:15 `task 27694` — 85 token, LRU | sınıflandırıcı |
| 22:17:46 | 22:17:34 `task 28020` — 139 token, LRU | sınıflandırıcı |
| 22:19:35 | 22:19:23 `task 28256` — 101 token, LRU | sınıflandırıcı |
| 22:19:50 | 22:19:38 `sim_best = 0.529` | bir önceki stall'ın artığı (zincir) |
| 22:21:00 | 22:20:15 `task 28281` — 142 token, LRU; 22:20:48 `sim_best = 0.382` | sınıflandırıcı |
| 22:24:35 | 22:24:23 `task 28547` — 83 token, LRU | sınıflandırıcı |

**8 stall'ın 6'sı doğrudan kesme sınıflandırıcısının, 1'i onun zincirinin,
1'i compaction'ın eseri.** Yani "compaction penceresine denk gelen" stall
sayısı: **1/8** (görev dosyasının tahmini "7 stall compaction'a denk mi"
sorusunun cevabı: hayır, sadece biri).

### llama-server prefill bilgisini VERİYOR — bakıldı, kullanıldı

`GET http://192.168.0.25:8082/slots` (canlıda doğrulandı, salt-okunur):

```json
[{"id":0,"is_processing":false,"id_task":28729,
  "n_prompt_tokens":17424,"n_prompt_tokens_processed":101,
  "next_token":[{"n_decoded":60}]}]
```

`is_processing` + `n_prompt_tokens_processed` + `n_decoded` üçlüsü, prefill
sürerken batch batch İLERLER (brain log'undaki `prompt processing, n_tokens =
4096 … 6144 … 8254` satırları aynı sayacın çıktısı). Bu üçlü **ilerleme
imzası** olarak kullanıldı.

### Yapılan (worker/pi_brain.py)

`BrainPrefillProbe` — watchdog turu öldürmeden ÖNCE beyne sorar:

* imza **iki yoklama arasında değiştiyse** → beyin prompt'u okuyor, bu sessizlik
  sağlıklı → tur ÖLDÜRÜLMEZ, sayaç sıfırlanır, beklenmeye devam edilir;
* `is_processing = false` (beyin boşta) → **gerçek takılma** → tur bugünkü gibi
  eşikte kapanır;
* uç erişilemez / cevap bozuk → **bugünkü davranışın birebir aynısı**
  (best-effort; yoklama turu ASLA bloklamaz veya düşürmez).

**Eşik KALDIRILMADI, SONSUZ YAPILMADI.** Meşru prefill'e verilen ek süre tur
başına birikimli ve tavanlı: `PI_PREFILL_GRACE` (varsayılan **60 s**; ölçülen
en ağır prefill 15 s idi, kat kat pay bırakıldı). Tavan dolunca uca hiç
gidilmez, tur kapanır. Gerçek takılmada (WebSocket 1000) hız kaybı **sıfır**:
beyin boşta olduğu için ilk yoklama zaten `False` döner.

### Stall'da kullanıcı sessiz mi kalıyor? — HAYIR (kod yolu doğrulandı)

Koordinatörün notu üzerine tüm çıkış yolları okundu; **yeni cümle/mekanizma
EKLENMEDİ**, sadece doğrulandı:

| durum | ne duyulur |
|---|---|
| stall + hiç metin yok | `PI_EMPTY_TURN_TEXT` ("Kusura bakma, bir an aklım dağıldı…") |
| stall + tam-content var | tam metin akıtılır |
| stall + yarım cevap akmıştı | yarım cevap kalır (sessizlik DEĞİL — bilerek ek cümle yok) |
| kayıt (enroll) turunda stall | yine `PI_EMPTY_TURN_TEXT` (o dal enroll guard'ın DIŞINDA) |
| tur tamamen metinsiz bitti | son emniyet ağı `PI_EMPTY_TURN_TEXT` |

Sessiz kalan bir stall yolu **yok**. Kapatılacak delik bulunamadı.

### Test

`worker/tests/test_prefill_watchdog.py` — 20 test. İçlerinde canlı hatanın
birebir tekrarı: cevap eşikten sonra geliyor + beyin o sırada işliyor →
**kol kapalıyken test DÜŞÜYOR** (`'Kusura bakma…' != 'Tabii, anlatayım.'`),
kol açıkken geçiyor. Gerçek takılma ve erişilemez uç testleri bugünkü
davranışın korunduğunu gösteriyor.

---

## 2. Önbellek ön eki — ÖLÇÜLDÜ, TAŞINACAK BİR ŞEY YOK

Görev dosyası "art arda iki turun prompt'unu karşılaştır, ilk fark nerede
başlıyor" dedi. `sim_best` zaten tam olarak bu sayı: llama-server'ın ortak
ön ek uzunluğu / prompt uzunluğu oranı.

### Rahatsız edilmemiş art arda turlar (aynı oturum, 22:12–22:27)

| ölçüm | değer |
|---|---|
| `sim_best` (29 tur) | **0.953 – 0.997**, medyan **≈ 0.98** |
| yeniden işlenen prompt token'ı | **45 – 879** (bağlam 15 000 – 29 646 iken) |
| prompt eval süresi | **0.36 – 1.19 s** |

Yani art arda iki turun **ilk farkı prompt'un ~%98'inde**, yaklaşık son
300–800 token içinde başlıyor. Şüpheli listesinin hepsi — `_now_note()`,
`_identity_note()`, `_maybe_greet()`, `_enroll_hint()`, `_barge_note()`,
`_personal_memory_note()`, konuşmacı bağlamı — pi'ya giden **kullanıcı
mesajının** başına ekleniyor, kullanıcı mesajı ise konuşmanın **EN SONUNDA**.
Ön ekte değillerdi. **Taşınacak bir şey yok; sayı bunu söylüyor.**

### Ön eki gerçekten bozan şey

`candan-brain` `--parallel 1` ile çalışıyor → **TEK slot**. O slota giren her
yabancı prompt, konuşmanın KV önbelleğini siler:

| yabancı istek | boyut | sonraki gerçek turda |
|---|---|---|
| kesme sınıflandırıcısı (`barge.classify_llm`) | 83–142 token | slot **LRU** ile seçilir (`sim_best` eşiğin altında) → 15 000+ token baştan, 13–15 s |
| compaction (pi'nın özetleme çağrısı) | 15 245 / 17 000 token | aynı şekilde slot sıfırlanır |

Zincirin artığı da ölçüldü: bir stall'dan sonra slotta yarım kalmış prompt
kalıyor → sonraki tur `sim_best = 0.529`, sonraki `0.382`, `0.487`, `0.574`,
`0.665`, `0.792`, `0.861`, `0.866` → 4 479 / 4 721 / 6 397 / 8 407 /
14 745 ms'lik prefill'ler. Kullanıcının "model cevap süreleri çok uzadı"
gözlemi bu.

### Ne YAPILMADI ve neden

Ön eki gerçekten onaracak değişiklik **llama-server tarafında**:
`--parallel 2` (sınıflandırıcı kendi slotunu alır, konuşma slotu bozulmaz).

⛔ **YAPILMADI — kullanıcıya sorulmadan sunucu ayarı değiştirilmiyor**
(görev sınırı) ve `candan-brain` yeniden başlatmak canlı oturumu kesiyordu.
Kararı kullanıcı verir. Riski de ölçüldü: `--parallel 2` ile slot başına
bağlam 65 536 → **32 768**'e iner, bu oturumda ölçülen tepe bağlam **29 646**
token — yalnız ~3 bin token pay kalır. Yani `--parallel 2` tek başına yeterli
değil, compaction eşiğiyle birlikte düşünülmeli.

Ara çözüm gerekmiyor: **1. maddedeki watchdog düzeltmesi, ön ek bozulsa bile
turun ÖLMESİNİ engelliyor.** Kullanıcı özür + soruyu tekrarlama yerine
cevabını alıyor — sadece o tek turda 13–15 s bekleyerek.

Sıfır stall isteniyorsa (sunucuya dokunmadan) tek satırlık kol zaten var:
`BARGE_CHECK_ENABLED=false` — sözünü-kesme sınıflandırıcısı susar, kesme
kararı deterministik kapıya düşer (şüphede "yeni istek"). Bu bir **tercih**,
düzeltme değil; kullanıcının kararı.

---

## 3. Compaction — bağlam GERÇEKTEN küçülüyor, eşiğe dokunulmadı

| compaction | öncesi bağlam | sıkıştırma isteği | sonraki gerçek tur bağlamı | küçülme |
|---|---|---|---|---|
| 22:09:56 (`reason=threshold`) | 28 787 token | 15 245 token / 13 587 ms | 15 305 token | **−47 %** |
| 22:21:15 (`reason=threshold`) | 29 646 token | 17 000 token / 14 745 ms | 15 043 token | **−49 %** |

* **Küçülüyor mu?** Evet, yaklaşık yarıya. Cevap net.
* **Eşik doğru yerde mi?** ~28,8–29,6 bin token'da tetikleniyor (ctx 65 536'nın
  %45'i). Eşiği **DEĞİŞTİRMEDİM**: her compaction'ın kendisi bir tam prefill
  (13–15 s) + sonrasındaki turun bir tam prefill'i demek. Eşiği düşürmek
  compaction sayısını artırır, yani aynı maliyeti daha SIK ödetir. Asıl sorun
  eşik değil, sıkıştırma SONRASI 15 bin token'lık taban — o pi tarafında.
  Ölçmeden değiştirmedim.
* **Sıkıştırma sürerken gelen turlar?** 8 stall'ın **1'i** compaction
  penceresine denk geliyor (22:10:30). DEVİR §4 madde 2'nin "bekleme
  çözülmedi" notu bu sayıyla güncellendi.
* ⚠️ Compaction **senkron yapılmadı** — sessizlik hatası geri gelmesin.

---

## 4. Değişen dosyalar

| dosya | ne |
|---|---|
| `worker/pi_brain.py` | `brain_slots_url()` / `prefill_grace()` / `brain_probe_timeout()` / `slot_mark()` / `BrainPrefillProbe`; tur döngüsünde prefill kapısı |
| `worker/tests/test_prefill_watchdog.py` | yeni, 20 test — ⚠️ `.gitignore`'da (`worker/tests/` "ne git'te ne sunucuda dursun", kullanıcı kararı) → commit'e GİRMEDİ, lokalde duruyor |
| `worker/.env.example` | `PI_PREFILL_GRACE` / `PI_BRAIN_SLOTS_URL` / `PI_BRAIN_PROBE_TIMEOUT` + ölçüm notu |
| `handoff/2026-07-27-DEVIR.md` | §4 madde 2 güncellendi |

`worker/truth_check.py`'a **DOKUNULMADI** (ayrı tura verildi).
`higgs-tts` ve `pi-service`'e **DOKUNULMADI**.

Testler: **387 geçiyor** (önce 367, +20 yeni). `./check.sh` bulguları
değişmedi (değişiklikten önce de sonra da aynı 4 bulgu).

---

## 4b. DEPLOY — YAPILMADI, komutlar burada

Kullanıcı canlıda konuşuyordu; `candan-worker` restart oturumu keserdi. Ayrıca
kural: **sunucu değişikliğini kullanıcı yapar.** Sadece `candan-worker` restart
edilir — `candan-brain` / `higgs-tts` / `pi-service` ELLENMEZ.

```bash
# 1) kodu gönder (tek dosya değişti)
scp worker/pi_brain.py root@192.168.0.25:/opt/candan-lite/worker/pi_brain.py

# 2) (opsiyonel) yeni kolları .env'e yaz — YAZILMAZSA varsayılanlar zaten geçerli:
#    PI_PREFILL_GRACE=60, PI_BRAIN_SLOTS_URL boş (CLAIM_CHECK_URL'den türetilir)
ssh root@192.168.0.25 'grep -q PI_PREFILL_GRACE /opt/candan-lite/worker/.env || \
  printf "\n# prefill farkindalikli watchdog (bkz. handoff/2026-07-27-gecikme-ve-stall.md)\nPI_PREFILL_GRACE=60\n" \
  >> /opt/candan-lite/worker/.env'

# 3) YALNIZ worker restart
ssh root@192.168.0.25 'systemctl restart candan-worker'

# 4) doğrulama — stall yerine bu satır görülmeli:
ssh root@192.168.0.25 'journalctl -u candan-worker -f | grep -E "prompt.u İŞLİYOR|tur stall"'
```

Beklenen: `pi tur: beyin prompt'u İŞLİYOR (12s sessiz, toplam prefill payı
12/60s) → stall SAYILMADI` satırı, eskiden `pi tur stall: 12s ilerleme yok`
gelen yerde.

---

## 5. TEK BLOK GERİ DÖNÜŞ

```bash
# Prefill farkındalıklı watchdog'u KAPAT — davranış 27 Tem 22:00'daki gibi olur
# (kod yerinde kalır, sabit 12s eşiğe döner).
ssh root@192.168.0.25 'printf "\nPI_PREFILL_GRACE=0\n" >> /opt/candan-lite/worker/.env && \
  systemctl restart candan-worker'

# Kodu tümden geri al:
#   git revert <commit>   &&   deploy   &&   systemctl restart candan-worker
```

---

## 6. Sıradaki (bu turun DIŞINDA)

1. **Kullanıcıya sorulacak:** `candan-brain` `--parallel 2` olsun mu?
   Kazanç: sınıflandırıcı ve compaction, konuşmanın KV önbelleğini artık
   silmez → 13–15 s'lik prefill'ler tamamen kalkar. Bedel: slot başına bağlam
   32 768'e iner (ölçülen tepe 29 646 — pay dar). Model yeniden yüklenir.
2. Compaction sonrası 15 bin token'lık taban — pi tarafında; düşürülebilir mi?
3. `truth_check` yanlış alarmı (*"not aldım"* → *"Aslında bunu kaydetmedim"*)
   — **ayrı tura verildi**, burada dokunulmadı.
