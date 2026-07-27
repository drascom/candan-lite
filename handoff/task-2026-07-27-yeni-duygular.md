# Görev — YENİ DUYGULAR (kulakla seçilenlerden kombo OLMAYANLAR)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Neden bunlar, neden şimdi

Kullanıcı duygu atlasını kulaklıkla dinledi (44 satır: 28 iyi, 12 idare, 4 kötü),
ardından kombo setini de dinledi (`kombo.html`). İki listeden çıkan kararlar burada.

Kombolar 27 Tem'de ÖLÇÜLDÜ: 14 koşul / 168 wav, **hepsi TEMİZ** (12/12 anlaşıldı,
WER 0.000, boş çıktı/başı yeme yok). Token sırası fark etmiyor — ölçüldü, farklar
standart sapmanın altında. Kullanılacak sıra `<|emotion:X|><|prosody:Y|>`.
Tablo: `handoff/2026-07-27-kombo-olcumu.md`.

## Eklenecekler

Hepsi 27 Tem ölçümünde **12/12 anlaşıldı, WER 0.000** (`handoff/2026-07-27-duygu-katmani.md`
§2) ve kullanıcı atlasta hepsine **"iyi"** dedi:

| yeni etiket | → Higgs | kullanıcının notu / gerekçe |
|---|---|---|
| `[mood:amused]` | `<\|emotion:amusement\|>` | "sorunsuz" — şakalaşma, hafif alay |
| `[mood:thinking]` | `<\|emotion:contemplation\|>` | "tonlama ve duraklamalar gayet güzel" — "bir düşüneyim" |
| `[mood:determined]` | `<\|emotion:determination\|>` | "iyi" — söz verme, kararlılık |
| `[mood:relieved]` | `<\|emotion:relief\|>` | "iyi" — "çözüldü, geçmiş olsun" |
| `[whisper]` | `<\|style:whispering\|>` | **"harika"** — bebek uyuyor, gece, sessiz ortam |

### Kombo yükseltmeleri (kulakla seçildi, ölçüldü)

| etiket | ESKİ | YENİ | kullanıcının notu |
|---|---|---|---|
| `[surprise-*]` (dördü) | `<\|emotion:awe\|>` | `<\|emotion:surprise\|><\|prosody:expressive_high\|>` | U1: "kombo her ikisinin tam karışımı" · U2: "hem kombo hem yalnız surprise gayet güzel" |
| `[mood:proud]` | `<\|emotion:pride\|>` | `<\|emotion:pride\|><\|prosody:expressive_high\|>` | **"kesinlikle kombo daha güzel"** |
| `[mood:calm]` | `<\|emotion:contentment\|>` | `<\|emotion:contentment\|><\|prosody:expressive_low\|>` | kombo seçildi |

⚠️ `awe` eşlemeden TAMAMEN ÇIKIYOR. Bugün 15:0x'te `[surprise-*]` ona bağlanmıştı;
kullanıcı kulaklıkla dinleyince tekil `awe`'yi **KÖTÜ** buldu ("şuh kalmış, heyecan
yok"). `awe+expressive_high` kombosu iyi bulundu ama şaşırma için `surprise` kombosu
seçildi. Koda kısa not düş: **awe denendi, kulakla elendi** — ileride biri geri
getirmesin.

Kayda geçsin (bu turda UYGULANMAYACAK): kullanıcı U1 notunda *"farklı durumlarda
üçünü de ayrı ayrı kullanabiliriz"* dedi — yani tekil `surprise`, tekil
`expressive_high` ve kombo üç ayrı şiddet gibi kullanılabilir. Bunun için modele
şiddet ayrımı öğretmek, yani prompt'ta yeni etiket sözlüğü gerekir; maliyeti var,
ayrı karar. Şimdilik tek eşleme (kombo) kullanılıyor.

Etiket adlarını mevcut düzene uydur (`[mood:X]` deseni yerleşik; `whisper` duygu
değil biçem olduğu için `[mood:]` altına sokmak yanlış olur — kod tabanındaki
`[laughter]`/`[pause]` desenine bak, hangisi tutarlıysa onu seç ve gerekçeni yaz).

## Eklenmeyecekler ve nedeni (kayda geçsin)

* `anger` "çok güzel", `disgust`, `shame` "harika", `bitterness`, `helplessness`
  → temiz ve iyi ama **ev asistanının ağzına uymuyor**; ölçüt "temiz olması gerekli,
  yeterli değil".
* `pitch_low` → **KÖTÜ**: "taban kadın sesi, etiketli erkek ses" — sesin KİMLİĞİNİ
  değiştiriyor. Asla kullanılmamalı; bunu koda yorum olarak yaz ki ileride biri
  denemesin. `affection+pitch_low` kombosunun "şuh" bulunmasının sebebi de bu.
* `shouting` → "ses başkasına ait gibi, ses rengi değişmiş" — aynı sebep.
* `longing` → KÖTÜ, "her kelimeyi uzatması fazla".
* `speed_*` token'ları → kullanıcı kulakla da doğruladı ("taban ile aynı", "fark yok",
  `speed_fast` için "taban olan daha iyi"). Hız artık **WSOLA** ile yapılıyor
  (`worker/tempo.py`), token yolu ölü. Bunu da yorumda belirt.
* `arousal`, `fear`, `singing` → idare/uygunsuz.

## Ayrıca

`[mood:calm]` şu an `<|emotion:contentment|>`. Kullanıcı bunu **"idare"** buldu:
*"ses tonu çok yumuşak, sanki yoga hocası konuşması gibi; öyle durumlarda
kullanılabilir, uykuya dalma yardımcısı gibi."* Kombo kararı geldiğinde
`contentment+expressive_low`'a yükseltilmesi gündemde — **bu turda DEĞİŞTİRME**,
yalnız koda bu notu düş.

## Prompt maliyeti

`pi/AGENTS.md` ve `pi/personas/candan.md`'ye beş yeni etiket girecek. **Satır sayısını
şişirme**: mevcut tarifleri sıkıştırarak yer aç, net artış mümkün olduğunca küçük olsun.
Kaç satır değiştiğini raporla. Yeni etiketler "listede olmayan işaret silinir"
kuralıyla tutarlı olsun.

## Test + deploy

* Her yeni etiket için dönüşüm testi + "tanınmayan etiket silinir" garantisinin
  bozulmadığı testi. Şu an **357 test** geçiyor.
* Deploy: yedek → gönder → import doğrula → **yalnız `candan-worker` restart** →
  log → md5. ⚠️ `pi-service`'e ve `higgs-tts`'e DOKUNMA.
* Eşleme değişti → sunucudaki TTS cache'inde bayat PCM kalmış olabilir, kontrol et
  (hız katmanı ham sesi saklıyor olabilir; gerekmiyorsa silme, gerekçeni yaz).

## Belgeleme

`handoff/2026-07-27-yeni-duygular.md`: eklenenler, eklenmeyenler ve NEDENLERİ
(özellikle `pitch_low`/`shouting`'in ses kimliğini bozması — bu kalıcı bir ders).
DEVİR'e kısa madde; başka ajanların maddelerini SİLME. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Eklenen etiketler ve karşılıkları
* Prompt'ta net kaç satır değişti
* Test sayısı, deploy sonucu, tek blok geri dönüş
* Commit hash
