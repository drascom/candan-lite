# Vurgu eşlemede — `[emphasis]` CANLIDA (28 Tem)

Görev: `handoff/task-2026-07-28-vurgu-esleme.md`.
Öncesi: `handoff/2026-07-28-vurgu.md` (ölçüm) ·
`handoff/2026-07-28-vurgu-bekleme.md` (3. tur, kelime sonrası bekleme) ·
`experiments/vurgu/README.md`.

Bu tur **eşleme turu**: dört tur ölçüm ve üç tur kulak testinden sonra kullanıcının
seçtiği yol (`tire-on`) `worker/higgs_tts.py`'ye ve prompt'a girdi.
`higgs-tts` ve `candan-brain` ELLENMEDİ, `pi-service` **reload** edildi (restart DEĞİL),
yalnız `candan-worker` restart edildi.

## 1. Seçilen işaret: `[emphasis]`

Model vurgulamak istediği kelimenin **tam önüne** `[emphasis]` yazar.

**Neden `[vurgu]` değil `[emphasis]`:** koddaki bütün işaretler İngilizce
(`[laughter]`, `[sigh]`, `[whisper]`, `[pause]`, `[long_pause]`, `[mood:*]`,
`[speed:*]`) ve Higgs'in kendi token kataloğu da öyle. Türkçe tek bir ad, listeye
"bazıları Türkçe olabilir" fikrini sokardı; model uydurduğu an işaret **sessizce
silinir** (kodda karşılığı yok) ve Candan vurgu yaptığını sanır. Prompt Türkçe,
işaret adları İngilizce — bugüne kadarki düzen bu.

⚠️ `[emphasis]` bir Higgs TOKEN'ı DEĞİLDİR, o yüzden `HIGGS_TAG_MAP`'e girmedi.
Katalogda kelime düzeyinde vurgu yok: uydurma `<|prosody:emphasis|>` /
`<|emphasis:strong|>` / SSML `<emphasis>` **harfi harfine okunuyor** (WER 0.22-0.56,
`katalog_yoklama.py`). Vurgu bu yüzden ölçülmüş bir **noktalama yerleşimine** çevrilir.

## 2. Dönüşüm — ölçülen metinle BİREBİR

Kural: işaretin solundaki boşluk ve **virgül** atılır, yerine `" — "` (boşluk +
uzun tire U+2014 + boşluk) gelir; hedeften **sonrası olduğu gibi kalır**.

    model yazar : Hadi kalk bakalım, [emphasis] on dakikaya çıkmamız gerekiyor!
    sunucuya gider: Hadi kalk bakalım — on dakikaya çıkmamız gerekiyor!

Bu, `experiments/vurgu/vurgu_set.py::_on_isaretli(c, " — ")`nin (yani ölçülen
`tire-on` yolunun) ürettiği dizgenin **aynısıdır** — tahmin değil, testle bağlandı:

* `EmphasisTagTest.test_conversion_is_identical_to_the_measured_text` — üç ölçüm
  cümlesinin üçünde de çıktı, ölçülen metne `assertEqual`.
* `test_measured_text_still_matches_the_experiment_source` — beklenen dizgeler
  `vurgu_set.py` dosyasından **çalıştırılarak** doğrulanır (deneydeki tanım
  değişirse test çakılır).

`awe` cümlesi bu üçlüde YOK: deneyde ona `bagli` alanıyla sınır geriye ("hem de"nin
önüne) alınmıştı — aşağıya bak.

## 3. Yerleşim kuralı — işaret hedefin TAM ÖNÜNE (`awe` dersi)

3. turda `awe` cümlesinde sınır "hem de"nin önüne alınmıştı; gerekçe sağlamdı
(Türkçede öbek sınırından ÖNCEKİ kelime belirginlik alır, "hem de" hedefe bağlı
pekiştirici) ve **ölçüm de onu üstün görüyordu** (Δ perde +2.17 vs −0.72).
Kullanıcı o hâle **0/3**, sade yerleşime **3/3** verdi.

**Karar: `bagli` mantığı canlıya TAŞINMADI.** İşaret nereye konduysa sınır orada
kurulur; geriye doğru bağlı öbek kapsanmaz. Basit olan kazandı.

## 4. "Yarı cümlede tutuyor" — bilerek kabul edildi

Kulak sonucu (örnek başına karar, 4 cümle):

| cümle | `kombo` (iki yanlı) | `kombo-on` | **`tire-on`** (seçilen) |
|---|---|---|---|
| affection "olur mu" | 0/3 | 2/3 | **3/3** |
| arousal "on dakikaya" | 3/3 ama bekleme 3/3 | 0/3 | 0/3 |
| awe "tek başına" | 3/3 ama bekleme 3/3 | 0/3 (yanlış sınır) | 0/3 (yanlış sınır) |
| confusion "anlamadım" | 3/3 ama bekleme 3/3 | **3/3** | **3/3** |

**Yani vurgu yaklaşık cümlelerin YARISINDA tutuyor.** Bu bir hata değil, bilinen
ve kabul edilmiş sınır: *"neden hep çalışmıyor"* sorusunun cevabı burada.
Kullanıcı bunu bilerek seçti çünkü **başarısızlık zararsız** — tutmadığında ses
bozulmuyor, yalnız vurgu gelmiyor. Dört ölçüm turunda da WER 0.000, cümle başı
yeme 0, kelime sonrası fazladan bekleme yok.

⚠️ **Arkaya işaret koymak YASAK.** 2. turda tire hedefin iki yanındaydı; kullanıcı
altı ayrı notta "kelime sonrası uzun bekleme" dedi, ölçüm doğruladı (hedeften sonra
+0.31 s fazladan sessizlik; `confusion` +0.56, `arousal` +0.50). Arkadaki işaret
atılınca bekleme taban seviyesine döndü (−0.02 s). `test_nothing_is_added_after_the_target`
bunu kilitliyor.

## 5. Koruma kuralları (hepsi testli)

| kural | gerekçe |
|---|---|
| Cümlede **en fazla bir** vurgu; ilki kalır, gerisi silinir | çok vurgu vurgusuzluktur (mood'daki "ilk gelen kazanır" deseninin aynısı) |
| **Cümle başındaki** işaret atılır (`_MIN_WORDS_BEFORE_EMPHASIS = 1`) | vurgulanacak bağlam yok; tire orada diyalog çizgisine benziyor. Ölçülmemiş tek yerleşim bu. |
| **Sağında kelime kalmayan** işaret atılır | sınır bir kelimeyi öne çıkarır, cümlenin ucunu süslemez |
| İşaret düşerse **soldaki virgül yerinde kalır** | cümlenin noktalaması bozulmasın |
| `[emphasis]` ANLATILIYORSA → "vurgu" | `_READABLE` girdisi; silinseydi cümlede delik kalırdı (27 Tem'in canlı hatası) |
| Tanınmayan `[...]` yine silinir | mevcut garanti bozulmadı: sunucuya giden metinde köşeli parantez KALMAZ |

### Neden `_MIN_WORDS_BEFORE_PAUSE = 3` kuralı AYNEN uygulanmadı

`[pause]` için 3 kelime kuralı ölçülmüştü: erken duraklama token'ı 24'te 2 kez ilk
kelimeyi yiyordu. **Vurgu için ölçüm var ve tersini söylüyor:** `confusion` cümlesi
("Tam — anlamadım şimdi…") sınırı **1. kelimeden sonra** taşıyor; `tire-on` 5/5,
`tire` 8/8 örnekte baş yeme 0, WER 0.000. Yani uzun tire duraklama token'ı gibi
davranmıyor. Ölçülmemiş tek yerleşim sıfır kelimeli hâl (cümlenin en başı) — o
atılıyor. Şüphede kalırsak vurguyu kaybederiz, ilk kelimeyi asla.

## 6. Prompt

`pi/AGENTS.md` ve `pi/personas/candan.md`'ye aynı madde eklendi: *"anlam TEK bir
kelimeye asılıyorsa o kelimenin tam önüne; cümle başına koyma; cümlede en fazla bir"*
+ birer örnek.

**Net satır değişimi: 0.** `AGENTS.md` 147 → **147** satır, `candan.md` 38 → **38**
satır. Yer açmak için: işaret listesi girişi kısaltıldı, mood başlığı iki satıra
indirildi, `[laughter]`/`[surprise-oh]` ve iki örnek satırı birleştirildi.

Yetenek listesi koddaki eşlemeyle **birebir**: `test_prompt_offers_exactly_this_marker`
iki prompt dosyasında da `[emphasis]` arıyor ve karşılığı olmayan `[vurgu]`nun
sızmadığını doğruluyor (uyuşmazlık "yetenek yalanı" üretiyor).

## 7. Test

`cd worker && python -m unittest discover -s tests` → **402/402 OK** (378 → +24;
12 yeni test, streaming alt sınıfıyla iki kez koşuyor).
`./check.sh` → yeni bulgu YOK (aynı 4 eski bulgu: `B007` ab_bench, `RUF006`,
2× `PLW0603` pi_brain).

## 8. Deploy (28 Tem 00:53)

Sürüklenme kontrolü: deploy ÖNCESİ üç dosyanın da sunucu md5'i `HEAD` ile birebir
eşitti. Yedek: `/opt/candan-lite/.deploy-backup-20260728-vurgu/`.

```bash
scp worker/higgs_tts.py root@192.168.0.25:/opt/candan-lite/worker/higgs_tts.py
scp pi/AGENTS.md root@192.168.0.25:/opt/candan-lite/pi/AGENTS.md
scp pi/personas/candan.md root@192.168.0.25:/opt/candan-lite/pi/personas/candan.md
ssh root@192.168.0.25 'systemctl restart candan-worker'
ssh root@192.168.0.25 'systemctl reload pi-service'      # restart DEĞİL
```

Doğrulama:
* md5 sunucu = yerel, **3/3**: `higgs_tts.py` `a4badd79…`, `AGENTS.md` `11e27967…`,
  `candan.md` `239d14a0…`
* sunucuda import + dönüşüm koşturuldu: `EMPHASIS_TAG=emphasis`, ayraç `' — '`,
  `"Hadi kalk bakalım, [emphasis] on dakikaya…"` → `"Hadi kalk bakalım — on dakikaya…"`,
  cümle başındaki işaret düşüyor.
* `registered worker {"agent_name": "candan"}` 00:53:50, traceback YOK.
* `pi broker reload tamamlandı (1 süreç)` → yeni `pi` **00:53:53**'te doğdu, prompt
  dosyaları **00:53:35**'te yazılmıştı → yeni prompt'u okumak zorunda.
  (Kural: `pi/` altında prompt değiştiren her deploy `reload` ile biter; prompt
  süreç doğarken `--append-system-prompt` ile veriliyor, reload edilmezse bayat kalır.)
* Dört servis de `active`; `candan-worker` `NRestarts=0`.
* `higgs-tts`, `candan-brain` ELLENMEDİ. TTS cache SİLİNMEDİ: anahtar mood ADInı
  içeriyor, vurgu anahtara girmiyor ve cache'lenen kısa kalıplarda `[emphasis]` yok.

### Tek blok geri dönüş

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite && cp .deploy-backup-20260728-vurgu/higgs_tts.py worker/higgs_tts.py && cp .deploy-backup-20260728-vurgu/AGENTS.md pi/AGENTS.md && cp .deploy-backup-20260728-vurgu/candan.md pi/personas/candan.md && systemctl restart candan-worker && systemctl reload pi-service && systemctl is-active candan-worker pi-service higgs-tts candan-brain'
```

## 9. Sırada (kullanıcı)

Canlıda kulakla dinle: Candan `[emphasis]`i **yerinde ve seyrek** kullanıyor mu
(her cümlede değil), vurgu duyuluyor mu, tire hiçbir yerde SESLENDİRİLİYOR mu
(ölçümde WER 0.000'dı, canlı yolda da beklenen bu). Tutmadığı cümleler olacak —
§4'teki tablo bunu baştan söylüyor.
