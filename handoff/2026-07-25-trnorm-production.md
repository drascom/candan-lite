# trnorm: Türkçe metin normalizasyonu production'a alındı

## Durum

**Sadece repo değişikliği. Deploy YAPILMADI, servise dokunulmadı, `.25`'e hiçbir yazma
isteği gönderilmedi.** Canlıya çıkarmak ayrı bir adım.

## Ne değişti

| Dosya | Değişiklik |
|---|---|
| `worker/trnorm.py` | **YENİ** — `experiments/tts-local-bench/trnorm.py`'den taşındı. stdlib-only, yeni bağımlılık YOK. |
| `worker/omnivoice_tts.py` | `from trnorm import normalize_tr` + `_run()` içinde tek satır çağrı |

Bench'teki kopya yerinde bırakıldı (deneyler ona bağlı). İki kopya şu an birebir aynı.

## Neden

OmniVoice'un kendi `normalize_text`'i Çince/İngilizce dışındaki dillerde **yalnız çıplak tam
sayıları** çeviriyor. Türkçede şunlar ham kalıyordu:

- `%25` → yüzde hiç okunmuyor
- `3.500` (binlik ayıraçlı) → rakam rakam okunuyor
- `14:30'da` (kesme işaretli ek) → çözülemiyor

Model çözemediği metinde hizalamayı kaybedince **kelime düşürüyordu** — kullanıcının bulduğu
örnek: "Toplantı saat 14:30'da **başlıyor**, lütfen geç kalma" cümlesinde "başlıyor" tamamen
atlanıyordu.

**Ölçüm** (ASR geri-dönüş testi, 14 cümle, Whisper large-v3-turbo ile transkribe edip beklenen
sözlü formla karşılaştırma — `experiments/tts-local-bench/out/asr_eval.json`):

| | WER | Atlanan kelime |
|---|---|---|
| OmniVoice dahili normalizasyon | %35.1 | 61 |
| **trnorm** | **%6.3** | **9** |
| *taban (gerçek insan sesi)* | *%0.0* | *0* |

Kullanıcı `omnipick-trnorm` setini dinledi ve onayladı.

## SIRALAMA KISITI (bunu bozmayın)

`OmniVoiceChunkedStream._run()` içindeki sıra **kritik**:

```
1. _extract_mood()   → [mood:X] KONTROL işaretini metinden SİLER
2. normalize_tr()    → ← YENİ, tam burada
3. _run_ws() / _run_http()
```

`normalize_tr()` **mood çıkarıldıktan SONRA** çağrılıyor ki `[mood:excited]` normalizer'a hiç
ulaşmasın. Sıra ters çevrilirse mood işareti normalizasyona girer.

Her iki yol da (WS nötr yol / HTTP mood yolu) **aynı** normalize metni alıyor — tutarsızlık yok.

## Seslendirilen etiketler korunuyor

OmniVoice `[laughter]`, `[sigh]`, `[question-*]`, `[surprise-*]`, `[confirmation-en]`,
`[dissatisfaction-hnn]` etiketlerini **seslendiriyor**, yani metne HAM gitmeleri gerekiyor.

trnorm köşeli parantez içindeki her şeyi kilitleyip (yer tutucu) sonunda aynen geri koyuyor.
`_OMNI_TAG_RE` listesindeki **13 etiketin tamamı** tek tek test edildi → **13/13 aynen korundu**.
Ayrıca yer tutucu bilerek rakamsız (U+E000+) seçildi; rakamlı olsaydı "çıplak tam sayı" kuralı
onu da çevirirdi (bu hata geliştirme sırasında yakalandı).

## ⚠️ `pronounce_tr.json` çakışma riski — KULLANICI KARARI GEREKİYOR

`.25:/opt/omnivoice/pronounce_tr.json` **repo dışı**, `bridge_server.py` kullanıyor. Görev
gereği dosyaya dokunulmadı ve sunucuya bağlanılmadı — dolayısıyla **içeriğini doğrulayamadım**.
Aşağıdakiler kod okumasına dayanan *risk uyarısı*, tespit edilmiş hata değil:

1. **Doğrudan çakışma beklenmiyor.** Yamanın bilinen hedefleri (`şimdi`, `şu an`, `işine`,
   `konuşuyor` — bkz. `handoff/2026-07-16.md`) saf harf; trnorm yalnız rakam/simge/kısaltma
   kalıplarına dokunuyor. Bu dört giriş etkilenmemeli.

2. **ÇİFT UYGULAMA riski.** Yamada sayı/yüzde okumasını elle düzelten bir giriş varsa artık
   gereksiz — ve zararlı olabilir. Örnek: `%` → "yüzde" çeviren bir giriş varsa, trnorm zaten
   "yüzde yirmi beş" ürettiği için yama bunu tekrar işleyip **"yüzde yüzde"** üretebilir.
   Kontrol edilmeli.

3. **Yeni kelimeler artık TTS metnine giriyor.** trnorm öncesinde metinde hiç görünmeyen
   `yüzde`, `bin`, `yüz`, `on`, `otuz`, `lira`, `virgül`, ay adları (`Mart`, `Ekim`…) artık
   düzenli olarak geçiyor. Yamada bunlarla eşleşen ve **kelime sınırı zayıf** bir giriş varsa
   şimdi yanlış yerde tetiklenebilir. Bu risk teorik değil: `handoff/2026-07-16.md`'de tam
   olarak bu tür bir sınır hatası kayıtlı ("sol-taraf `\b` sınırı eklendi, yoksa 'konuşuyor'
   bozuluyordu"). Özellikle `on` gibi çok kısa tokenlara dikkat.

**Önerilen kontrol (deploy'dan ÖNCE, salt-okuma):** `pronounce_tr.json`'daki anahtarları
gözden geçirip (a) sayı/yüzde ile ilgili girişleri, (b) yukarıdaki yeni kelimelerle eşleşebilen
girişleri işaretlemek. Karar kullanıcının.

## Doğrulama (yapılanlar)

- `python3 worker/trnorm.py --selftest` → **26/26 geçti**
- Etiket koruma → **13/13** etiket aynen korundu, normalizasyon yine de çalışıyor
- Mood akışı (`[mood:excited] Toplantı saat 14:30'da başlıyor.`) → mood `excited` yakalandı,
  işaret metinden silindi, saat "on dört otuzda" oldu, "başlıyor" yerinde
- `./check.sh` → **yeni bulgu YOK**; mevcut 4 bulgu önceden vardı (`worker/pi_brain.py`,
  `bench/ab_bench.py`), bu değişiklikle ilgisiz. `worker/trnorm.py` + `worker/omnivoice_tts.py`
  ruff temiz.
- **Uygulama çalıştırılmadı, ses üretilmedi, işitsel test yapılmadı** (görev kısıtı).

## Bilinen eksikler (trnorm)

- Cümle-sonu kelime düşmesi nadiren sürüyor (`speed=0.9` da çözmedi, nedeni bulunamadı).
- `attach_suffix` yaygın ekleri uyumluyor (-DA, -DAn, -lI, -lIk, yönelme/belirtme/tamlayan);
  **tanımadığı eki bozmadan aynen ekler**.
- Saat ayracı yalnız `:` — `9.05` biçimi saat sayılmaz.
- Trilyon üstü sayı yok. `m` (metre) gibi İngilizce kelimeyle çakışabilecek birimler bilerek
  kapsam dışı.
