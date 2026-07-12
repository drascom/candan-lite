# candan-lite — HANDOFF (2026-07-12)

> ⚠️ **AŞAĞIDAKİ BÖLÜM GÜNCELDİR — önce bunu oku.** Altındaki eski bölümler tarihsel
> (özellikle "Beyin = OpenAI-uyumlu /v1 + PIDEV_BASE_URL" İPTAL — pivot edildi).

## 🔥 2026-07-12: pi gecikmesi ÇÖZÜLDÜ (40sn turlar) + global izolasyon

**Kök sebep (ölçümle kanıtlandı):** `pi` bir KODLAMA ajanı ve global kurulumdan built-in
tool'ları miras alıyordu, üstelik oto-onaylı. Gerçek oturumda Candan `read`×8, `edit`×6,
`bash`×2, `grep`×1 çağırmış — 23 tool çağrısının 19'u sesli asistanda gereksiz.
Her tool çağrısı = modele fazladan istek bacağı; `openai-codex` bunları WebSocket üzerinden
atıyor ve bacaklar ara sıra 30-100s asılı kalıp `WebSocket closed 1000` (0 token) üretiyor.
40sn turun anatomisi: `toolUse → toolResult → [30.8s asılı] → error WS-1000 → retry → 39.9s`.
Ölçüm: tool'suz tur TTFT medyan **2.4s**, tool'lu **6.4s** (2.7x). Aykırılar HEP tool'lu turlarda.

**Fix 1 — tool allowlist** (`7584165`): `PI_TOOLS_ALLOWLIST=memory_add,memory_search,web_search`
+ `PI_NO_BUILTIN_TOOLS=true`. ⚠️ **`--no-builtin-tools` TEK BAŞINA İŞE YARAMIYOR** — o bayrak
altında bile pi `read`/`find`/`ls` çağırdı (29.9s aykırı üretti). Built-in'leri gerçekten kesen
tek şey **allowlist (`-t`)**. Zorlayıcı testte built-in çağrısı SIFIR.
Yan fayda: asistanın repo'da oto-onaylı `bash`/`edit` çalıştırabildiği **güvenlik açığı kapandı**
(baseline'da gerçekten bir dosyaya yazabildiği doğrulandı).

**Fix 2 — global izolasyon** (`c53f1cc`): `PI_ISOLATED=true` (default) →
`--no-extensions --no-skills --no-prompt-templates --no-themes --no-context-files`.
Worker'ın pi süreci `~/.pi/agent`'tan şunları miras alıyordu: `filechanges`, `read-only-mode`,
`pi-beautify`, **global `memory.ts`** (bizim mem extension'ının YANINDA ikinci hafıza sistemi!),
`pi-mcp-adapter` → `ha-builtin` MCP sunucusu, `stop-slop` skill.
Bayraklar sadece KEŞFİ kapatıyor; `-e` ile verdiğimiz mem extension ve `--skill` ile verdiğimiz
memory skill AYNEN yükleniyor (kanıt: izolasyonlu spawn stdout'u BOŞ; `memory_search` canlı
turda gerçek veriden cevap verdi). **Kullanıcının global pi kurulumuna DOKUNULMADI.**
Brain VPS'te çalışacak ve orada global pi olmayacak → izolasyon zaten prod davranışını taklit ediyor.
⚠️ `web_search` pi built-in'i DEĞİL, global `npm:pi-web-access`'ten geliyordu → izolasyonla
**web arama yeteneği ŞU AN YOK** (allowlist'teki isim ölü giriş, zararsız). Gerekirse lokal
extension olarak eklenecek. Kullanıcı diğer tool'ları "birer birer" kendisi ekleyecek.

**Oturum şişmesi — ROTATE GEREKMİYOR (ölçüldü, varsayım çürüdü).** 185 KB'lık gerçek oturumda
TTFT medyan **1.81s** (hedef ≤2s). Bağlam etkisi ~**2.4ms/KB**, lineer, kırılma noktası YOK;
TTFT'nin 2s'yi aşması için ~380-400 KB gerekir. Daha önceki "185KB → 4.17s (2.5x)" ölçümü
**tool'lar açıkken** yapılmıştı — o yükün asıl kaynağı bağlam değil TOOL BACAKLARIYMIŞ.
→ Session rotasyonu / alt-oturum / eşik mantığı **YAZILMADI** (gereksiz karmaşıklık olurdu).
~350 KB'a yaklaşınca yeniden ölç. Boot enjeksiyonu (persona+profile+family+skill) toplam
~3.9 KB (~1000 token) — TTFT'ye ölçülebilir katkısı YOK.

**Oturum davranışı (mevcut, doğru):** `--session-id <slug>` ile **kişi başına KALICI oturum** —
bağlan/kes yeni oturum AÇMIYOR, aynı dosyaya devam ediyor (`baba` oturumu 10 Tem'de açıldı,
12 Tem'de hâlâ aynı dosyada). Kullanıcının istediği "tek ana oturum sürekli devam etsin"
davranışı ZATEN VAR. `session-finalize` (`agent.py:87` → `PiBrain.finalize()`) da bağlı ve çalışıyor.

**Hafıza durumu:** dead/bağlanmamış modül YOK. `pi/extensions/mem/index.ts` (memory_add/
memory_search) + `pi/skills/memory/SKILL.md` + boot enjeksiyonu + `MEM_USER` + policy.json +
FTS5 index + finalize → hepsi bağlı ve canlı loglarda çalışıyor. Planlanıp YAZILMAYAN: `tools/mem`
CLI (gereksiz, extension yerini aldı) ve **git-audit** (`memory/.git` var ama TEK COMMIT YOK,
kodda hiç git çağrısı yok → fiilen ÖLÜ).
`Moduler_Cok_Kullanicili_Hafiza_Sistemi_Plani.md` (`32c4ea5`) = kullanıcının İLK planı, **kuzey
yıldızı/referans** — ŞU AN UYGULANMAYACAK. Sistem bitince son halle karşılaştırılıp sapma
kontrolü yapılacak. (Mevcut pi-native hafıza plandaki ihtiyaçların çoğunu çok daha hafif karşılıyor.)

**⏳ AÇIK: CANLI TEST YAPILMADI.** Yukarıdaki iki fix CLI ölçümleriyle doğrulandı ama sesli
oturumda test edilmedi. Worker yeni ayarlarla ayakta. Kullanıcı tarayıcıyı TAM KAPATIP yeniden
bağlanmalı → konuşup doğrulayacak: turlar hızlandı mı, 40sn takılma tekrar ediyor mu, hafıza
çalışıyor mu. ⚠️ WS-1000 fix sonrası hiç YENİDEN ÜRETİLEMEDİ (her varyantta 0) — yani fix'in
takılmaları azalttığı DOĞRUDAN kanıtlanmadı; kanıtlanan şey onlara yol açan tool bacaklarının
sıfırlandığı.

## 🔌 BAĞLANTI YARIŞI ÇÖZÜLDÜ (2026-07-12, `da05992`) — explicit dispatch

**Sorun:** "bazen bağlanıyor, bazen bağlanmıyor / agent odaya giremiyor" — **sessiz** başarısızlık,
worker logda sapasağlam "registered worker" yazıyor, hata YOK, sadece iş gelmiyor.

**Kök sebep:** worker `agent_name`'siz kaydoluyordu → LiveKit **otomatik dispatch** modu.
Otomatik dispatch **SADECE oda OLUŞTURULURKEN** çalışır. Oda adı SABİT (`candan-lite-dev`) olduğu için:
- tarayıcı **açıkken** worker restart edilirse → oda LiveKit'te HÂLÂ YAŞIYOR → dispatch anı geçmiş →
  yeni worker odaya **GİREMEZ**.
- tarayıcı kapanıp oda ölürse → yeniden bağlanınca oda sıfırdan doğar → dispatch tetiklenir → çalışır.
Yarışı belirleyen tek şey: "restart anında oda ayakta mıydı".
(HANDOFF'taki eski "worker restart sonrası sekmeyi TAM KAPAT" gotcha'sının gerçek sebebi buydu.)

**Fix:** `WorkerOptions(agent_name="candan")` (`LIVEKIT_AGENT_NAME`) + web token route'unda
**server-side zorlanan** `RoomAgentDispatch({agentName})` (client göndersin göndermesin).
KANIT: JWT decode → `grant.roomConfig.agents[0].agentName = "candan"`.
Artık oda ister yeni ister eski olsun her bağlantı agent'ı açıkça çağırıyor; **restart sırası önemsiz**.

⚠️ **DİKKAT:** `agent_name` verildiği için **otomatik dispatch KAPANDI**. Web token'ı dispatch
istemezse agent odaya **HİÇ** girmez (yine sessizce). İki taraf da default `"candan"`a düşüyor —
**isimler ikiz kalmalı**, biri değişirse diğeri de değişmeli.

**Not — `wake_stt: WAKE tespit → ' Dondon.'`:** fuzzy wake toleransı gevşek, alakasız kelimeler
uyandırabiliyor. **Kullanıcı kararı: ŞİMDİLİK ÖNEMSİZ** — mobil versiyonda **yerleşik (cihaz)
speech recognition** kullanılacak, wake tespiti cihaza geçecek. Fuzzy eşiğini kurcalamaya gerek yok.

## 🧹 TAM SIFIRLAMA (2026-07-12 gece) — temiz sayfadan test

Kullanıcı isteğiyle HER ŞEY silindi (yedek: scratchpad `FULL-RESET-BACKUP-231914/`):
`worker/data/speakers.db` (ses kayıtları), `sessions/*.jsonl` (12 dosya), `memory/users/*`
(profil dahil), FTS index. `family.md` iskelete döndü. **`policy.json` = `{}`**.
Sebep: bench worker'ları gerçek hafızayı test notlarıyla kirletmişti ("Bench testi yapıldı" ×3 vb.);
kullanıcı temiz sayfadan yeniden test etmek istedi.

**İlk konuşmada beklenen akış:** tanımıyor → "adını söyler misin?" → isim → **policy BOŞ olduğu için
`adult`** (ev sahibi) → hafıza açık.

## 🧠 HAFIZA DÜZELTMELERİ (2026-07-12, `5d40a8e`)

**Sorun 1 — çift kayıt:** "ailece yemek yiyeceğiz, not al" → Candan **private'a** yazdı. Kullanıcı
"aile notuna yazmadın" deyince family'ye de yazdı **ama private'daki yanlış kaydı silmedi** → not
İKİ YERDE kaldı. Ayrıca aynı not defalarca ekleniyordu (dedup yoktu).

**Fix:** `memory_add`'e opsiyonel `replaces` alanı (yeni tool AÇILMADI):
- açık düzeltme → eski kayıt silinir, yenisi hedefe yazılır
- **örtük taşıma** → aynı not başka kapsamdaysa KOPYALANMAZ, **TAŞINIR**
- dedup: `dkey()` normalizasyonu (diakritik strip + lowercase + `ı`/`i` katlaması + alfanumerik dışı
  sadeleştirme). LLM/embedding YOK. Aynı kapsamda varsa "Zaten kayıtlı" döner.
- `SKILL.md`: **ailevi içerikte SESSİZCE private'a yazma → SOR** ("aile notuna mı, sana özel mi?")
  + düzeltmede yeni kayıt ekleme, taşı.

**Sorun 2 — enroll policy'ye YAZMIYORDU (kritik):** oto-enroll kişiyi `speakers.db`'ye kaydediyor ama
`policy.json`'a eklemiyordu → **yeni tanışılan herkes `guest` → hafıza YAZAMIYOR**. Bugünkü
"not alamıyorum" hatasının asıl mekanizması buydu.

**Fix:** `_policy_set()` (flock + tempfile + `os.replace`, atomik). Kural (kullanıcı kararı):
**policy BOŞSA ilk tanışan → `adult`** (ev sahibi); doluysa **sonrakiler → `guest`**
(hafıza YOK, aile hafızası görünmez — kullanıcı bunu bilerek seçti: misafir mahremiyeti + aile gizliliği).
Benzerlik kapısı mevcut kişiye merge ettiğinde policy'ye yeni girdi AÇILMAZ.
**Rol yükseltme ("X'i yetişkin yap") TOOL DEĞİL, worker'da scripted komut** — yetki LLM'in eline
verilmez (prompt-injection ile atlatılamaz). Aktör = **speaker-ID ile çözülen konuşmacı** (iddiası
değil); guest reddedilir, istek pi'ya bile gitmez. Test: guest'in 3 denemesi de REDDEDİLDİ, policy sabit.

Roller (mem/index.ts `canSee`): `adult` = private+family+project · `child` = private+family ·
**`guest` = HİÇ hafıza yok**.

## 🔌 KARAR: paket/extension KURMA YOK (şimdilik)
Kullanıcı: sistem ileride **kendi kendine** extension/tool/skill kurabilsin (Hermes gibi) — o yüzden
şimdi elle kurmayalım. `web_search` lokal extension işi **İPTAL** (allowlist'teki `web_search` ölü
isim olarak duruyor, zararsız; kurulunca canlanır).
⚠️ Orkestratörün şartı: self-install olacaksa **insan onaylı** olmalı (Candan "şu paketi kurmam
lazım" TEKLİF etsin, kullanıcı ONAYLASIN). Otomatik kurulum = tedarik zinciri riski; bugün pi'nin
kendi built-in tool'larının 40sn gecikme + oto-onaylı `bash`/`edit` güvenlik açığı yarattığını ölçtük.

## ✅ CANLI TEST GEÇTİ (2026-07-12) — fix'ler doğrulandı

| | Fix öncesi | Fix sonrası (canlı) |
|---|---|---|
| Medyan TTFT | 3.11s | **1.85s** |
| Medyan tur | 3.35s | **2.22s** |
| En kötü tur | 11.87s | **7.50s** |
| >10s takılan tur | 2 | **0** |
| `WebSocket closed 1000` | var | **yok** |
| Built-in tool (read/edit/bash) | çağrılıyordu | **hiç çağrılmadı** |

Hafıza canlı doğrulandı (Candan kullanıcıyı + köpeklerini hatırladı).

## 🆔 KİMLİK: `baba` → `ayhan` (2026-07-12, `4660882`)

**Bulunan sorun:** aynı kişi İKİ kimliğe bölünmüştü — `baba` (adult, hafızası dolu) ve `ayhan`
(policy'de YOK → **guest** → hafıza YAZAMIYOR). Kullanıcı "not al" deyince Candan "erişimim yok"
diyordu — doğru söylüyormuş.

**Kök sebep:** `baba` sadece **2 ses örneğiyle** enroll edilmiş (zayıf centroid) → farklı gün/mikrofon
koşulunda skor `SPEAKER_THRESHOLD`(0.45) altında kaldı → 5 miss → unknown → "adını söyler misin?"
→ kullanıcı "Ayhan" dedi → `_create_speaker()` **SADECE İSME** bakıyordu → YENİ KİMLİK açtı.
Sistemde **"bu ses zaten kayıtlı birine benziyor mu?"** kontrolü YOKTU.

**Migrasyon:** policy `{"ayhan":"adult"}`, `memory/users/baba/` → `memory/users/ayhan/`,
persona `pi/personas/ayhan.md`, speakers.db'de Baba'nın 2 örneği Ayhan'a taşındı (→ **6 örnek**,
silme yok). FTS5 index kendini onardı (dosyalar otoriter). Yedek: scratchpad `backup-20260712-221520/`.
Doğrulandı: `ayhan` kimliğiyle eski hafıza geliyor (Oscar/Amy, Londra) + `memory_add` yazıyor.

**Enroll ses-benzerlik kapısı (tekrar bölünmesin):** `_finish_enrollment()` artık yeni kişi açmadan
ÖNCE embedding'i tüm centroid'lere ölçüyor → `skor >= threshold` = yeni kimlik AÇMA, mevcut kişiye
EK ÖRNEK / `merge_low(0.35) <= skor < threshold` = **"Sen X misin?"** diye SOR / altındaysa yeni kişi.
`SPEAKER_THRESHOLD` **0.45'te BIRAKILDI** (düşürmek yanlış-pozitifi artırır: misafiri ev sahibi sanıp
özel hafızasına yazma riski). Artımlı öğrenme eklendi ama **default KAPALI** (`SPEAKER_LEARN_ENABLED=false`).

**Log gürültüsü:** livekit `dev` modu log_level'ı DEBUG yapıyordu → her sessiz pencere basılıyordu.
Artık `WorkerOptions(log_level=INFO)` + `worker/log_utils.py` `DedupeFilter` (aynı mesaj 30sn içinde
tekrarlanırsa susturulur, pencere sonunda "[+N tekrar bastırıldı]" özeti). `WORKER_VERBOSE_LOGS=true`
→ eski ham davranış.

**Bekleyen küçük kararlar:** (a) `sessions/` altındaki bench artıkları (`*_bench-*.jsonl`) silinsin mi
— SORULDU, cevap YOK; (b) `sessions/*_baba.jsonl` (191 KB) tarihsel olarak duruyor, yeni turlar
`*_ayhan.jsonl`'e gidiyor — bırakılması önerildi; (c) `speakers.db`'de boş `Baba` satırı (id=1,
0 örnek) etkisiz duruyor; (d) `web_search` lokal extension olarak geri eklensin mi (izolasyonla gitti).
`konusma.md` kullanıcı izniyle SİLİNDİ.

## 🟢 GÜNCEL DURUM (2026-07-10 session sonu) — hepsi çalışıyor, main'de push'lu

**Repo:** github.com/drascom/candan-lite (PUBLIC). `web/` absorbe edildi. `memory/` gitignored (kişisel veri public'e girmez) + içinde nested audit-git.

**Mimari (kilitli):**
- **Beyin = `pi` CLI**, warm `--mode rpc` alt-süreci (HTTP /v1 DEĞİL). Codex subscription. Model pin: `PI_MODEL=openai-codex/gpt-5.6-terra` (global `gpt-5.6-luna` bozuk). thinking=minimal. `worker/pi_brain.py`. Detay: `docs/pi-brain-design.md`.
- **Ses:** livekit-agents `AgentSession` — web → Whisper STT (wyoming .25:10300) → pi beyni → OmniVoice TTS (.25:8808, **24kHz**) → ses + barge-in.
- **Speaker-ID:** campplus (sherpa-onnx), konuşarak oto-enroll (bilinmeyen ses→"adını söyler misin?"→onay→kaydet). **Enrollment ODA sesinden olmalı** (CLI mic yolu tanımayla uyuşmaz). Sticky (`SPEAKER_VAD_RMS`/`SPEAKER_STICKY_MISSES`). Kullanıcı=slug → persona `pi/personas/<user>.md` + session `<user>` + `MEM_USER`.
- **Hafıza = PI-NATIVE (Hermes YOK):** `pi-hermes-memory` extension KALDIRILDI (`pi remove`). Kendi **lokal** extension'ımız `pi/extensions/mem/index.ts` (`memory_add`/`memory_search`, node:sqlite FTS5, per-user `memory/users/<user>/`, rol=policy.json). Worker `-e` ile SADECE kendi pi'sine yükler. Boot: profile.md+family.md enjeksiyon. Session-finalize (kapanışta 3-5 kalıcı not). Detay: `docs/hafiza-v2-plan.md` (⚠️ Hermes-çerçeveli, tarihsel) yerine gerçek = `pi/extensions/mem/`.

**Wake word tasarımı (worker-tarafı):**
- Wake word **"candan"**, akış: **tek başına "candan" → çan → sonra soru** (iki-adım). Konuşma penceresi 15s sessizlikte uyur. Uyurken sessiz (token yok). `WakeGate` PiBrain'de; `wake_match()` merkezi (izole/cümle ayrımlı **fuzzy** — izole yanlış-çevirileri "John Don/Can dan/Kandan" yakalar, cümlede sadece gerçek "candan").
- **Wake durumu web'e:** `candan.awake` participant attribute. Web: uyurken kullanıcı transcript'ini GİZLER + uyan/uyu **çanı** (Web Audio, %100). Transcript'i worker'da toggle ETME (TranscriptSynchronizer bozuluyor — web'de gizle).
- **Anlık wake:** `WAKE_STT` (VAD + kısa-pencere Whisper, ~200ms, SADECE uyurken) — erken çan. `wake_now()` idempotent.
- **Env (worker/.env):** `WAKE_ENABLED=true WAKE_WORD=candan WAKE_WINDOW_SECONDS=15 WAKE_VARIANTS=candan,kandan,... WAKE_STT_ENABLED=true WAKE_STT_WINDOW=1.5`.

**Web UI:** dark mode, LiveKit branding YOK, **auto-connect** (başlat/bitir butonu YOK), alt **debug durum satırı** (😴Uykuda/👂Dinliyorum/🧠Düşünüyorum/🗣️Konuşuyorum), waveform. **Kamera + ekran-paylaşımı butonları KALDI** (ileride görüntü/ekran-desteği — KULLANICI istedi, KALDIRMA).

**.25 GPU:** Qwen fallback (vllm.service) **durduruldu+disable+silindi** (14.6GB GPU + 9GB disk açıldı). Whisper+OmniVoice sağlam.

## Çalıştırma
- Worker (Mac): `cd worker && .venv/bin/python agent.py dev` (venv kurulu; sherpa-onnx/soundfile/sounddevice/websockets dahil).
- Web: `cd web && pnpm dev` → localhost:3000.
- Worker RESTART sonrası: tarayıcı sekmesini TAM KAPAT → yeniden bağlan (mevcut odaya oto-dispatch olmaz).

## ✅ KAPANDI (2026-07-12): KWS "Jackie" — canlı testte ELENDİ, wake word "candan" kalıyor

**KARAR: Jackie İPTAL.** Mevcut sistem korunuyor: Whisper-"candan" (`WAKE_STT` + fuzzy `wake_match`).
Canlı test gerçek insan sesiyle yapıldı (kullanıcı konuştu, Claude arka planda çalıştırıp logları okudu).

**Sonuç:**
- **KWS mekanizması canlı mikrofonda ÇALIŞIYOR** — kontrol kelimesi "forever" gerçek sesle tetikledi. Yani ölü olan mekanizma değil, **sadece "Jackie" kelimesi**.
- **"Jackie" / "Hey Jackie" ADİL koşulda defalarca denendi → HİÇ tetiklemedi.** (Adil = sinyal referans seviyesine çıkarılmış: peak_out 0.66 vs referans wav peak 0.42.)
- Muhtemel sebep: "jackie" GigaSpeech'te seyrek (BPE: `▁JA CK I E`, zayıf temsil) + TR aksanı İngilizce akustik modele uymuyor. "forever" ise sık geçen + modelin kendi örnek keyword'ü.

**⚠️ DERSLER (KWS'e dönülürse aynı tuzağa düşme):**
1. **Eski handoff'taki eşikler YANLIŞTI.** `--threshold 0.25 --score 1.5` ile kontrol keyword'ü **referans wav'da bile tetiklemiyor**. Çalışan bölge: **`threshold ≤ 0.20` VE `score ≥ 4.0`** (iki knob BİRLİKTE gerekiyor). O ayarla teste girilseydi "Jackie tutmuyor" sonucuna YANLIŞ sebeple varılacaktı.
2. **Mikrofon seviyesi KRİTİK.** HUAWEI USB-C headset ham peak ~0.03 = referans wav'ın (0.42) ~14 katı altında. **O seviyede "forever" bile tetiklemiyor.** Gain/AGC olmadan yapılan her KWS testi GEÇERSİZ. Araçta `--agc` (hedef peak 0.40, sessizlikte gürültü şişirmez) ve `--gain X` var.
3. `level: 0.000` = macOS mikrofon izni yok (ÇÖZÜLDÜ). İzin verilince Claude'un kendi süreci de mikrofonu okuyabiliyor → canlı testi Claude çalıştırıp logu okuyabilir, kullanıcı sadece konuşur.
4. sherpa-onnx per-keyword `:score #thr @name` sözdizimi **sessizce çalışmıyor** → düz token dosyası + global `--score` kullan.
5. Model asset adı **tarihli**: `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2` (tarihsiz isim 404).

**Test aracı SAKLANDI:** `worker/tools/live_mic_kws.py` (üretmesi pahalıydı; scratchpad session'la silinir).
Kullanım: `cd worker && .venv/bin/python tools/live_mic_kws.py --agc --threshold 0.10 --score 5.0`
(model ilk çalıştırmada indirilir; başlangıçta referans wav ile kendini test eder — "forever" tetiklemezse kurulum bozuktur, mikrofona geçme.)

**KWS'e dönülürse yapılacak (YAPILMADI):** aday kelime turu — 6-8 İngilizce aday (computer, jack, jackson, hey jack…) aynı anda encode edilip tek oturumda test edilir, kullanıcının sesiyle tutarlı tetikleyen aranır.

## Memory (index): delegation=Agent tool; kaldırmadan-önce-sor. Kişisel hafıza dosyaları `memory/` (gitignored, local).

---
Yeni session bununla devam eder. Özet: ağır **Hermes+plugin** yığını donduruldu;
Candan'ın hafif yeniden yapımı `candan-lite` başlatıldı. Beyin = **pi.dev agent**
(OpenAI-uyumlu `/chat`), ses = **livekit-agents** üstünde ince worker.

## Kararlar (kilitli)
- **Beyin:** pi.dev agent, **OpenAI-uyumlu `/v1`** → worker'da `openai.LLM(base_url=…)`, sıfır glue.
- **Beyin konumu:** şimdilik **local PC**; olgunlaşınca remote (tek değişen `PIDEV_BASE_URL`).
- **LiveKit server:** oracle-stage'de **kalıcı** (:7880). **STT/TTS:** .25 GPU (Whisper :10300 wyoming, OmniVoice :8808) — mevcut.
- **Client:** `web/` (LiveKit agent-starter-react). Ses+metin aynı worker→aynı beyin.
- **Ses worker'ı:** livekit-agents `AgentSession` — VAD/turn/barge-in framework'ten; sadece STT+TTS custom plugin. (Eski adapter.py ham rtc kullanıyordu, 137KB; onu kullanmıyoruz.)

## Yapıldı ✅
1. **oracle-stage Hermes durduruldu + disable:** `hermes-serve`, `hermes-gateway`, `hermes-webui` (boot'ta gelmez).
   - `livekit-server.service` **AÇIK bırakıldı** (bağımsız systemd; config: `/home/ubuntu/.hermes/mate_voice/livekit/livekit.yaml`; `use_external_ip:true`).
   - GERİ DÖNÜŞ: `ssh oracle-stage 'sudo systemctl enable --now hermes-serve hermes-gateway hermes-webui'`
2. **web/** — `livekit-web` komple taşındı (kendi `.git`'i içinde). **Hermes'ten koparıldı → direct-mint token:**
   - `.env.local`: `MATE_DISCOVERY_ENDPOINT` KALDIRILDI, `MATE_LIVEKIT_ROOM=candan-lite-dev` eklendi.
   - `pnpm build` **OK**. (Runtime testi kullanıcı yapar.)
3. **worker/** skeleton: `agent.py` (AgentSession + `WhisperWyomingSTT`/`OmniVoiceTTS` custom plugin **iskele**, henüz port edilmedi), `requirements.txt`, `.env.example`.
4. `README.md` + bu `HANDOFF.md`.

## 🎉 HAFIZA FAZ B ÇALIŞIYOR — Pİ-NATIVE (2026-07-10)
**KARAR: kendi memory sistemimiz Pi-native; Hermes YOK.** `pi-hermes-memory` extension'ı KALDIRILDI (`pi remove`) — Hermes-türeviydi + bizim sistemle paralel koşup karışıklık yapıyordu.
- **Kendi lokal extension'ımız:** `pi/extensions/mem/index.ts` — `memory_add` + `memory_search` custom tool'ları (pi `registerTool` API). Worker pi'yı `-e pi/extensions/mem/index.ts` ile **sadece kendi süreçlerinde** yükler (global DEĞİL; senin kişisel pi'na dokunmaz).
- **Kullanıcı-başı:** `MEM_USER` env → `memory/users/<user>/` + rol (adult/child/guest, `policy.json`). Depolama = repo'daki markdown dosyalar (otoriter) + `node:sqlite` FTS5 (`memory/.index/mem.db`, Türkçe diacritics-duyarsız). `memory/` gitignored + nested audit-git.
- **Session-finalize:** `ctx.add_shutdown_callback` → `PiBrain.finalize()` → pi oturum kapanınca 3-5 kalıcı not çıkarır (30sn best-effort). Canlı test: köpek isimleri kendiliğinden kaydedildi.
- **Canlı doğrulandı:** memory_add(private+family), memory_search (FTS getirme), correction/update, izolasyon, finalize. Hepsi sesli oturumda.
- **AÇIK KONU (D fazı):** pi rpc'de ara sıra `WebSocket closed 1000` + tek turda ~40sn gecikme. Ses için yüksek — kararlılık/gecikme incelenecek.
- Eski plan dokümanları (`docs/hafiza-v2-plan.md`, `hafiza-implementasyon-rehberi.md`) Hermes-çerçeveliydi; artık geçersiz/tarihsel — gerçek = bu bölüm + `pi/extensions/mem/`.

## 🎉 FAZ 3 + HAFIZA-A ÇALIŞIYOR (2026-07-10)
- **Speaker-ID (campplus, sherpa-onnx):** oto-enroll konuşarak (bilinmeyen ses → "adını söyler misin?" → onay → kaydet). Tanıma tutarlı.
- **KRİTİK DERS — enroll = recognize AYNI ses yolu:** CLI `--record` (Mac mic, ham) ile enroll edilince tanıma skorları düşük/oynak (0.1-0.6) çünkü oda sesi tarayıcı-WebRTC işlemeli. **Oda sesinden oto-enroll** (SpeakerState.last_embedding) → skorlar 0.45-0.67, tutarlı. Enrollment'ı HEP oda sesinden yap.
- **Sticky speaker-ID:** `SPEAKER_VAD_RMS`(0.01) sessizlik-kapısı + `SPEAKER_STICKY_MISSES`(5) → kısa sessizlik/dip'lerde konuşmacı korunur, pi swap thrash yok.
- **Hafıza Faz A:** boot enjeksiyonu (persona+profile+family, role-gated) + `MEM_USER` env + memory-skill v0. Test edildi: özel not→`notes/`, aile→`family.md`(açık istekle), profil kendini zenginleştirdi, izolasyon OK. `memory/` gitignored (public repo'ya girmez) + nested audit-git.
- **Kullanıcı:** ~~`baba`~~ → **`ayhan`** (adult, policy.json). persona `pi/personas/ayhan.md`. Model pin `gpt-5.6-terra`.
  ⚠️ 2026-07-12'de kimlik `baba`→`ayhan` olarak taşındı (aynı kişi ikiye bölünmüştü). Detay en üstteki bölümde.
- **SIRADAKİ:** Hafıza Faz B (`tools/mem` CLI + SQLite FTS5 arama + session-finalize + git-audit). Detay: `docs/hafiza-implementasyon-rehberi.md`.

## 🎉 FAZ 2 ÇALIŞIYOR (2026-07-10): sesli uçtan-uca
web → STT (Whisper wyoming) → pi beyni (Candan, warm rpc) → TTS (OmniVoice) → ses. Test edildi, normal hız.
- **Repo:** github.com/drascom/candan-lite (public, main). web/ absorbe (fork silindi).
- **Çalıştırma:** worker `cd worker && .venv/bin/python agent.py dev`; web `cd web && pnpm dev` (:3000).
- **OmniVoice gerçek çıktı = 24kHz** (audio_start bildiriyor; referans "48kHz" YANLIŞti → 2× hız bug'ıydı, düzeltildi).
- **GOTCHA — worker restart:** worker'ı yeniden başlatınca mevcut odaya OTOMATİK dispatch OLMAZ (sadece yeni odaya). Tarayıcı sekmesini TAM KAPAT → tekrar bağlan (oda sıfırdan oluşsun).
- Not: cloud turn-detector 401 → yerel mini modele düşüyor (zararsız; Faz 3'te düzeltilir).

## KARAR GÜNCELLEME (2026-07-10): beyin = pi CLI, warm RPC
`PIDEV_BASE_URL` + `openai.LLM(base_url=…)` planı **İPTAL**. Beyin `pi` CLI (Codex sub),
worker onu **kalıcı `--mode rpc` subprocess** olarak sürer. Detay: `docs/pi-brain-design.md`.
- Warm per-user süreç → cold-start oturum başına 1 kez (tur başına DEĞİL). Pre-warm @join.
- Local pi config: `candan-lite/pi/` (personas/skills/settings) + `sessions/<user>/` memory.
- Kimlik = speaker-ID (`hermes-livekit/voice/speaker*.py` port). Warm-havuz şimdilik YOK.
- Referans client: `~/work/candan assistant/mate-mac/` (macOS).

## SIRADAKİ (revize öncelik)
1. ✅ **`pi/` local scaffold** — AGENTS.md, personas/candan.md, skills/, settings.json.
2. ✅ **`worker/pi_brain.py`** — warm `pi --mode rpc` LLM adaptörü. Protokol runtime doğrulandı:
   `message_update.assistantMessageEvent.text_delta` stream; `agent_settled` tur bitişi; abort@barge-in.
   ⚠️ **Model:** global varsayılan `gpt-5.6-luna` Codex'te BOZUK ("Model not found"). Pin: `PI_MODEL=openai-codex/gpt-5.6-terra` (worker/.env). Alternatif: gpt-5.4/5.5/5.6-sol.
   Test: `python worker/pi_brain.py smoke` (get_state) ve `... prompt "merhaba"` (metin) → PASS.
3. **Faz 2 test:** metinle uçtan uca (web chat → worker → pi rpc → cevap) — worker'ı gerçek LiveKit odasında çalıştır (livekit-agents kurulumu + `python agent.py dev`). Bu RUNTIME testi KULLANICI yapar.
4. **STT/TTS port:**
   - `worker/whisper_stt.py` ← `../candan assistant/hermes-livekit/adapter.py` ~**1399-1543**.
   - `worker/omnivoice_tts.py` ← `hermes-livekit/voice/tts.py` + OmniVoice etiket (adapter ~207-221).
5. **Speaker-ID port:** `worker/speaker_id.py` ← `voice/speaker.py` + `speaker_store.py` → persona seçimi.
6. **Deploy:** worker+pi remote, systemd/compose.

## Anahtar bilgiler
- **LiveKit URL:** `wss://mate-livekit.drascom.uk` (→ oracle-stage :7880)
- **LiveKit key/secret:** `web/.env.local` ve `worker/.env` içinde (gitignored — public repo'ya girmez; web ve worker AYNI değeri kullanır)
- **Oda (dev):** `candan-lite-dev` (eski Hermes worker'ıyla çakışmasın diye ayrı)
- **STT/TTS:** `192.168.0.25:10300` (Whisper wyoming) / `192.168.0.25:8808` (OmniVoice)
- **SSH:** `ssh oracle-stage` (~/.ssh/config; passwordless sudo var)
- **Kaynak repo (referans, DOKUNMA):** `~/work/candan assistant/hermes-livekit/` (donduruldu, silinmedi)

## Kurallar (memory'den)
- `Mate-IOS/`'a dokunma. Commit'e AI imzası koyma. Git akışı: sadece main.
- Claude görsel/runtime test YAPMAZ — sadece build doğrular; app'i kullanıcı açar.
- Sunucuda git-takipli dosyaları elle düzenleme (plugin/monorepo); .25 kutusu serbest.

Memory: `candan-lite-pivot-plan.md` (index'te ⭐⭐ EN GÜNCEL).
