# Görev — VURGU: bir kelimeyi öne çıkarabiliyor muyuz?

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

⚠️ **YALNIZ `experiments/` ve `handoff/` altında çalış.** `worker/`'a DOKUNMA —
şu anda başka ajanlar orada. Eşleme değişikliği bu görevin işi değil; bu tur
"yapılabiliyor mu" sorusunu cevaplıyor.

## Neden

Kullanıcı duygu atlasını kulaklıkla dinledi (44 satır). En çok tekrar eden şikâyet
duygu değil **vurgu** — beş ayrı satırda, kendi kelimeleriyle:

| token | kullanıcının notu |
|---|---|
| `emotion:affection` | "**olur mu** kısmında vurgu eksik" |
| `emotion:arousal` | "**on dakikaya** derken vurgu lazım" |
| `emotion:contemplation` | "tonlama ve duraklamalar gayet güzel, sadece **da** vurgusu eksik" |
| `emotion:awe` | "tam not vurgusu güzel ama **tek başına** kısmına da vurgu lazım" |
| `kombo:confusion+pause` | "**anlamadım** kelimesinde vurgu yok" |

Higgs kataloğunda (43 etiket) **kelime düzeyinde vurgu token'ı YOK.** `expressive_high`
var ama o cümlenin tamamına etki ediyor — kullanıcının istediği belirli bir kelimenin
öne çıkması. Bu tur o boşluğun doldurulup doldurulamayacağını ÖLÇER.

## Denenecek yollar

Hepsi aynı cümlelerde, aynı hedef kelimede:

1. **Taban** — hiçbir şey (karşılaştırma zemini)
2. `<|prosody:expressive_high|>` cümle başında — ölçülü ve temiz, ama cümle geneline
   etki ediyor; hedef kelimeyi ayırt ediyor mu, yoksa her şeyi mi yükseltiyor?
3. **Hedef kelimeden hemen önce `<|prosody:pause|>`** (bitişik, boşluksuz — ölçülmüş
   yerleşim kuralı). Duraklama dikkat çeker mi?
4. **Büyük harf** — `BUGÜN` gibi. Modeller çoğu zaman buna tepki verir.
5. **Noktalama** — hedef kelimenin iki yanına virgül; ya da kısa çizgi/tire ile ayırma.
6. **Kombinasyon** — en umut verici ikisini birlikte.
7. Katalogda gözden kaçmış bir şey var mı: `experiments/higgs-tts3/` altındaki resmi
   PROMPTING dokümanına/paketlemeye tekrar bak, `emphasis`/`stress`/`accent` benzeri
   bir etiket ya da SSML benzeri bir sözdizimi destekleniyor mu. **Varsa öncelik onun.**

## Cümleler

Kullanıcının şikâyet ettiği cümlelerin kendisini kullan (atlas setinden al,
`experiments/duygu-atlasi/atlas_set.py` içinde duruyorlar) — böylece "eksik" dediği
vurgu gerçekten geldi mi doğrudan karşılaştırılabilir. En az üç cümle, her birinde
hedef kelime belli.

## Ölçüm — asıl zorluk bu

"Vurgu geldi mi" kulakla belli olur ama önce **sayıyla eleme** yapılmalı, yoksa
kullanıcıya 40 ses dinletiriz. Hedef kelimenin taban hâline göre farkına bak:

* **süre** (kelime uzadı mı),
* **enerji/RMS** (yükseldi mi),
* **perde hareketi** (F0 tepe farkı).

Kelime sınırları için Whisper'ın **kelime zaman damgalarını** kullan
(`mlx-community/whisper-large-v3-turbo`, `word_timestamps`). Hız işinde `worker/tempo.py`
ve `experiments/konusma-hizi/speed_eval.py` benzer sinyal işleme yapıyor, desen oradan
alınabilir — ama `worker/`'a DOKUNMA, sadece OKU.

**Anlaşılırlık şartı da ölç:** WER yükselmemeli, cümlenin başı yenmemeli. Büyük harf
ve noktalama denemelerinde model harfleri tek tek okuyabilir ya da tuhaf duraklar
yapabilir — bu tuzağa dikkat, ölçüm onu yakalamalı.

Koşul başına **≥8 örnek**. Δsüre eleme ölçütü DEĞİL (kullanıcı kararı).

## Kulak seti

Ölçümde en umut veren 3-4 yolu, taban ile yan yana, `duygu-atlasi` sayfa deseniyle
sun (kulak notu + JSON kopyalama dahil). Kullanıcı hangi yolun gerçekten vurgu
duyurduğunu seçecek. Sayfada hedef kelime **yazıyla işaretli** olsun ki neye
odaklanacağını bilsin.

## Sınırlar

* Ölçüm **canlı yoldan**: `POST /api/tts/stream` (:8809), referans klonu, streaming.
  Deney koşumu yanıltır.
* `higgs-tts` servisini RESTART ETME.
* Sunucuya hiçbir değişiklik yok.
* Yol bulunamazsa bu da bir sonuçtur — "olmuyor" demek, zorlama bir çözüm uydurmaktan
  iyidir. Neyin neden olmadığını yaz.

## Belgeleme

`handoff/2026-07-28-vurgu.md`: denenen yollar, ölçüm tablosu, öneri (ya da "yol yok"),
kulak setinin komutu. DEVİR'e kısa madde; başka ajanların maddelerini SİLME.
Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Katalogda vurgu için bir şey var mıydı
* Hangi yol işe yaradı, sayılarla (süre/enerji/F0 farkı, WER)
* Kulak setinin komutu
* Commit hash
