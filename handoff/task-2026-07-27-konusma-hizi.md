# Görev — KONUŞMA HIZI: gerçekten değiştirilebilir olsun

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Canlı hata (27 Tem 18:21)

```
Ayhan : Biraz daha hızlı konuşabilir misin?
Candan: Tabii Ayhan, konuşma hızımı biraz daha artırabilirim.
Ayhan : Hala yavaş. Biraz daha hızlandırmanı istiyorum.
Candan: Tamam Ayhan, hızımı daha da artırıyorum.
Ayhan : Sanırım tempo değişmedi.
```

**Tempo gerçekten değişmedi ve değişemezdi.** `speed_*` token'ları 27 Tem ölçümünde
temiz çıktı ama etkileri zayıf olduğu için (|Δsüre| ≤ 0.2 s) eşlemeye ALINMADI.
Candan'ın elinde hız kolu yok, olmadığını da bilmiyor. Bu `truth_check`'in kapattığı
"araç yalanı"nın kardeşi: **yetenek yalanı**.

Kullanıcı kararı: **hızı gerçekten ekleyelim** (yalnız prompt'ta "yapamıyorum" demek
yeterli değil).

## Aşama 1 — ÖLÇ, sonra seç

İki yol var, hangisinin gerçek olduğunu ÖLÇEREK seç:

**(a) Token yolu** — `<|prosody:speed_fast|>` / `speed_very_fast` / `speed_slow`.
Bedava ama ölçülen etki zayıftı. **Yeniden ölç**: tek cümlede değil, canlı cevap
uzunluğunda (2-3 cümle) metinlerde, token başına ≥8 örnek, **kelime/saniye** cinsinden.
Δsüre değil hız ölç — asıl soru "duyulur biçimde hızlandı mı".
⚠️ `speed_very_slow` ŞÜPHELİ çıkmıştı (ilk heceyi kırpıyor), onu aday alma.

**(b) Motor/sinyal yolu** — gerçek ve ayarlanabilir olan:
* Önce `higgs-tts` ucunun (`POST /api/tts/stream`, :8809) **hız/rate parametresi
  kabul edip etmediğine bak** (kod: `experiments/higgs-tts3/`, sunucudaki servis).
  Kabul ediyorsa en temizi budur.
* Etmiyorsa **PCM üzerinde tempo değiştirme** (perde KORUNARAK — WSOLA/phase-vocoder
  tipi; basit resample perdeyi de değiştirir, Candan'ın sesi bozulur, KABUL EDİLMEZ).
  Streaming yapısını bozma: kodek çözücüsü sağa bakıyor, blok sağından 8 kare atılıyor,
  solundan 16 kare bağlam — **bu yapıya dokunma**, tempo dönüşümü onun ÇIKIŞINDA olsun.
  Gecikmeyi ölç: ilk ses şu an 0.55 s, bunu bozarsa değmez.

**Karar ölçütü:** hangisi kelime/saniye'yi belirgin (≥%15) ve anlaşılırlığı bozmadan
değiştiriyorsa o. Anlaşılırlık Whisper geri-dönüşüyle doğrulansın (WER), `token_eval.py`
deseni hazır.

Ölçüm bittiğinde kullanıcının **kulakla seçeceği** küçük bir set üret
(`experiments/` altında, `duygu-atlasi` sayfa deseniyle): aynı cümle normal / biraz
hızlı / hızlı — hangi kademe "biraz daha hızlı" isteğine karşılık geliyor.

## Aşama 2 — davranış

Hız **konuşma boyunca kalıcı bir ayar** olmalı, cümlelik bir süs değil: kullanıcı
"biraz daha hızlı konuş" dediğinde o oturum boyunca hızlı konuşmalı, sonraki cevapta
eski tempoya dönmemeli. Kullanıcının canlı şikâyeti tam buydu.

* Kademeler sınırlı ve adlandırılmış olsun (ör. `yavaş / normal / hızlı / çok hızlı`),
  serbest sayı değil. Aralık ölçümle belirlensin, uçları anlaşılırlığı bozmasın.
* Modelin hızı DEĞİŞTİRME yolu tek ve deterministik olsun (kontrol etiketi ya da
  mevcut tool deseni — kod tabanında hangisi yerleşikse ona uy, yeni mekanizma icat etme).
* **Uygulanmayan hız isteği "uyguladım" diye anlatılmasın.** `truth_check` desenine
  bağla: istek karşılanamadıysa (kademe sınırda, motor reddetti) modelin "hızlandırdım"
  cümlesi canlıya çıkmasın, harness doğrusunu söylesin.
* Varsayılan bugünkü hız olsun; bayrak kapalıyken davranış BUGÜNKÜYLE bire bir aynı.

## Aşama 3 — yetenek tarifini gerçeğe uydur (bu tur ZORUNLU)

Aynı konuşmada ikinci bir yetenek yalanı çıktı: Candan yeteneklerini sayarken
**"onaylama"** dedi — `[confirmation-en]` eşlemede SİLİNİYOR, öyle bir tepki yok.
Duygu gösterisinin sonundaki tonsuz "Peki, sen bu konuda ne düşünüyorsun?" ve
"Tamam, anladım." satırları da bu yüzden: model `[question-*]`/`[confirmation-en]`
kullandı, temizleyici sildi, geriye düz cümle kaldı.

`pi/AGENTS.md` ve `pi/personas/candan.md`'deki yetenek tarifini **gerçek eşlemeyle
hizala**: silinen etiketleri (onaylama, soru tonu, hoşnutsuzluk) listeden çıkar;
bugün eklenen `[mood:proud]`, `[mood:confused]`, `[mood:warm]`, `[mood:calm]`,
`[pause]`, `[long_pause]` listeye girsin; hız yeni davranışa göre yazılsın.
**Prompt maliyetini büyütme** — satır ekliyorsan başka satır kısalt, kaç satır
değiştiğini raporla.

## Sınırlar

* Ölçülmemiş şey canlıya girmez. Ölçüm **canlı yoldan** (`POST /api/tts/stream`,
  referans klonu, streaming) yapılır — deney koşumu yanıltır.
* Streaming blok/lookahead yapısına DOKUNMA.
* Deploy: kullanıcı yetkilendirdi. Yedek → gönder → doğrula → **yalnız candan-worker
  restart** → log → md5. `pi-service`'e ve `higgs-tts` servisine DOKUNMA
  (higgs-tts restart'ı gerekiyorsa ÖNCE SOR — atlas/ölçüm koşumlarını keser).
* Kullanıcının gerçek verisine test yazma yok.
* Tüm takım koşsun (şu an 271 test).

## Rapor (KISA)

* Ölçüm sonucu: hangi yol seçildi, kelime/saniye kazancı, WER, ilk ses gecikmesi
* Kullanıcının dinleyeceği setin komutu
* Değişen dosyalar, test sayısı, deploy sonucu, tek blok geri dönüş
* Prompt'ta kaç satır değişti
* Commit hash
