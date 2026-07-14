# Çok-İstemcili candan — Sunucu Sözleşmesi ve Yol Haritası

Durum: TASLAK / karar dokümanı. Kod yok, sözleşme var.
Kapsam: candan-lite'ı tek-web-istemcisi varsayımından çıkarıp, çok cihazlı bir platforma çevirmek.
Prensip: **Sunucu sözleşmedir. İstemciler o sözleşmenin üstünde ince kabuklardır.** Bir davranış birden fazla istemcide tekrar ediyorsa yanlış yerdedir.

---

## 0. Kilitlenmiş kararlar (tartışma dışı, girdi)

1. **Oda modeli: cihaz/oturum başına oda.** Ortak "ev odası" yok. Süreklilik odadan değil, oda-dışı hafıza katmanından gelir.
2. **Wake word cihazda çözülür ve KİMLİKTEN BAĞIMSIZDIR.** Pi → openWakeWord, iOS/Mac → SFSpeechRecognizer. Cihaz kimseyi tanımaz, ses tonu ayırt etmez — herhangi bir ses "candan" dediğinde uyandırabilir; cihazın tek işi "biri wake kelimesini söyledi" demektir (amaç: GPU'yu boşuna yormamak, 5 hoparlör sürekli ses akıtmasın). Kimlik SUNUCUDA, uyanıştan sonraki **ilk konuşmadan speaker-ID ile** çözülür (§3.1, §3.2) — bu yüzden cihaz **ön-tampon (pre-roll)** göndermek ZORUNDADIR (§4.5), yoksa kısa komutlarda kimlik sessizce çözülemez. Sunucu yalnızca uyanıştan sonraki (pre-roll dahil) ses görür. **İstisna: web** — tarayıcıda cihaz-içi wake zayıf → sunucu-taraflı `worker/wake_stt.py` + `WakeGate` orada yaşamaya devam eder (bu yol zaten sürekli dinlediği için pre-roll'u bedava sağlar).
3. **Uyanıklık kapısının yönü ters çevrilir.** İstemci `awake`'i SET eder, agent TÜKETİR. (Bugün lite'ta agent `candan.awake`'i yayınlıyor — ters.)
4. **Raspberry Pi'ler aptal kalır, Wyoming konuşur.** Pi'ye LiveKit kurulmaz. Sunucuda **Wyoming ↔ LiveKit köprüsü** her Pi adına odaya katılır.
5. **Kimlik birincil kaynağı SES'tir (speaker-ID), cihaz değil.** `worker/speaker_id.py` + `speaker_tap.py` + `enroll.py` candan-lite'ta **zaten çalışıyor** (§3.1) — kişi kimliği konuşmacının sesinden çözülür, iddiasından değil (prompt-injection ile atlatılamaz). `device.kind = personal | shared` **silinmiyor** ama rolü küçüktür: kişisel hafızayı açan şey cihaz değil sestir.
   - **personal** (kişisel oda hoparlörleri ×3, telefonlar): ses+cihaz genelde aynı kişiye işaret eder (hız/güven kazandırır) ama kimliği yine ses çözer.
   - **shared** (salon, mutfak, +bahçe): kimlik yalnızca sesle çözülür; tanınmazsa/eşik altındaysa kişisel hafıza **asla** açılmaz (mevcut `adult`/`child`/`guest` rol kapısı zaten bunu yapıyor).
   - Cihaz sınıfının asıl işlevi: (a) tanınamama durumunda beklenen davranış, (b) hatırlatma/mesaj teslim hedefini seçmek (§3.5).
6. **İsim alanı: `mate.*`** (`candan.*` değil). İki olgun istemci (mac/iOS) zaten `mate.*` konuşuyor; değişecek taraf ucuz olan (tek web bileşeni). Geçiş sırasında worker bir faz boyunca ikisini de yayınlayabilir, sonra `candan.*` düşer (§6.2).

---

## 1. Hedef mimari

```
  CİHAZLAR                      SUNUCU                                  BEYİN / HAFIZA
  ────────                      ──────                                  ─────────────

  ┌────────────┐                                                   
  │ iOS / Mac  │  device wake (SFSpeech)                           
  │  (LiveKit) │──1─► TOKEN SERVİSİ ────────────────┐              
  └────────────┘      (TEK giriş kapısı)            │              
        │             /api/token                    │              
        │             · cihaz kaydını okur          │              
        │             · oda adını ÜRETİR            │              
        │             · grant'ı verir               │              
        │             · DISPATCH'İ ZORLAR ──────────┼──► createDispatch(agent="candan")
        │             · TTL 15dk                    │              
        │                                           ▼              
        └──2─► ┌──────────────────────────────────────────────┐    
               │            LiveKit  (oda = cihaz/oturum)     │    
  ┌────────────┐│  room: dev-<deviceId>                       │    
  │  Web       ││  ┌────────────┐        ┌──────────────────┐ │    
  │ (LiveKit)  │┼─►│ katılımcı  │◄──────►│  worker / agent  │─┼──► pi (warm --mode rpc)
  └────────────┘│  │  (istemci) │  ses   │  AgentSession    │ │      │
   sunucu-wake  │  └────────────┘  data  │  STT/TTS/VAD     │ │      ▼
   (istisna)    │    attr:              │  wake = TÜKETİCİ │ │   ┌────────────────┐
                │    mate.awake ────────►│                  │ │   │ HAFIZA KATMANI │
  ┌────────────┐│                        └──────────────────┘ │   │ memory/        │
  │ Pi (Wyoming│└─────────────────────────────────────────────┘   │  family.md     │
  │  APTAL)    │        ▲                                          │  soul.md       │
  │ openWake-  │        │ 3                                        │  users/<kişi>/ │
  │ Word + snd │  ┌─────┴──────────────────┐                       │   profile.md   │
  └─────┬──────┘  │ WYOMING ↔ LIVEKIT      │                       │   soul.md      │
        │         │ KÖPRÜSÜ                │                       │   notes/       │
        └────────►│ · Pi başına 1 katılımcı│                       │ policy.json    │
      Wyoming TCP │ · ses format çevirimi  │                       │ events.db      │
      (16k mono)  │ · awake'i O set eder   │                       └────────────────┘
                  └────────────────────────┘                         ODA-DIŞI, KALICI
```

Akış (tek cümle): **cihaz uyanır → token servisinden oda+token alır (dispatch zorlanmış) → LiveKit odasına girer → `awake` attribute'unu set eder → worker sesi işler → kimlik çözülür → hafıza kapısı açılır (ya da açılmaz) → pi cevap verir → TTS geri döner.**

Değişmeyen: LiveKit, worker/agent.py, pi beyni, hafıza katmanı, STT/TTS servisleri.
Değişen: token servisi (tek + cihaz-farkında), oda adı (sabit → cihaz başına), wake yönü, `mate.*` protokolü, +Wyoming köprüsü.

**Güçlü nokta:** speaker-ID **sunucu tarafında** çalışır, dolayısıyla **istemciden bağımsızdır.** "Wake word cihazda çözülsün" kararı (§0.2) kimlik mimarisini zedelemez — wake sonrası ses sunucuya ulaştığı an kimlik sesten çözülür, hangi cihazdan geldiği fark etmez. Pi ve telefon bundan **bedava** faydalanır; ayrı bir kimlik doğrulama mekanizması kurulmasına gerek yoktur.

---

## 2. Cihaz kayıt & kimlik sözleşmesi

### 2.1 Cihaz kaydı (device registry)

Sunucuda tek bir kayıt defteri. Kaynak-doğruluk (source of truth) burası; istemciler bunu **taşımaz**, sadece kendi `deviceId`'sini bilir.

```jsonc
// devices.json (veya memory/ yanında SQLite — bkz. Açık Soru #10)
{
  "dev-a1b2c3": {
    "kind": "personal",          // personal | shared
    "person": "ayhan",           // kind=personal ise ZORUNLU; shared ise YASAK (null)
    "label": "Ayhan'ın odası",
    "location": "ayhan-odasi",
    "transport": "wyoming",      // livekit | wyoming
    "capabilities": ["audio_in", "audio_out", "wake_onboard"],
    "created_at": "...",
    "revoked": false
  },
  "dev-salon": {
    "kind": "shared",
    "person": null,              // ← kasten boş. Bu boşluk bir GÜVENLİK ÖZELLİĞİ.
    "label": "Salon",
    "location": "salon",
    "transport": "wyoming",
    "capabilities": ["audio_in", "audio_out", "wake_onboard"]
  }
}
```

`person` alanının `shared` cihazlarda **şema düzeyinde yasak** olması bilinçli: "yanlışlıkla doldurulmuş bir alan" en kötü hata modumuzu (sessiz gizlilik ihlali) geri getirir. Token servisi bunu doğrular ve ihlalde **token vermez** (sessizce düşmez).

**`last_seen` / son-görülme kaydı (KARAR #3 için gerekli, yeni alan):** Hatırlatma/mesaj teslimi artık "hedef kişinin en son giriş yaptığı cihaza" gitmek zorunda (§3.5) — bunun için kayıt defterine kişi bazlı bir son-görülme izi eklenir:

```jsonc
// devices.json'a değil, kişi bazlı ayrı bir kayda (örn. memory/events.db → presence tablosu):
{ "person": "ayhan", "device_id": "dev-a1b2c3", "seen_at": "2026-07-14T09:12:00Z" }
```

Her `mate.hello` (cihaz kimliğinin çözüldüğü an, §6) + her speaker-ID eşleşmesi bu kaydı günceller: personal cihazda anında (`device.person` biliniyor), shared cihazda **kim olduğu speaker-ID ile doğrulandığı an** (§3.1) — cihaz kaydı değil, ses kaydı günceller. Pratik sonuç: bir kişi shared cihazda tanınırsa o da last_seen kaynağı olur; device.kind burada da teslim hedefini seçmekten başka bir şey yapmaz.

### 2.2 Token servisi — TEK olacak

Bugün **iki** token yolu var ve biri bozuk:

| Yol | Dispatch | Sonuç |
|---|---|---|
| lite `web/app/api/token/route.ts` | **VAR** (`withAgentDispatch` + `ensureAgentDispatch` → `createDispatch`) | Doğru. |
| heavy Hermes `/mate/token` (Swift istemciler bunu kullanıyor) | **YOK** (`RoomAgentDispatch` yok) | İstemci odaya girer, **agent gelmez, hata da vermez.** |

**Karar: tek token servisi = lite'ın `/api/token`'ı.** Hermes token yolu kapatılır. Swift istemciler `HermesTokenSource`'u lite endpoint'ine çevirir (URL + istek şekli; sınıf yaşar).

Token servisinin verdiği şey (sözleşme):

```
POST /api/token
  body: { device_id, device_secret }        ← pairing'den gelen uzun-ömürlü sır
  ->    { serverUrl, roomName, participantToken, participantIdentity, deviceKind }
```

Grant — **her alan gerekçeli**:

| Grant | Neden |
|---|---|
| `roomJoin`, `room=<üretilen>` | Oda adını **istemci seçmez, sunucu üretir.** İstemcinin oda seçmesi = başkasının odasına girme. |
| `canPublish`, `canSubscribe`, `canPublishData` | Ses + data kanalı. |
| **`canUpdateOwnMetadata: true`** | **KRİTİK.** Bu olmadan istemci `mate.awake` attribute'unu yazamaz — LiveKit **sessizce no-op** eder. Heavy'de tam olarak bu yaşandı. Wake yönü tersine dönünce bu bayrak zorunlu hale gelir. Bugünkü lite grant'ında **YOK** (`route.ts` → `createParticipantToken`). |
| `roomConfig.agents = [{ agentName: "candan" }]` | Yeni oda yolunda dispatch. |
| + server-side `createDispatch` | Var olan oda yolunda dispatch (gömülü davet yalnızca oda İLK yaratılırken işlenir). |
| `ttl = 15m` | Kısa. Yenileme istemcide. |

**Sessiz hata testi (her fazın kabul kriteri):** token alındıktan sonra odada **tam olarak 1 agent** katılımcısı olacak. 0 = dispatch düştü. 2 = dispatch yarışı (lite'ta `dispatchInFlight` kilidi bunun için var; yeni token yolunda **korunmalı**).

### 2.3 Oda adı

`room = "dev-" + deviceId` — **cihaz başına kalıcı oda adı.** (Oturum başına rastgele oda değil.)
Gerekçe: yeniden bağlanma aynı odaya döner; `ensureAgentDispatch` mantığı (oda yaşıyor + worker restart) zaten bu senaryoya göre yazılmış ve çalışıyor. Rastgele oda her seferinde soğuk başlangıç + yeni pi süreci demek. (Bkz. Açık Soru #2.)

### 2.4 Pairing / onboarding

Heavy'de çalışan akış var: `PairingSectionView.swift` + `Helpers/PairingClient.swift` + `Helpers/DeviceIdentity.swift` (kararlı cihaz kimliği). **Aynen taşınır**, hedefi lite olur:

```
1. Cihaz  → POST /api/pair/start { device_id, label, capabilities }  → { code: "482913" }
2. İnsan  → web'de "Cihazlar" sayfasında kodu görür, kind/person/location atar, ONAYLAR
3. Cihaz  → POST /api/pair/poll { device_id, code }                  → { device_secret }
4. Bundan sonra: device_secret → /api/token → kısa ömürlü LiveKit JWT
```

`kind` ve `person`'u **insan atar**, cihaz talep edemez. Cihazın kendi sınıfını beyan etmesi = ayrıcalık yükseltme.

### 2.5 Gömülü secret'lar — kaldırılacak

`mate-mac/VoiceAgent/Secrets.swift` ve `Mate-IOS/VoiceAgent/Secrets.swift`: **gömülü, 720 saat ömürlü, canlı LiveKit JWT + gateway token.** Repo'da duruyor.
Karar: dosya silinir, yerine pairing + 15dk TTL token gelir. Mevcut JWT'ler + LiveKit API key/secret **rotate edilir** (repo geçmişinde kalıyorlar).

---

## 3. Kimlik → hafıza kapısı

En kötü hata modumuz: **birinin kişisel hafızasının başkasına açılması.** Sesli, gürültülü, çok kişili bir evde bu sessizce olur — kimse fark etmez. Tasarım bunu *ihtimal* olmaktan çıkarmalı.

### 3.1 Bugünkü durum — ZATEN DOĞRU (önceki taslaktaki "ya hep ya hiç" bulgusu YANLIŞ ALARMDI, düzeltildi)

Kod incelemesi: kimlik **cihazdan değil sesten** çözülüyor ve hafıza kapısı zaten doğru çalışıyor.

- **Speaker-ID zaten var ve çalışıyor:** `worker/speaker_id.py`, `worker/speaker_tap.py`, `worker/enroll.py`, `worker/data/speakers.db` (kayıtlı ses örnekleri), `models/campplus.onnx`. `worker/agent.py:148-169` → `build_speaker_id()` + `SpeakerTap` + sticky-miss mantığıyla konuşmacı embeddingden tanınır. HANDOFF.md: "Aktör = **speaker-ID ile çözülen konuşmacı** (iddiası değil)" — kimlik kişinin söylediğinden değil sesinden çözülür, **prompt-injection ile atlatılamaz.**
- **Hafıza kapısı zaten doğru:** `worker/pi_brain.py` → `ROLES=("adult","child","guest")`; `_role(user)` `memory/policy.json`'dan rol okur (bilinmeyen → `guest`); `_mem_user(user)` rol `guest` **değilse** kullanıcı slug'ı döner, `guest`/tanınmayan için `''` (hafıza YOK). `_build_pi_args()` → `mem_user` doluysa `profile.md` + `family.md` + kişisel `soul.md` **birlikte** yüklenir; boşsa **hiçbiri** yüklenmez — `family.md` **dahil**.
- **Bu son nokta bir bug DEĞİL:** guest'in ortak aile hafızasını (`family.md`) da görmemesi **doğru davranıştır** — misafir ailenin ortak hafızasını görmemeli, "misafir" olmanın anlamı zaten budur. Önceki taslak bunu "ya hep ya hiç" eksikliği sayıp aşağıdaki üç kademeli `memory_scope` (none|family|personal) önerisini getiriyordu; **bu öneri geri çekildi.** Mevcut rol-tabanlı kapı (adult/child → hafıza, guest → yok) zaten istenen şeyi yapıyor; Faz 2'de yeni bir kapsam kavramı **eklenmez** (bkz. §9 Faz 2).

### 3.2 `device.kind`'ın gerçek rolü — küçük ama gerçek

`device.kind = personal | shared` **silinmiyor**, ama kimliği o çözmüyor — **ses çözüyor** (§3.1). Cihaz sınıfının işe yaradığı iki gerçek yer:

1. **Tanınamama durumunda beklenen davranış.** Ses güven eşiğin altındaysa (ya da speaker-ID hiç eşleşme bulamadıysa) mevcut `guest` davranışı (hafıza yok) devam eder — cihaz sınıfı sonucu değiştirmez, sadece "neden tanınamadı" beklentisini yönetir: shared'da yabancı biri olması normaldir, personal'da olmaması şaşırtıcıdır (araştırılmalı).
2. **Hatırlatma/mesaj teslim hedefini seçmek** (§3.5) — kişinin en son görüldüğü cihaz.

Yazma tarafı da kimlikten geçer, cihazdan değil: `memory_add` kişisel not yazmayı `mem_user` boşsa (tanınmayan/guest) **reddeder** (`pi/extensions/family-memory/index.ts`). Aksi halde salonda söylenen bir cümle yanlış kişinin defterine düşer — okuma ihlalinden daha kalıcı bir hata. Cihaz personal ya da shared olması bunu değiştirmez; ses tanımadıkça yazma yok.

**Proaktif teslim (hatırlatma/mesaj), cihaz sınıfına göre ENGELLENMEZ** — hedefin en-son-görülen cihazına gider; sızıntıyı içerik-onay kapısı önler. Ayrıntı ve gerekçe: §3.5 aşağıda. (Eski Açık Soru #4 — kapandı, karar değişti.)

### 3.3 Güvenli varsayılan (abstain)

- **Kimlik varsayımı hiçbir cihazda YASAK.** "Salonda genelde Ayhan olur" gibi bir sezgi koda girmez — personal cihazda bile kimlik sesle doğrulanır (§3.2), cihaz kimliği tek başına yeterli sayılmaz.
- Tanıma **eşiğin altındaysa** → `guest` davranışı (hafıza yok). Şüphe hâlinde **çekimser kal**, tahmin etme.

### 3.4 Speaker-ID — zaten aktif; kalan iş yalnızca DOĞRULAMA

Önceki taslak burada speaker-ID'yi "şimdi yapılmayacak, ileride açılacak bir genişleme noktası" gibi sunuyordu. **Yanlıştı.** Altyapı zaten var VE ÇALIŞIYOR (§3.1). `worker/agent.py` bunu bugün tek-oda akışında zaten kullanıyor.

`resolve_identity` sözleşmesi (kavramsal — kod zaten bunu yapıyor, bu **yeni kod değil**, mevcut davranışın adı):

```
resolve_identity(device, audio_window) -> (user | None, confidence)

  # Birincil kaynak HER cihazda sestir — device.kind bu çözümü DEĞİŞTİRMEZ.
  (user, confidence) = speaker_id(audio_window)
  confidence >= THRESHOLD ? (user, confidence) : (None, confidence)
```

`device.kind`, bu çözümün sonucunu değil, tanınamama durumunda **beklentiyi** yönetir (§3.2): personal cihazda tanınamama şaşırtıcıdır, shared cihazda normaldir.

**Kalan gerçek iş — doğrulama, yeni mimari DEĞİL:** speaker-ID bugüne kadar yalnızca web'in tek-oda akışında kanıtlandı. Wyoming köprüsünden gelen Pi sesinde (farklı donanım/mikrofon, yeniden örnekleme) ve iOS/Mac'ten gelen seste de doğru kişiyi tanıdığından **emin olunmalı** — varsayım değil, test. Faz 1 (iOS) ve Faz 4 (Wyoming köprüsü) kabul kriterlerine eklenir (§9). Ayrıca **ön-tampon (pre-roll) zorunluluğu** var (§4.5): kısa komutlarda ("Candan, ışıkları aç") wake sonrası ses `SPEAKER_MIN_SECONDS` eşiğini (bugün `1.0` sn) bulamayabilir; istemci wake kelimesinin kendi sesini de göndermezse kimlik sessizce çözülemez.

### 3.5 Teslim modeli: en-son-görülen cihaz + sözlü onay kapısı (KARAR)

Hatırlatma (`worker/reminders.py` → `Deliverer`) ve mesaj (§7.1) teslimi için **cihaz sınıfı (personal/shared) teslimi ENGELLEMEZ.** Önceki taslağın kuralı ("kişisel içerik yalnızca personal cihaza") bu modelle DEĞİŞTİRİLDİ. Gerekçe aynı — sızıntıyı önlemek — ama mekanizma değişti: sızıntıyı artık cihaz sınıfı değil, **içeriğin onaya kadar tutulması** önlüyor.

**Akış:**
1. Hedef kişinin **en son giriş yaptığı (last-seen) cihazı** belirlenir (§2.1).
2. O cihazdan **İÇERİKSİZ ANONS** çalınır: *"Ayhan, bir hatırlatmam var"* / *"Ayhan, sana bir mesajın var"*. Randevu, gönderen, konu — **hiçbiri** anonsta geçmez.
3. Kullanıcı kabul ederse ("söyle"/"tamam") → içerik o an okunur. Ertelerse ("şimdi olmaz") → `Deliverer`'ın mevcut `wait_reply()` davranışıyla bekletilir, tekrar dener.
4. Yanıt yoksa → mevcut `busy()`/kuyruk mantığı aynen çalışır.

**KRİTİK KURAL (tasarımın çekirdeği):** Anons **İÇERİK TAŞIMAZ.** "Ayhan, doktor randevun var" gibi içerik sızdıran bir anons **YASAK.** İçerik ancak onaydan **SONRA** okunur. Bu, paylaşımlı hoparlörde misafir varken bile hiçbir içeriğin sızmamasını sağlar — kim dinliyor olursa olsun anons boş kalır.

`worker/reminders.py` → `Deliverer`'ın bugünkü `present()`/`busy()`/`wait_reply()` üçlüsü bu akışın **zaten karşılığı**; yeni motor yazılmaz, anons/onay adımı bunun üstüne ince bir katman olarak eklenir.

**Kalan risk — büyük ölçüde ZATEN KAPALI, tam kapalı değil:** Naif bakışta "shared cihaza düşerse biri 'evet, oku' der, içerik sızar" gibi görünüyor — ama **speaker-ID zaten aktif** (§3.1, §3.4): "evet, oku" diyen kişi hedef değilse ses eşleşmez, içerik açılmaz. Gerçek kalan boşluk daha ince: onay ifadesi çoğu zaman tek kelime/çok kısa ("evet", "oku") ve `SPEAKER_MIN_SECONDS` (bugün `1.0` sn, §4.5) eşiğini bulamayabilir — bu durumda konuşanın kimliği hiç doğrulanamaz. Bu gerçek bir açık soru (Açık Soru #16): kimlik doğrulanamazsa içerik yine de okunsun mu, yoksa ek bir onay turu mu istensin?

---

## 4. Wake mimarisi

### 4.1 Sözleşme: istemci SET eder, agent TÜKETİR

```
İstemci (cihazda wake çözüldü)
   ├─ participant attribute:  mate.awake = "true" | "false"     (durum, kalıcı)
   └─ RPC:                    mate.set_awake { awake: bool }    (kenar, ACK'li)

Agent (worker)
   └─ bu iki kaynağı dinler → oturum uyanıklık durumunu günceller
```

İkisi birden neden var: attribute **durum**tur (geç katılan taraf da görür, reconnect'te hayatta kalır), RPC **kenar**dır (teslim onayı verir, kaybolmaz). Heavy'de ikisi de var; ikisi de taşınır.

**Ön koşul:** token'da `canUpdateOwnMetadata` — yoksa attribute yazımı **sessizce hiçbir şey yapmaz.** (Bkz. §2.2.)

### 4.2 Ters çevirme — bugünkü kod nerede

`worker/agent.py` → `_apply_wake_state()` şu an `ctx.room.local_participant.set_attributes({"candan.awake": ...})` ile durumu **yayınlıyor**; `pi_brain.WakeGate` durumu **üretiyor**. Web tarafı (`web/components/app/debug-status.tsx`, `agent-chat-transcript.tsx`) bunu **tüketiyor** (çan + uyku-transkript gizleme).

Hedefte agent, **çözülmüş** (resolved) uyanıklık durumunu tutar; kaynaklar:

| Kaynak | Hangi cihaz | Notu |
|---|---|---|
| `mate.awake` attribute / `mate.set_awake` RPC | iOS, Mac, Wyoming köprüsü (Pi adına) | **Birincil.** Cihazda çözülmüş wake. |
| dahili `WakeGate` + `wake_stt.py` | **yalnızca web** | İstisna. Cihaz-içi wake yok. |
| uyku zamanlayıcısı (`WAKE_WINDOW_SECONDS`) | hepsi | Kim uyandırırsa uyandırsın, sessizlikten sonra uykuya **agent** düşürür. Uykuyu istemciye bırakmak N istemcide N kere yeniden yazmak demek. |

Agent, çözülmüş durumu **yine yayınlar** (`mate.awake`, agent participant üzerinde) — çünkü UI'ın (çan, transkript gizleme, "dinliyorum" göstergesi) durumu bilmesi gerekiyor ve bu durum artık birden fazla kaynağın birleşimi. **Yön tersine döndü; ayna kaldı.** Karışmaz: istemcinin yazdığı kendi participant'ının attribute'u, agent'ınki agent participant'ının.

### 4.3 `worker/wake_stt.py`'ın daralan rolü

Bugün: her odada opsiyonel paralel erken-wake dinleyici (`WAKE_STT_ENABLED`).
Yarın: **yalnızca `capabilities`'inde `wake_onboard` OLMAYAN cihazlar için** (pratikte: web). Cihaz sınıfına bakıp koşullu kurulur. Cihaz-içi wake'i olan bir cihazda çalışması saf GPU israfı (bilinen bekleyen konu: büyüyen pencere) — ve bu daralma o israfı kendiliğinden çözer. **Teyit: Pi, iOS ve Mac bu yolu HİÇ kullanmaz** — üçü de cihaz-içi wake ile geliyor (§0.2); `wake_stt.py` yalnızca tarayıcı istisnası içindir.

`pi_brain.WakeGate` **silinmez**: uyku zamanlayıcısı + `wake_touch`/`proactive_wake` mantığı orada ve hepsi cihazdan bağımsız. Sadece "wake word'ü metinde arama" dalı web'e özgü hale gelir.

### 4.4 Dürüst uyarı: iOS'ta "her zaman dinleyen" mod YOK

`Info.plist`'te `UIBackgroundModes: [audio]` var ama bu **çalan/yayınlayan** ses içindir. iOS'ta uygulama arka planda süresiz mikrofon dinleyemez. Yani telefon, oda hoparlörünün yerini **tutmaz**:

- Uygulama önplanda / aktif oturumdayken → cihaz-içi wake çalışır.
- Arka planda → pratikte push-to-talk veya "uygulamayı aç, konuş".
- Ev içinde "sürekli dinleyen" rolü **Pi'lerindir.** Telefon: dışarıda ve elde.

Bunu planda yazıyoruz ki ürün beklentisi buna göre kurulsun.

### 4.5 Ön-tampon (pre-roll) ZORUNLU — istemci sözleşmesinin parçası

Wake **kimlikten bağımsızdır** (§0.2, §3.2): cihaz kimseyi tanımaz, ses tonu ayırt etmez — **herhangi bir ses** "candan" dediğinde uyandırabilir. Cihazın tek işi "biri wake kelimesini söyledi" demektir; GPU'yu boşuna yormamak asıl amaç (5 hoparlör sürekli ses akıtmasın, sunucu yalnızca uyanıştan sonra çalışsın). Kimlik SUNUCUDA, uyanıştan sonraki **ilk konuşmadan** speaker-ID ile çözülür (§3.1, §3.2).

Bu ayrım bir tuzak açar: `worker/agent.py` → `SPEAKER_MIN_SECONDS` (bugün `1.0` sn) speaker-ID'nin kimlik çözmek için istediği asgari ses uzunluğu. "Candan, ışıkları aç" gibi kısa bir cümlede **wake kelimesinden SONRAKİ** kısım 1 saniyeyi bulmayabilir → kimlik çözülemez → kullanıcı **sessizce** guest'e düşer (hafıza açılmaz, sebebi de görünmez — tam olarak §11'deki "sessiz başarısızlık" sınıfı).

**Zorunlu tasarım maddesi:** cihaz, sunucuya ses akıtmaya başlarken **wake kelimesinin kendi sesini de** göndermeli — geriye dönük bir **ön-tampon (pre-roll)** ile. Önerilen varsayılan: **wake tespitinden ~1–1.5 sn öncesi.** Bu, hem Wyoming köprüsü (Pi adına) hem iOS/Mac istemcileri için **zorunlu** madde; web zaten sunucu-taraflı `wake_stt.py` ile sürekli dinlediği için pre-roll'u **bedava** sağlar (bu yol zaten baştan beri ses görüyor — istisna burada da istisna kalıyor, §0.2).

**Kabul kriteri (ilgili fazlara eklenir, §9):** kısa bir komutla ("Candan, ışıkları aç") test edildiğinde speaker-ID kimliği **çözebiliyor** — pre-roll'suz bir test bu fazı geçmiş sayılmaz.

---

## 5. Wyoming ↔ LiveKit köprüsü

Pi aptal kalır. Köprü sunucuda çalışır ve **her Pi için bir LiveKit katılımcısıdır.**

### 5.1 Nerede çalışır

Ayrı bir süreç: `bridge/wyoming_bridge.py` (worker'dan bağımsız; worker'ın yaşam döngüsü LiveKit job'ına bağlı, köprününki cihaza bağlı — birbirine karıştırılmamalı). Ev sunucusunda systemd servisi. Cihaz kaydını okur, `transport: "wyoming"` olan her cihaz için bir "kanal" açar.

### 5.2 Rol dağılımı

```
  Pi (wyoming-satellite)                  Köprü (sunucu)                    LiveKit
  ─────────────────────                   ──────────────                    ───────
  mic ──► openWakeWord                                                     
          (CİHAZDA)                                                        
            │ detection                                                    
            ▼                                                              
       audio-start ──────Wyoming TCP────► oda'ya KATIL (token servisi)     
       audio-chunk* ────────────────────► PCM → rtc.AudioFrame ──────────► mic track
       audio-stop  ────────────────────► mate.awake = true (attr + RPC)   
                                                                           
       snd ◄──────────────────────────── agent audio track (subscribe) ◄── agent TTS
       (audio-start/chunk/stop)           PCM → yeniden örnekleme          
                                                                           
       (isteğe bağlı) played  ◄────────── mate.cue (çan) ◄──────────────── agent
```

**Wake sonrası akış:** Pi wake'i kendi çözer. Köprü, wake sinyalini gördüğü an odaya katılır (ya da zaten katılıysa `awake=true` set eder) ve sesi akıtmaya başlar. **Sunucu wake öncesi ses görmez** — kilitli karar #2 böyle sağlanır. **Pre-roll ZORUNLU** (§4.5): Pi/wyoming-satellite tarafı, `audio-start` ile birlikte wake tespitinden **~1–1.5 sn öncesinin** de tamponlanıp gönderilmesini sağlar — yoksa köprünün ilk gördüğü ses "ışıkları aç" gibi kısa bir komutta `SPEAKER_MIN_SECONDS` eşiğini bulamaz ve kimlik sessizce çözülemez.

**Oda/katılımcı yaşam döngüsü:**
- Oda adı: `dev-<piDeviceId>` — Pi başına, kalıcı.
- Köprü katılımcı kimliği: `pi-<deviceId>`. Odada **tek** katılımcı; Pi'nin kendisi LiveKit görmez.
- Sessizlik sonrası (`WAKE_WINDOW_SECONDS` + pay) köprü `awake=false` yapar; odadan **hemen çıkmaz** (yeniden bağlanma maliyeti). Uzun boşta (örn. 10 dk) odadan ayrılır → worker job kapanır → warm pi süreci serbest kalır. Bu, N hoparlörün N warm pi süreci tutmasını engeller (bkz. Riskler).

### 5.3 Ses formatı

| Yön | Kaynak | Hedef | İş |
|---|---|---|---|
| Pi → LiveKit | ReSpeaker/wm8960: 16 kHz, mono, s16le | LiveKit `rtc.AudioFrame` | Format zaten uyumlu. Sadece çerçeveleme (10/20 ms). Yeniden örnekleme **yok**. |
| LiveKit → Pi | Agent TTS (OmniVoice, `omnivoice_tts.py` → `DEFAULT_SAMPLE_RATE`, f32le→s16le çevrimi zaten var) | Wyoming `audio-chunk` @ Pi'nin oynatma hızı | Yeniden örnekleme **gerekli** (24k → 16k veya kartın desteklediği hız). |

Wyoming event çerçeveleme bilgisi zaten repoda: `worker/whisper_stt.py` (`transcribe → audio-start → audio-chunk* → audio-stop`, `{rate,width,channels}`). Köprü aynı kütüphaneyi/protokolü **ters yönde** kullanır. Sıfırdan protokol yazılmıyor.

### 5.4 Hata / yeniden bağlanma

- Pi bağlantısı düşer → köprü odadan çıkar, `awake=false`. Pi geri gelince yeniden katılır. **Agent'ın haberi olmaz** (odada katılımcı yok = kullanıcı yok; `reminders.py` → `_LiveKitIO.present()` zaten `remote_participants`'a bakıyor → proaktif seslenme kendiliğinden susar. Bu davranış **doğru ve bedava**).
- LiveKit düşer → köprü Pi'ye hata sesi/`played` göndermez, sessizce yeniden dener (üstel geri çekilme). Kullanıcıya "bağlantı yok" cue'su verilebilir (`mate.cue`).
- Köprü çöker → systemd restart; Pi tarafında tek etkisi: cevap gelmez. **Pi hiçbir durum tutmaz** (aptal kalır — kilitli karar #4'ün asıl faydası budur).

### 5.5 Donanım

`candan assistant/docs/SATELLITE_BRINGUP_2026-06-13.md` hâlâ geçerli: ReSpeaker 2-Mics HAT + Pi Zero 2W, `dtoverlay=wm8960-soundcard`, venv çakışmaları. Yeni araştırma gerekmez.

---

## 6. `mate.*` protokol ekosistemi — al / at

Swift istemcilerinin yarısı bu kanallara bağlı. Lite'ta **hiçbiri yok**. Hepsini taşımak lite'ı heavy'ye çevirir; hiçbirini taşımamak istemcileri çöpe atar. Öneri:

### AL (Faz 3–5)

| Kanal | Tip | Neden |
|---|---|---|
| `mate.awake` | attribute | Wake sözleşmesinin ta kendisi. Yönü ters (§4). **Zorunlu.** |
| `mate.set_awake` | RPC | Wake'in ACK'li kenarı. **Zorunlu.** |
| `mate.hello` | RPC | Cihaz el sıkışması: `{device_id, kind, capabilities, version}`. Agent'ın "kiminle konuşuyorum" bilgisinin **tek kaynağı**. Hafıza kapısı buna dayanır. **Zorunlu.** |
| `mate.cue` | data | Çan/uyanma sesi. Bugün web'de `playChime` (Web Audio) — cihaz-taraflı. Pi ve iOS'ta `Sounds/*.wav` var. Sunucudan tetiklenmesi = tek davranış, N istemcide tekrar yok. **Al.** |
| `mate.speaker` | data | Kim konuşuyor. Speaker-ID **zaten aktif** (§3.4) — bu kanal onun UI karşılığı, yeni bir tanıma mekanizması değil. Yeni transport'larda (Wyoming/iOS) doğrulanana kadar boş/`unknown` da yayınlanabilir. **Al.** |
| `mate.reminder` | data | `worker/reminders.py` zaten proaktif teslim yapıyor — ama **sessiz**: UI hiçbir şey görmüyor. Bu kanal onu görünür + onaylanabilir kılar. **Al.** |
| `mate.debug` | data | Geliştirme sırasında kör uçmamak için. Lite'ta `debug-status.tsx` zaten var, kaynağı yok. **Al (ucuz).** |
| `mate.say` | RPC | **YENİ** (§7.1). Sunucu → hedef odadaki agent: "şunu söyle". `announce` alanı uzaktan mesajlarda **her zaman true**. Faz 6. |
| `mate.message` | data | **YENİ** (§7.1). Mesajın metni + gönderen, görsel istemcilerde balon. Faz 6. |

### ERTELE

| Kanal | Neden |
|---|---|
| `mate.approval` / `mate.approval.resolve` | pi araç (tool) onayı. Lite'ta pi tool çalıştırıyor (`memory_add`, `web_search`, `reminder_*`) ama onay akışı yok. **Değerli, ama sesli akışta "onaylıyor musun" UX'i ayrı bir tasarım.** Faz 5+. |
| `mate.content` | Zengin UI kartları. Sesli asistanın çekirdeği değil; mac/iOS UI'sını canlandırır. İstemci birleştirmeden sonra. |
| `mate.session` | Oturum tarayıcısı — **Hermes gateway WS'ine bağlı** (§6.1). Lite'ta gateway yok. Ertelenir. |

### AT

| Kanal | Neden |
|---|---|
| `mate.update` / `mate.update.resolve` | Uygulama güncelleme akışı. Ev içi 5 cihaz için gereksiz karmaşa. |
| `mate.capture_frame` | Kamera/görüntü. Kapsam dışı. |
| `mate.barge_in`, `mate.interrupt` | **livekit-agents `AgentSession` barge-in'i zaten yapıyor** (VAD + turn detection). Elle kanal = frameworkün işini ikinci kez yapmak. |
| `mate.role`, `mate.device`, `mate.client`, `mate.livekit`, `mate.mac`, `mate.ios` | İsim-alanı önekleri/sabitler, protokol değil. |

### 6.1 Hermes gateway WS — taşınmıyor

mac'in ikinci kanalı (`ws://host:8800/api/ws`, JSON-RPC): oturum geçmişi, onay, config, cron. Lite'ta karşılığı yok.
**Karar: gateway taşınmaz.** İhtiyaç duyulan parçalar Next.js API route'ları olarak gelir (`/api/devices`, `/api/sessions`, `/api/reminders`). Gerekçe: ayrı bir WS gateway süreci = ayrı bir dağıtım birimi + ayrı auth + ayrı hata modu; lite'ın tüm anlamı bunu **olmamak**. mac'in gateway'e bağlı ekranları (session browser) gateway'siz sürümde **devre dışı** başlar.

### 6.2 İsim alanı: `mate.*` (KARAR)

Lite bugün `candan.awake` yayınlıyor; Swift istemciler `mate.*` bekliyor.
**Karar: `mate.*`.** Gerekçe: iki olgun istemci onu konuşuyor, tek web bileşeni (`debug-status.tsx`, `agent-chat-transcript.tsx`) `candan.awake` okuyor — değişecek taraf ucuz olan (web). Geçiş sırasında worker **ikisini de** yayınlayabilir (bir faz boyunca), sonra `candan.*` düşer. (Eski Açık Soru #1 — kapandı.)

---

## 7. Cihazlar arası mesaj & intercom

İstenen: *"Neva'ya şu mesajı ilet"* → asistan, kızının **odasındaki hoparlörden** konuşur.

Bunlar **iki ayrı yetenektir.** Karıştırmak, ucuz olanı pahalı olanın altında ezer:

| | (A) Asenkron mesaj | (B) Canlı intercom |
|---|---|---|
| Ne | Metin taşınır, hedefte **TTS** konuşur | Ham **ses** taşınır, canlı |
| Maliyet | Ucuz. Bir tool + bir kuyruk. | Pahalı. İki odanın geçici birleşmesi. |
| Ne zaman | **Faz 6** | **Faz 8 (opsiyonel)** |
| Teslim | Kuyruklu, garantili | Anlık ya da hiç |

### 7.0 Gizlilik çekirdeği (sonradan yama DEĞİL — tasarımın temeli)

Bu üç kural, yeteneğin **tanımının parçası**. İhlal eden bir uygulama yanlıştır, "eksik" değil.

1. **Intercom / mesaj DAİMA DUYULUR, anons İÇERİKSİZDİR.** Hedef cihaz, içeriği çalmadan önce **işaret sesi + içeriksiz anons** verir: çan → *"Baban seninle konuşmak istiyor"* (intercom) / *"Baban'dan bir mesajın var"* (mesaj) — içerik burada **geçmez** (§3.5). İçerik ancak hedef kabul edince açılır/okunur. **Sessizce mikrofon açan hiçbir yol olmayacak.** Uzaktan **konuşturulabilen** cihaz, uzaktan **dinlenebilen** cihaza bir adım uzaktır (bkz. Riskler). Bu yüzden protokolde "sessiz teslim", "gizli mod", "sadece kaydet" gibi bir bayrak **bulunmayacak** — var olmayan bayrak istismar edilemez.
2. **Mesaj teslimi hedefin en-son-görülen cihazına gider (personal ya da shared) — cihaz sınıfı teslimi ENGELLEMEZ (§3.5, KARAR).** Önceki kural (yalnız personal cihaza teslim, shared'dan hiç anons yok) bu modelle **DEĞİŞTİRİLDİ** — gerekçe aynı (§3'teki kişisel-hafıza sızıntısını önlemek), mekanizma farklı: cihaz sınıfı değil, **içerik-onay kapısı**. Anons hiçbir zaman içerik taşımaz; içerik ancak hedef sözlü onay verdikten sonra okunur — ve onayı veren kişi zaten aktif olan speaker-ID ile doğrulanır (§3.1, §3.4).
3. **Yetki kuralı (basit tutulacak):** `policy.json`'daki roller kullanılır — `adult` **herkese**, `child` yalnızca `adult`'lara ve kendisine mesaj gönderebilir. `guest`/tanınmayan **hiç kimseye** gönderemez (tanınmayan biri evdeki hoparlörleri konuşturamaz — apaçık istismar yolu). Karmaşık ACL yok; ev 5 kişilik. (Açık Soru #11.)

### 7.1 (A) Asenkron mesaj iletme — Faz 6

**Agent tool'u** (pi `family-memory` extension'ının yanına, `pi/extensions/`):

```
mesaj_birak(hedef: string, metin: string) -> { status, message_id, teslim: "hemen"|"kuyrukta", sebep? }

  status: "iletildi"  → hedef cihazda ŞU AN çalındı
          "kuyrukta"  → hedef yok/uygun değil, döndüğünde çalınacak
          "reddedildi"→ yetki yok / hedef bilinmiyor  ← agent bunu KULLANICIYA SÖYLER
```

Agent, tool'un dönüşünü kullanıcıya **açıkça** aktarır: *"Neva'nın odasından ilettim"* / *"Neva şu an odasında değil, dönünce ileteceğim."* **Boşluğa konuşma yok, sessiz kayıp yok.**

**Hedef çözümü** (device registry + last_seen, §2.1, §3.5):
```
hedef_kişi → last_seen(hedef_kişi)   # en son giriş yapılan/tanınan cihaz, personal YA DA shared
  → cihaz aktif (odada katılımcı var) : hedef bu — içeriksiz anons + onay kapısı (§3.5)
  → cihaz aktif değil                 : KUYRUKLA, cihaz bir sonraki mate.hello'sunda tetiklenir
  → last_seen hiç yok                 : KUYRUKLA (kişi hiç görülmemiş)
```
\* Aynı anda birden fazla cihazda aktifse en son `seen_at` olanı tercih edilir; ilk oynatan mesajı `delivered` işaretler, diğerleri kuyruktan idempotent şekilde düşer (tek `message_id`).

**Başka odaya konuşma enjekte etme — netleştirilmiş:**

Cihaz-başına-oda modelinde hedef **başka bir odadadır**. İki durum:

```
1) Hedef odada AGENT ZATEN VAR (cihaz bağlı/aktif)
   → RPC ile agent'a söyle:  performRpc(agent, "mate.say", { text, announce: true, from })
   → agent hedef odada session.say(...) ile konuşur. Yeni dispatch YOK.
   → HIZLI YOL. Tercih edilen.

2) Hedef odada AGENT YOK (cihaz boşta / köprü odadan çıkmış / telefon kapalı)
   → İki alt-durum:
     2a) Cihaz ULAŞILABİLİR ama odada değil (Pi açık, köprü boşta çıkmış):
         köprü uyandırılır → odaya katılır → createDispatch("candan") → agent gelir →
         mate.say. (Token servisi zaten dispatch'i zorluyor — §2.2. Yeni mekanizma YOK.)
     2b) Cihaz ULAŞILAMAZ (Pi kapalı, telefon offline):
         KUYRUĞA yaz. Teslim, cihaz bir sonraki `mate.hello`'sunda tetiklenir.
```

**Boşta bir oda için sırf mesaj yüzünden agent dispatch etmek pahalı mı?** Evet (warm pi süreci). Ama mesaj **nadir** bir olay. Alternatif (köprünün mesajı kendi TTS'iyle çalması) TTS yolunu ikinci kez yazmak demek — reddedildi. **Tek TTS yolu vardır: agent.**

**Kuyruk = mevcut altyapı.** `memory/events.db` + `worker/reminders.py` → `EventStore`/`Deliverer` zaten *"vakti gelince, kişi oradayken, meşgul değilken seslen"* işini yapıyor: `present()` (odada katılımcı var mı), `busy()`, `wait_reply()`. **Mesaj = hatırlatmanın kardeşidir.** Yeni bir kuyruk motoru yazılmaz; `events.db`'ye yeni bir olay tipi eklenir:

```
event: { type: "message", from: "ayhan", to: "neva", text: "...",
         created_at, delivered_at: null, status: "pending" }
```

Teslim kuralları hatırlatmayla **aynı**: en-son-görülen cihaz + içerik-onay kapısı (§3.5, Kural #2 — güncel hali).

**Teslim bildirimi kaynağa geri döner.** Mesaj çalındığında `delivered_at` yazılır ve **kaynak kişiye** bir olay kuyruklanır: *"Neva'ya mesajın iletildi."* Kaynak o an odada değilse bu da kuyruğa girer — aynı motor, ekstra kod yok.

**Protokol (§6'ya eklenir):**

| Kanal | Tip | Ne |
|---|---|---|
| `mate.say` | RPC (server→agent) | `{ text, announce: bool, from }` — hedef odadaki agent'a "şunu söyle". `announce: true` (varsayılan, Kural #1) → agent önce **içeriksiz** anons yapar (çan + "X'ten bir mesajın var" — `text` bu adımda SÖYLENMEZ, §3.5); hedef kabul edince `text` okunur. `false` yalnızca agent'ın kendi konuşması için. |
| `mate.message` | data | UI'a mesajın kendisi (görsel istemcilerde balon). Ses zaten çalıyor; bu ek. |

### 7.2 (B) Canlı intercom — Faz 8, opsiyonel

Kaynak mikrofon → hedef hoparlör, **canlı ses**. Model: **"varsayılan izole, talep üzerine birleşen oda."**

Cihaz-başına-oda modelini **bozmadan** iki seçenek:

```
Seçenek 1 — KÖPRÜ KATILIMCI (önerilen)
  Sunucuda kısa ömürlü bir "intercom" süreci:
    · Kaynak odaya katılır  → kaynağın mic track'ine SUBSCRIBE olur
    · Hedef odaya katılır   → o track'i hedef odaya PUBLISH eder (yeniden yayın)
    · (çift yön istenirse aynısını ters yönde)
  + Oda modeli DEĞİŞMEZ. İzolasyon varsayılan kalır.
  + Kim neyi duyuyor: köprünün abonelikleri = TEK denetim noktası. Denetlenebilir.
  − Ses sunucudan iki kez geçer (decode/encode). Ev içi LAN'da sorun değil.

Seçenek 2 — ORTAK GEÇİCİ ODA
  İki cihaz da geçici `intercom-<uuid>` odasına ek bağlantı açar.
  − İstemcilerin ikinci bir odaya katılmayı bilmesi gerekir (iOS/Mac/Pi köprüsü — üç yerde iş).
  − Pi "aptal" kalamaz: köprünün iki odayı yönetmesi gerekir → zaten Seçenek 1'e döner.
  → REDDEDİLDİ.
```

**Yaşam döngüsü (Seçenek 1):**
```
1. Kaynak: "Neva'yla konuş" → agent tool: intercom_ac(hedef)
2. Yetki kontrolü + hedef **personal** cihazda mı? → değilse RED. (Not: §3.5'teki en-son-görülen-cihaz + onay-kapısı modeli yalnızca **asenkron** hatırlatma/mesaj içindir — canlı intercom, geri döndürülemez canlı ses taşıdığı için burada **daraltılmamıştır**; hedef personal cihazda değilse açılmaz.)
3. HEDEF CİHAZDA ÖNCE ANONS: çan + "Baban seninle konuşmak istiyor."   ← Kural #1, ATLANAMAZ
4. Köprü katılımcı iki odaya girer, track forward başlar. Hedefte GÖRÜNÜR gösterge
   (LED/UI/periyodik hafif ton — "hat açık" sinyali).
5. Kapanış: her iki taraf da "kapat" diyebilir; ayrıca SERT ÜST SINIR (örn. 2 dk) — süre
   dolunca hat kendiliğinden kapanır. Sonsuz açık hat = dinleme cihazı.
6. Kapanınca hedefte kapanış tonu (açılış kadar duyulur).
```

**Agent'ın rolü:** hattı **kurar**, sonra **çekilir** (kendi TTS'i araya girmez). Hat kapanınca geri gelir.

**Tek yön mü, çift yön mü?** Öneri: **çift yön** (intercom), ama hedef **kabul etmeden** kaynağın hedefi duyması **başlamaz** — yani hedefin mikrofonu, hedef "tamam/efendim" diyene kadar forward edilmez. Anons → sessizlik → hedef cevap verirse hat çift yönlü açılır. Cevap vermezse: kaynak bir şey duymaz, hat kapanır. (Açık Soru #13.)

## 8. İstemci birleştirme

`mate-mac/` ve `Mate-IOS/` **ikisi de `livekit-examples/agent-starter-swift` fork'u.** Dosya iskeletleri neredeyse birebir; iOS, mac'in sade alt kümesi + `LocalTTSSpeaker` (cihaz-içi TTS yedeği).

**Karar: tek kod tabanı.** Bir Xcode projesi, iki hedef (iOS + macOS), ortak `VoiceAgentCore` Swift paketi.

```
VoiceAgentCore/            ← platform-bağımsız (paket)
  Voice/WakeWordDetector.swift     ← LiveKit PCM tamponunu gözleyen SFSpeech wake dedektörü
  Voice/WakeCoordinator.swift      ← uyku/uyanık durum makinesi
  Voice/AdaptiveVAD.swift
  Voice/CueSounds.swift + Sounds/*.wav
  Net/TokenSource.swift            ← eski HermesTokenSource, hedefi lite /api/token
  Net/PairingClient.swift
  Helpers/DeviceIdentity.swift     ← kararlı cihaz kimliği
  SettingsStore.swift
  Protocol/Mate.swift              ← mate.* RPC/topic tipleri (§6) — TEK yerde
App-iOS/                   ← ince kabuk
App-macOS/                 ← ince kabuk + #if os(macOS): AudioDeviceSelector/Store, debug monitörü
```

**Bundle ID / domain (KARAR):** Uygulama bundle id `uk.drascom.mate` olur (sunucu domaini zaten `mate.drascom.uk` — kullanımda). Xcode projelerindeki eski `com.livekit.example.*` template bundle id'si bu fazda **hem iOS hem macOS target'ında** değiştirilir. Not: mevcut bir App Store/TestFlight kaydı varsa bundle id değişikliği yeniden imzalama + yeniden gönderim gerektirir — bu faz sırasında kontrol edilmeli.

**`WakeWordDetector.swift` KORUNUR — dokunulmaz.** LiveKit'in PCM tamponunu gözler; **ayrı `AVAudioEngine` AÇMAZ.** Bu bilinçli: CoreAudio iki-motor çakışması `StartIO error 35` veriyor. Bu bilgi pahalıya öğrenildi; kodda ve burada yazılı kalsın. "Temizlerken" ayrı engine'e geçirmek klasik regresyon.

`LocalTTSSpeaker` (iOS): sunucu TTS'i düştüğünde cihaz-içi yedek. Ortak pakete alınır, macOS'ta da işe yarar.

---

## 9. Fazlandırılmış yol haritası

Her faz **bağımsız doğrulanabilir bir kazanım.** Yarım kalırsa sistem çalışır durumda kalır.

### Faz 1 — "Telefondan konuş" (uçtan uca ince dilim)
**Yap:** iOS istemcisini lite'a bağla. `TokenSource` → lite `/api/token`. `Secrets.swift` sil. Token servisine `device_id` gövdesi + `canUpdateOwnMetadata` ekle. Oda adı `dev-<deviceId>`. Dispatch zorlanır (`ensureAgentDispatch` + `dispatchInFlight` kilidi **korunur**). iOS wake dedektörü **ön-tampon (pre-roll) gönderir** (§4.5) — ZORUNLU, yoksa kısa komutlarda kimlik çözülmez.
**Doğrula:** iPhone'dan "candan" de, cevap gel. LiveKit oda görünümünde **tam 1** agent katılımcısı. Web istemcisi **bozulmadı** (ikisi ayrı odada, aynı anda çalışsın). Kısa bir komutta ("Candan, ışıkları aç") speaker-ID kimliği **çözebiliyor** (pre-roll sayesinde `SPEAKER_MIN_SECONDS` eşiği doluyor, §3.4).
**Risk:** Dispatch sessiz düşerse "hata yok ama agent yok" → agent sayısını **açıkça** kontrol et, güvenme.

### Faz 2 — Cihaz kaydı + kimlik→hafıza kapısı
**Yap:** devices kaydı + `/api/devices` + kişi bazlı `last_seen` izi (§2.1, §3.5). Hafıza kapısı tarafında **yeni kavram eklenmez** — mevcut `_mem_user`/rol mekanizması (§3.1) zaten doğru; yalnızca cihaz kaydına bağlanır (kimlik hâlâ sesten çözülür, cihazdan değil — §3.2). Pairing akışı (heavy'den taşı).
**Doğrula:** shared cihazda tanınmayan bir ses "benim doktor randevum ne zamandı" derse → **kişisel hafızaya erişmez** (mevcut guest davranışı, değişmedi), "kim olduğunu bilmiyorum" der. Aynı cihazda speaker-ID ile **tanınan** biri aynı soruyu sorarsa → cevaplar — çünkü kimliği açan cihaz değil sestir. `memory_add` tanınmayan/guest'te reddeder (zaten böyle, §3.2).
**Risk:** **Gizlilik.** Bu fazın testi negatif test (erişememeli). Pozitif testin geçmesi yeterli değil.

### Faz 3 — Wake yönünü ters çevir
**Yap:** `mate.awake` attribute + `mate.set_awake` RPC → agent tüketir. Agent çözülmüş durumu ayna olarak yayınlar. `wake_stt.py` yalnızca `wake_onboard` yeteneği olmayan cihazlarda kurulur (= web). Web bileşenleri `mate.awake` okur.
**Doğrula:** iOS'ta cihaz-içi wake → çan çalar, transkript görünür; sunucuda wake_stt **hiç çalışmaz** (log/GPU ile teyit). Web'de eski davranış aynen.
**Risk:** `canUpdateOwnMetadata` yoksa attribute yazımı **sessizce no-op**. Faz 1'de eklendi; burada **açıkça teyit et** (attribute'u okuyup gördüğünü logla).

### Faz 4 — Wyoming ↔ LiveKit köprüsü (1 Pi)
**Yap:** `bridge/wyoming_bridge.py`. Tek Pi ile uçtan uca. Ses formatı çevrimi, TTS geri yolu, boşta odadan çıkma. **Pre-roll ZORUNLU** (§4.5, §5.2): köprü, wake tespitinden ~1–1.5 sn öncesini de LiveKit'e akıtır.
**Doğrula:** Salon Pi'sine "candan" de → cevap **Pi'nin hoparlöründen** gelsin. Pi kablosunu çek → agent proaktif seslenmeyi kessin (`present()` false). Tak → geri gelsin. Kısa bir komutta ("candan, ışıkları aç") speaker-ID kimliği **çözebiliyor** (pre-roll doğrulaması, §3.4).
**Risk:** Ses formatı/gecikme. Köprü çökerse Pi sessizleşir ama **bozulmaz** (durumsuz).

### Faz 5 — `mate.*` protokolü (al listesi)
**Yap:** `mate.hello`, `mate.cue`, `mate.speaker` (boş), `mate.reminder`, `mate.debug`. `candan.*` düşer.
**Doğrula:** Çan sunucudan tetiklenir, üç istemcide de çalar. Hatırlatma teslimi UI'da görünür.
**Risk:** Düşük. Katkısal.

### Faz 6 — Asenkron mesaj iletme (§7.1)
**Yap:** `mesaj_birak(hedef, metin)` tool'u. Hedef çözümü **last_seen** üzerinden (§2.1, §3.5 — personal ya da shared, cihaz sınıfı ENGELLEMEZ). `mate.say` RPC (`announce: true` varsayılan, **içeriksiz** anons + onay kapısı, §3.5). Kuyruk = `events.db`'ye yeni olay tipi (`reminders.py` motoru **yeniden kullanılır**, yeni motor yazılmaz). Yetki kuralı (`policy.json` rolleri). Teslim bildirimi kaynağa geri döner.
**Doğrula:** (1) Hedef en-son-görüldüğü cihazdaysa: önce **çan + içeriksiz anons** ("X'ten bir mesajın var"), hedef kabul edince mesaj okunur, kaynağa "iletildi" denir. (2) Hedef hiçbir cihazda aktif değilse: mesaj **kuyrukta** bekler; hedef bir sonraki `mate.hello`'sunda teslim tetiklenir. (3) **Negatif test zorunlu:** anons metninde mesajın içeriği **hiçbir zaman** geçmemeli — shared cihazda bu özellikle test edilir. (4) Yetkisiz/tanınmayan biri mesaj gönderemez.
**Risk:** **İçerik-onay kapısının atlanması** — anonsun içerik taşıması (§3.5 ihlali) en tehlikeli regresyon; negatif test **zorunlu**. İkinci risk: hedef odada agent yoksa dispatch yolu (§7.1, durum 2a) — sessiz dispatch düşmesiyle mesaj **kaybolur**; teslim `delivered_at` yazılmadan **başarılı sayılmaz**.

### Faz 7 — İstemci birleştirme + pairing UI
**Yap:** `VoiceAgentCore` paketi, iki hedef. Bundle id `com.livekit.example.*` → `uk.drascom.mate` (iOS+macOS, §8). Web'de "Cihazlar" sayfası (kod onayla, kind/person ata).
**Doğrula:** Yeni bir telefon sıfır-konfigle eşleşsin (gömülü sır yok, elle URL yok).
**Risk:** Xcode proje birleştirme sıkıcı ama düşük riskli. `WakeWordDetector`'ın ayrı-engine'siz tasarımını **bozma**. Bundle id değişimi provisioning profili güncellemesi gerektirir.

### Faz 8 — Genişleme (kararı sonraya, opsiyonel)
- **Canlı intercom (§7.2):** köprü-katılımcı ile track forward. Zorunlu anons + görünür "hat açık" göstergesi + sert süre üst sınırı. **Faz 6 çalışmadan başlanmaz** (asenkron mesaj, intercom'un yetki + hedef-çözüm + gizlilik kurallarını zaten kanıtlar).
- Eşzamanlılık/kapasite (bkz. Riskler). `mate.approval`.

---

## 10. Açık sorular (kullanıcıya)

1. ~~İsim alanı: `mate.*` mi `candan.*` mı?~~ **KARARLANDI: `mate.*`.** (Bkz. §0.6, §6.2.)
2. **Oda ömrü:** cihaz başına **kalıcı** oda adı (`dev-<id>`, öneri) mi, oturum başına **geçici** oda mı? Geçici = her seferinde soğuk pi süreci.
3. **Aynı kişi, iki cihaz, aynı anda** (telefon + oda hoparlörü): tek pi oturumu mu paylaşsınlar (aynı `--session-id` → **iki warm süreç aynı session dosyasına yazar, çakışır**) yoksa ayrı oturum + ortak hafıza dosyaları mı? (Öneri: ayrı oturum, ortak hafıza. Süreklilik zaten hafızadan geliyor — kilitli karar #1.)
4. ~~Shared cihazda kişisel hatırlatma: salon hoparlörü "Ayhan, randevun var" desin mi?~~ **KARARLANDI:** teslim cihaz sınıfına göre engellenmez; hatırlatma/mesaj hedefin **en-son-görülen cihazına** gider, içerik yalnızca sözlü onaydan sonra okunur (anons hep içeriksiz). (Bkz. §3.5, §7.0.)
5. **Eşzamanlı kapasite:** kaç cihaz aynı anda konuşabilmeli? Tek RTX 3090'da Whisper + OmniVoice + (varsa) wake_stt paylaşılıyor. 5 hoparlör + 2 telefon **aynı anda** uyanırsa ne olur? (Kuyruk mu, red mi, düşük öncelik mi?)
6. **Pi akışı:** wake sonrası tam-dupleks (sürekli akış, barge-in var) mı, tek-tur (soru→cevap→kapat) mı? Tam-dupleks daha iyi ama Pi Zero 2W'de eşzamanlı capture+playback maliyetli.
7. **Bahçe hoparlörü:** shared mi, hiç mi? (Dışarıda konuşulan şey duvarların ötesine gider — ayrı bir gizlilik sınıfı gerekir mi?)
8. **Pairing onayı:** web'de "Cihazlar" sayfası + kod onayı (öneri) yeterli mi, yoksa CLI/elle dosya düzenleme mi?
9. **Cihaz kaydı depolama:** `devices.json` (basit, git'siz) mi, `memory/events.db` yanında SQLite mi?
10. **Mac istemcisi:** gateway'siz (session browser devre dışı) sürümle yaşamaya razı mıyız, yoksa mac şimdilik **kapsam dışı** mı? (Öneri: iOS + Pi + web önce; mac Faz 7'de döner.)

**Mesaj & intercom (§7):**

11. **Yetki modeli:** öneri = `adult` herkese, `child` yalnız `adult`'lara + kendine, `guest`/tanınmayan hiç kimseye. Yeterli mi? Çocuklar birbirine mesaj atabilsin mi?
12. **Cevabı geri getirme:** hedef, mesajı duyduktan sonra *"tamam, geliyorum"* derse bu **otomatik olarak** kaynağa dönsün mü? (Öneri: **evet, ama sadece mesaj çalındıktan hemen sonraki kısa pencerede** — sürekli dinleme değil, tek turluk cevap yakalama. Bu, "sessizce mikrofon açma" sınırına yakın; **duyulur** kalması şart: cevap yakalandığı sırada da gösterge açık olmalı.)
13. **Canlı intercom yönü:** hedef "kabul edene" kadar tek yön (kaynak→hedef), kabul edince çift yön (öneri) mi; yoksa doğrudan çift yön mü? Doğrudan çift yön = hedefin odasını **onayı olmadan** açmak.
14. **Süre üst sınırı:** canlı hat için sert kapanma süresi ne olsun (öneri: 2 dk)? Sonsuz açık hat kabul **edilemez**.
15. **Mesaj metni hafızaya yazılsın mı?** ("Ayhan Neva'ya şunu iletti") Kullanışlı ama ev içi konuşmaların kalıcı kaydı gizlilik açısından ayrı bir karar. (Öneri: **hayır**, mesaj teslim edilince olay kapanır; yalnız teslim durumu tutulur.)
16. **Kısa onay ifadeleriyle kimlik çözümü (§3.5):** İçerik-onay kapısında hedefin tek kelimelik "evet"/"oku" cevabı `SPEAKER_MIN_SECONDS` (bugün `1.0` sn, §4.5) eşiğini bulamayabilir → o an konuşanın kimliği speaker-ID ile doğrulanamayabilir. Bu durumda içerik yine de okunsun mu (mevcut oda-bazlı `present()` sinyaline güvenerek) yoksa ek bir onay turu mu istensin ("adını söyler misin" gibi)?

---

## 11. Riskler

### Sessiz başarısızlıklar (en tehlikeli sınıf — hata vermezler)
- **Dispatch düşmesi.** Token verilir, istemci odaya girer, **agent gelmez, hata yok.** Heavy'nin Hermes token'ında tam olarak bu var. Savunma: tek token servisi + `createDispatch` + **her fazda "odada 1 agent var mı" kontrolü.**
- **Dispatch yarışı.** Eşzamanlı iki token isteği → iki agent → **her cevap çift ses.** lite'ta `dispatchInFlight` kilidi ve `listDispatch` kontrolü bunun için var (`web/app/api/token/route.ts`). Yeni token yolunda **aynen korunmalı** — "temizlik" sırasında silinmeye açık bir kod.
- **`canUpdateOwnMetadata` eksikliği.** Attribute yazımı **no-op**. Wake hiç gelmez, log da yoktur. Bugünkü lite grant'ında bu bayrak **yok**; wake yönü tersine dönmeden fark edilmez.
- **`asyncio` zayıf task referansı.** `agent.py`'de `bg_tasks` seti tam bu yüzden var (GC task'ı bitmeden toplarsa attribute sessizce gitmez). Köprüde ve yeni yayınlarda aynı tuzak.

### Gizlilik
- **Yanlış kişi eşleşmesi = sessiz ihlal.** Kimse hata görmez; sadece Ayhan'ın notu yanlış kulağa gider. Savunma: shared'da **varsayılan çekimserlik**, `person` alanının shared'da şema düzeyinde yasak olması, yazma kapısı, negatif testler.
- **Proaktif teslim (hatırlatma + mesaj) artık cihaz sınıfına göre engellenmez — en-son-görülen cihaza gider (§3.5, KARAR; eski Açık Soru #4 ve §7.0 Kural #2'nin yerini aldı).** Önceki savunma ("yalnız personal cihaza teslim") düşürüldü; yeni savunma **içerik-onay kapısıdır**: anons hiçbir zaman içerik taşımaz, içerik yalnızca hedef sözlü onay verdikten sonra okunur — ve o onayı veren kişi zaten aktif olan speaker-ID ile doğrulanır (§3.1, §3.4). Kalan gerçek boşluk: çok kısa onay ifadeleri ("evet") speaker-ID'nin asgari ses eşiğini bulamayabilir (Açık Soru #16).

### Bilinçli tasarım tercihi — kimlikten bağımsız wake (risk değil, bedeli var)
Wake cihazda **kimlikten bağımsız** çözülüyor (§0.2, §3.2, §4.5): TV, radyo, misafir de "candan" deyip cihazı uyandırabilir. Bu bir gizlilik açığı **değildir** — sunucu ardından speaker-ID ile bakar, tanımazsa `guest` davranışı devreye girer ve hafıza açılmaz (§3.1). Yanlış uyanışın gerçek bedeli yalnızca **boşa giden bir sunucu turu** (STT/TTS/GPU) — kabul edilebilir bir maliyet, kilitli kararların (§0.2: wake cihazda, GPU'yu yormamak) doğal sonucu.

### İNTERCOM'UN ASIL TEHDİDİ — uzaktan konuşturulabilen cihaz, uzaktan dinlenebilen cihazdır
Ev hoparlörlerini uzaktan **konuşturabilen** bir yol açtığımız anda, o yolun bir adım ötesi **odayı uzaktan dinlemektir**. Bu, projedeki en ciddi ikinci gizlilik tehdidi (birincisi yanlış-kişi hafıza sızıntısı). Savunmalar tasarımın içinde, sonradan eklenen kontrol değil:

- **"Sessiz teslim" bayrağı PROTOKOLDE YOK.** `mate.say`'in `announce` alanı uzaktan gelen mesajlar için **her zaman `true`**. Var olmayan bayrak istismar edilemez; opsiyonel bayrak er ya da geç `false` olur.
- **Anons atlanamaz VE içeriksizdir.** Hedef cihaz, içerik açılmadan önce **çan + içeriksiz anons** olmadan hiçbir şey çalamaz (§3.5). Sessizce çalabilen bir cihaz, sessizce dinleyebilen bir cihazın yarısıdır.
- **Canlı hatta görünür/duyulur "açık" göstergesi + sert süre üst sınırı.** Sonsuz açık hat = kasıtsız kurulmuş bir böcek.
- **Hedefin mikrofonu, hedef kabul etmeden forward edilmez** (§7.2, Açık Soru #13).
- **Yetkisiz/tanınmayan kimse mesaj gönderemez** (§7.0 Kural #3). Aksi halde `guest` bir sesin evdeki hoparlörleri konuşturması mümkün olur.
- **Yeni kod incelemesi kuralı:** intercom yoluna "sessiz", "gizli", "arka planda", "bildirimsiz" bir seçenek eklemek isteyen her PR **reddedilir**. Bu satır, gelecekteki iyi niyetli bir kolaylaştırma için buradadır.

### Platform
- **iOS'ta "her zaman dinleyen" mod YOK.** `UIBackgroundModes:[audio]` bunu vermez. Telefon, oda hoparlörünün yerini tutmaz (§4.4).
- **CoreAudio iki-motor çakışması** (`StartIO error 35`): `WakeWordDetector` bilerek ayrı `AVAudioEngine` açmıyor. Refactor sırasında bozulmaya açık.

### Kapasite / kaynak
- **N oda = N warm pi süreci + N AgentSession.** 5 hoparlör + telefonlar sürekli odada kalırsa RAM/GPU tükenir. Savunma: köprünün boşta odadan çıkması (§5.2) + oda `emptyTimeout`.
- **Tek GPU, çok istemci.** STT/TTS paylaşımlı. Eşzamanlı uyanışta kuyruk davranışı tanımsız (Açık Soru #5).
- **Bekleyen bilinen konu:** `wake_stt` büyüyen pencere = GPU israfı. §4.3'teki daralma (yalnız web) bunu büyük ölçüde kendiliğinden çözer.

### Güvenlik
- Repo geçmişinde **canlı LiveKit JWT + gateway token** var (`Secrets.swift`, 720 saat). Silmek yetmez — **anahtarlar rotate edilmeli.**

---

## Ek: yanlış anlaşılan isim

`/Users/drascom/work/candan-lite/pi` **Raspberry Pi değildir** — `pi` CLI agent'ının konfigürasyonudur (persona'lar, extension'lar, skill'ler). Lite'ta bugün **hiç** Raspberry Pi kodu yoktur; Wyoming köprüsü (§5) ilk olacak. Yeni dizin adı: `bridge/`, `pi/` değil.
