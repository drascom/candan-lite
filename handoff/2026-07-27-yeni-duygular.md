# Yeni duygular + kombo yükseltmesi (27 Tem, akşam)

Görev: `handoff/task-2026-07-27-yeni-duygular.md`. Taban commit `3bc8443`.
Girdi: `handoff/2026-07-27-duygu-katmani.md` §2 (43 token ölçümü) +
`handoff/2026-07-27-kombo-olcumu.md` (14 koşul / 168 wav) + kullanıcının
kulaklıkla dinlediği atlas ve kombo setinin notları.

**Bu turda ÖLÇÜM YAPILMADI** — her iki ölçüm de daha önce bitmişti. Bu tur
yalnızca eşlemeyi, prompt'u, testleri ve deploy'u yaptı.

## 0. Geri alma (TEK BLOK)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/higgs_tts.py.bak-duygu2-20260727 worker/higgs_tts.py && \
  cp pi/AGENTS.md.bak-duygu2-20260727 pi/AGENTS.md && \
  cp pi/personas/candan.md.bak-duygu2-20260727 pi/personas/candan.md && \
  rm -f worker/data/tts-cache/*.pcm && systemctl restart candan-worker'
```
Yereldeki karşılığı: `git checkout HEAD~1 -- worker/higgs_tts.py pi/AGENTS.md pi/personas/candan.md`.

## 1. EKLENEN beş etiket

Hepsi 27 Tem ölçümünde **12/12 anlaşıldı, WER 0.000, boş çıktı/başı yeme yok**
ve kullanıcı atlasta hepsine "iyi" dedi.

| yeni etiket | → Higgs | kullanıcının notu |
|---|---|---|
| `[mood:amused]` | `<\|emotion:amusement\|>` | "sorunsuz" — şakalaşma, hafif alay |
| `[mood:thinking]` | `<\|emotion:contemplation\|>` | "tonlama ve duraklamalar gayet güzel" |
| `[mood:determined]` | `<\|emotion:determination\|>` | "iyi" — söz verme, kararlılık |
| `[mood:relieved]` | `<\|emotion:relief\|>` | "iyi" — "çözüldü, geçmiş olsun" |
| `[whisper]` | `<\|style:whispering\|>` | **"harika"** — bebek uyuyor, gece |

### Neden `[whisper]`, neden `[mood:whisper]` değil

İki gerekçe:

1. **Kategori farkı.** `MOOD_PRESETS`'teki her değer `<|emotion:…|>`; fısıltı ise
   `<|style:…|>` — duygu değil **biçem**. `[mood:]` altına sokmak eşlemenin
   anlamını bulandırırdı.
2. **Kod tabanındaki desen.** `[laughter]`, `[sigh]`, `[pause]`, `[long_pause]`
   zaten çıplak etiket ve `HIGGS_TAG_MAP`'te duruyor. `[whisper]` oraya
   sıfır makine değişikliğiyle oturdu: `_PREFIX_TOKEN_RE` `<|style:` önekini
   zaten cümle başına taşıyor.

⚠️ **Kapsam farkı bilinçli ve prompt'a yazıldı.** `[mood:X]` TUR boyu yaşar
(`_current_mood`, `reset_mood()` ile sıfırlanır); `[whisper]` `HIGGS_TAG_MAP`'te
olduğu için **cümle kapsamlıdır** — livekit cümle cümle sentezliyor, token
yalnız konduğu cümleye etki eder. Prompt bu yüzden "fısıldanacak **her cümlenin
başına**" diyor. Turluk bir fısıltı kolu istenirse `_current_mood`'un yanına
ayrı bir `style` kanalı gerekir; bu tur o makineyi kurmadı.

## 2. Kombo yükseltmeleri (üç eşleme)

Kombolar 27 Tem'de ölçüldü (14 koşul / 168 wav, **hepsi TEMİZ**), sonra kullanıcı
tekil/kombo/taban seslerini yan yana dinleyip **beş kombonun beşini de** seçti.
Token sırası ölçüldü: **fark standart sapmanın altında**, yani serbest; tutarlılık
için `<|emotion:X|><|prosody:Y|>` (resmi PROMPTING.md örnekleriyle aynı).

| etiket | ESKİ | YENİ | kullanıcının notu |
|---|---|---|---|
| `[surprise-ah/oh/wa/yo]` | `<\|emotion:awe\|>` | `<\|emotion:surprise\|><\|prosody:expressive_high\|>` | U1: "kombo her ikisinin tam bir karışımı" · U2: "hem kombo hem yalnız surprise gayet güzel" |
| `[mood:proud]` | `<\|emotion:pride\|>` | `<\|emotion:pride\|><\|prosody:expressive_high\|>` | **"kesinlikle kombo daha güzel"** |
| `[mood:calm]` | `<\|emotion:contentment\|>` | `<\|emotion:contentment\|><\|prosody:expressive_low\|>` | kombo seçildi; tekili "yoga hocası gibi" düz kalıyordu |

### `awe` eşlemeden TAMAMEN ÇIKTI — geri getirmeyin

`awe` bugün 15:0x'te `[surprise-*]`'a bağlanmıştı. Ölçümü kusursuzdu (12/12,
WER 0.000, Δsüre +0.35 s) ama **kullanıcı kulaklıkla dinleyince tekil `awe`'yi
KÖTÜ buldu**: *"şuh kalmış, heyecan yok."* `awe+expressive_high` kombosu temiz ve
beğenildi, ama şaşırma için `surprise` kombosu seçildi. Koda "denendi, kulakla
elendi" notu düşüldü ve `test_awe_is_gone_for_good` bunu kilitliyor.

### Kombo ATOMİK — `_add_prefix` değişti

`_to_higgs_markup` kategori başına tek token koyuyor ("istifleme evet, aynı
kategoriden iki tane hayır"). Kombo gelince yeni bir soru doğdu: parçalardan
biri dolu kategoriye düşerse ne olacak? Karar **tamamı düşer**. Aksi hâlde
`[mood:sad] … [surprise-oh]` cümlesinde `surprise` elenip `expressive_high`
kalırdı ve üzgün cümle sebepsiz canlanırdı. `test_losing_combo_is_dropped_whole`
bunu kilitliyor.

## 3. EKLENMEYENLER ve NEDENLERİ (kalıcı ders)

**Kalıcı ders: "TEMİZ" yalnızca ANLAŞILIR demek. Candan'ın SESİ olarak kabul
edilebilir demek DEĞİL.** Ölçüm gerekli, yeterli değil; son sözü kulak söyler.

### 3a. Sesin KİMLİĞİNİ bozanlar — asla kullanılmasın

| token | ölçüm | kulak |
|---|---|---|
| `prosody:pitch_low` | TEMİZ 12/12, Δ +0.77 s | **"taban kadın sesi, etiketli erkek ses"** |
| `style:shouting` | TEMİZ 12/12, Δ +0.18 s | **"ses başkasına ait gibi, ses rengi değişmiş"** |

İkisi de konuşanın **kim olduğunu** değiştiriyor. Bir ev asistanında bu tonlama
hatası değil, kimlik hatası: kullanıcı Candan'ı değil başkasını duyuyor.
`affection+pitch_low` kombosunun atlasta "şuh" bulunmasının sebebi de buydu —
kusur `affection`'da değil `pitch_low`'daydı. Koda yorum olarak yazıldı,
`test_identity_breaking_tokens_are_never_mapped` eşlemeye sızmasını engelliyor.

### 3b. Diğerleri

* `emotion:longing` → KÖTÜ: "her kelimeyi uzatması fazla".
* `prosody:speed_*` → **ÖLÜ YOL.** Kullanıcı kulakla da doğruladı ("taban ile
  aynı", `speed_fast` için "taban olan daha iyi"). Hız artık WSOLA ile yapılıyor
  (`worker/tempo.py`: +%14.8/+%29.7, WER tabanla aynı, ilk ses gecikmesi +1 ms).
  Aynı testin yasaklı listesinde.
* `emotion:anger` ("çok güzel"), `disgust`, `shame` ("harika"), `bitterness`,
  `helplessness` → temiz ve iyi, ama **ev asistanının ağzına uymuyor.**
  Reddedilme sebebi kalite değil ROL.
* `emotion:arousal`, `fear`, `style:singing` → idare/uygunsuz.
* `prosody:speed_very_slow` → ŞÜPHELİ (23/24, WER 0.075, ilk heceyi kırpıyor).
* `[question-*]`, `[confirmation-en]`, `[dissatisfaction-hnn]` → hâlâ SİLİNİYOR.
  `pitch_high` ölçümde temiz ama "soru tonu" demek değil; uydurma eşleme yapılmaz.

## 4. Kayda geçsin — uygulanMAYAN fikir

Kullanıcı U1 notunda *"farklı durumlarda üçünü de ayrı ayrı kullanabiliriz"*
dedi: tekil `surprise`, tekil `expressive_high` ve kombo **üç ayrı şiddet**
gibi kullanılabilir. Bunun için modele şiddet ayrımını öğretmek, yani prompt'a
yeni etiket sözlüğü koymak gerekir — prompt maliyeti var, ayrı karar.
Şimdilik tek eşleme (kombo) kullanılıyor. Aynı notun ikizi: `expressive_high`
duygu değil **vurgu** kolu olabilir (ör. `[emphasis]`), ölçümü zaten var.

## 5. Prompt maliyeti — net +3 satır

| dosya | önce | sonra | net |
|---|---|---|---|
| `pi/AGENTS.md` | 129 | 131 | **+2** |
| `pi/personas/candan.md` | 37 | 38 | **+1** |

Beş yeni etiket için üç satır. Yer şuradan açıldı:

* `AGENTS.md` mood listesi 3 madde-satırından 4'e çıktı ama **10 duyguyu** taşıyor
  (eskiden 6'ydı); tarifler kısaltıldı ("kullanıcı bir şey başardığında" → "başarı").
* `[whisper]` örneği yeni satır açmadı — mevcut `[surprise-oh]` örnek satırının
  yanına `·` ile eklendi.
* `candan.md`'den **tekrar eden** "Yerleşim: surprise/laughter cümle başında…"
  satırı silindi; aynı bilgi zaten madde madde yukarıda yazıyor.

"Listede olmayan işaret silinir" kuralı ikisinde de yerinde ve yeni etiketler
listeye girdi — model artık `[whisper]`'ı da yeteneklerini sayarken söyleyebilir.

## 6. Testler — 357 → **367** (+10)

`cd worker && ./.venv/bin/python -m unittest discover -s tests` → **367 OK**.
(Beş yeni test metodu; `TagMarkupTest` bir alt sınıfça da koşulduğu için sayaç
+10 artıyor.)

| test | neyi kilitliyor |
|---|---|
| `test_combo_moods_emit_both_tokens_in_order` | kombo iki token'ı da, ÖLÇÜLEN sırada üretir |
| `test_losing_combo_is_dropped_whole` | kaybeden kombo yarım kalmaz |
| `test_awe_is_gone_for_good` | `awe` hiçbir eşlemeye geri sızmaz |
| `test_identity_breaking_tokens_are_never_mapped` | `pitch_low`/`shouting`/`longing`/`speed_*` eşlemeye giremez |
| `test_whisper_is_a_sentence_initial_style_token` | `[whisper]` cümle başına taşınır, mood ile birlikte yaşar |

Ayrıca beş yeni etiket `PROMPT_TAGS`'e eklendi → **"tanınmayan etiket silinir"**
garantisi (`test_no_square_bracket_ever_reaches_server`) yeni etiketleri de
tarıyor, `test_map_values_are_valid_higgs_syntax` artık değerin BAŞTAN SONA
token'lardan ibaret olmasını istiyor (`sfx` taklit metni hariç).

`./check.sh` → yalnız ÖNCEDEN VAR OLAN 4 ruff bulgusu (`bench/ab_bench.py`,
`worker/pi_brain.py`); bu turda değişen dosyalarda bulgu yok.

## 7. Deploy (22:08 BST) — yapıldı

Yedekler: `*.bak-duygu2-20260727`. Gönderim öncesi sunucudaki üç dosyanın md5'i
yereldeki `HEAD` ile **bire bir** eşleşti (sunucuda kaçak değişiklik yoktu).

1. Yedek → 2. `rsync` üç dosya → 3. sunucuda `import higgs_tts` + eşleme
   doğrulaması (10 mood, üç kombo, `awe` yok) → 4. **yalnız `candan-worker`
   restart** → 5. log → 6. md5 **3/3 eşleşti**.

`pi-service` (14:43:43) ve `higgs-tts` (13:45:56) **restart EDİLMEDİ** —
`ActiveEnterTimestamp`'leri değişmedi, doğrulandı. `pi` süreçleri
`candan-worker`'ın çocukları olduğu için yeni `AGENTS.md`/`candan.md` worker
restart'ıyla yürürlüğe girdi.

Log: traceback YOK. `registered worker {"agent_name": "candan"}` 22:08:42.
(22:08:38'deki `exit code 255` restart sırasında ÖLEN eski süreç — beklenen.)

### TTS cache'i SİLİNDİ (11 dosya, 1.2 MB) — gerekçe

Cache anahtarı `sha256(ref, voice, mood_ADI, metin)`; **mood adı değişmedi,
üretilen token değişti** → aynı anahtar artık yanlış sesi gösteriyor. Bayat bir
HIT tam da kullanıcının elediği sesi çalardı (tekil `awe`, kombosuz `pride`).
Anahtar opak olduğu için seçici temizlik mümkün değil; 11 kısa kalıp cümlenin
bir kez yeniden sentezlenmesi ~0.5 s/cümle, ihmal edilebilir.

Not: **hız katmanı cache'e karışmıyor.** `_run_short` cache'e `_shape()`'ten
ÖNCEKİ ham PCM'i yazıyor, yani tempo pişmiş değil — WSOLA her çalışta yeniden
uygulanıyor. Cache'i silme sebebi yalnızca eşleme değişikliğidir.

## 8. Sonraki adım — KULLANICI kulakla dinlesin

Ajan görsel/işitsel test YAPMAZ. Canlıda denenmesi gerekenler:

* `[mood:proud]` ve `[mood:calm]` kombolu hâlleriyle (tekilinden farkı duyuluyor mu).
* `[surprise-*]` — `awe`'nin yerine gelen kombo şaşkın DUYULUYOR mu.
* `[whisper]` — çok cümleli bir yanıtta modelin her cümlenin başına koyup
  koymadığı (kapsam cümle; koymazsa ikinci cümle normal sesle çıkar).
* Dört yeni mood'un yerinde kullanılıp kullanılmadığı (prompt'ta tarifleri kısaldı).
