# ARAŞTIRMA F — Akış temelli doğrulama: DTW (uyandırma kelimesi) + prozodi füzyonu

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**SALT-OKUNUR / ARAŞTIRMA.** Kod yazma, deploy, canlıya yazma YOK.
Dış kaynak araştırması serbest (WebSearch/WebFetch).

## Önce codebase-memory

`codebase-memory-mcp` ile başla. İndeks yoksa `index_repository`. Grep/Glob sonra.

## ⚠️ EN ÖNEMLİ TESLİMAT

Bu araştırmanın **birincil çıktısı** şu: **gölge kaydediciye ŞİMDİ eklenmesi gereken
alanların kesin listesi.** Veri toplama başladı; alanları sonradan eklersek toplanan
veri işe yaramaz ve baştan başlarız. Onun için önce bunu netleştir, gerekçesini yaz.

## Bağlam (veri — tekrar araştırma)

* Boru hattı: ham dalga → sherpa-onnx içinde fbank → **ResNet34** → 256-d embedding.
  `worker/speaker_id.py:220 embed_samples` sherpa'ya ham dalga veriyor; öznitelik
  çıkarma sherpa'nın içinde. Model: `wespeaker_en_voxceleb_resnet34_LM` (İngilizce VoxCeleb).
* Ölçüm (Araştırma A): 790 kararın %70.5'i Bilinmeyen. Sebep eşik DEĞİL, **tur uzunluğu**:
  0 pencere → %0, 1 pencere → %7, 2 → %59, ≥5 → %82. Turların %38'i ölü bölgede.
* Pencere: ilk 1.0 sn, sonrası hop 1.0 / içerik 1.5 sn (`speaker_tap.py:385-392`).
* Gölge kaydedici (YENİ, `speaker_tap.py`, commit `4a7109c`): oturum başına
  `worker/data/session-emb/<id>.npz`; alanlar `ts, turn, emb(float16), best_name,
  best_score, second_name, second_score, window_seconds, rms, decided`.
  Reddedilen pencereler de yazılıyor. Embedding yeniden hesaplanmıyor, hızlı yolun
  ürettiği kullanılıyor.
* Kullanıcının fikri (bu görevin kaynağı): *"Sesleri waveform ile analiz etsek,
  görsellere çevirsek... medyan medyana değil de sesin dalga formatına, akışına göre
  analiz etsek."* Doğru teşhis: model her pencereyi tek vektöre eziyor (pooling),
  biz de vektörleri ortalamaya eziyoruz — **zaman içindeki akış iki kez atılıyor.**
* Donanım: 6 CPU çekirdeği boş (yük 0.02), 14 GB RAM. ⚠️ GPU VRAM 21.2/24 DOLU,
  yeni model SIĞMAZ. CPU tasarımı yap.

## Araştır

### 1. Prozodi öznitelikleri — ŞİMDİ eklenecekler
Hangi öznitelikler konuşmacı kimliği taşır ve embedding'den **bağımsız** bilgi verir?
Değerlendir: F0 (perde) istatistikleri ve eğrisi, jitter, shimmer, HNR, formant
yörüngeleri, konuşma hızı, enerji eğrisi, sesli-çerçeve oranı, spektral eğim.

Her biri için:
* Konuşmacıya mı özgü, içeriğe/duyguya mı bağlı? (Kullanıcı duyguyla oynuyor —
  duyguya çok duyarlı öznitelik bizde ZARARLI olabilir, bunu değerlendir.)
* 1-1.5 sn'lik pencerede güvenilir hesaplanır mı? (Ölü bölgemiz orası.)
* CPU maliyeti, gerçek zamanlı yola girer mi?
* **Ham ses saklamadan** hesaplanıp sayı olarak yazılabilir mi? (ŞART — biyometrik
  veri, depo PUBLIC, ses saklamıyoruz.)
* Hangi kütüphane? ⚠️ `worker/.venv`'de NE VAR bak; yeni ağır bağımlılık önerme
  (praat-parselmouth, librosa gibi şeyleri maliyetiyle birlikte değerlendir; numpy/scipy
  ile yapılabiliyorsa onu tercih et).

**Çıktı: kaydediciye eklenecek alanların kesin listesi** — alan adı, dtype, boyut,
oturum başına ek bayt maliyeti. Az ve öz olsun; "her ihtimale karşı hepsini yaz" deme,
ama sonradan pişman olacağımız bir şeyi de atlama.

### 2. Uyandırma kelimesinde DTW
"Candan" her turda söyleniyor → sabit ifade → metne-bağlı doğrulama mümkün.
* **ÖNCE FİZİBİLİTE:** uyandırma kelimesinin ses segmenti boru hattında ayrıştırılabiliyor
  mu? Wake word tespiti nerede yapılıyor (`WAKE_*` kolları, `worker/`), segmentin
  zaman sınırları elimizde mi? **Yoksa bu fikir şimdilik ölü — açıkça söyle.**
* Ayrıştırılabiliyorsa: DTW neyin üzerinde koşar (fbank çerçeveleri? embedding
  çerçeveleri? bizde çerçeve düzeyi çıktı var mı, yoksa sadece havuzlanmış vektör mü)?
* Maliyet ve beklenen kazanç. Kaynak göster.

### 3. Füzyon
Prozodi/DTW skoru embedding skoruyla nasıl birleştirilir? Basit ağırlıklı toplam mı,
kalibre edilmiş füzyon mu (Araştırma B'de QMF/lojistik regresyon geçmişti)?
⚠️ Çalışma noktamız: **yanlış ret pahalı, yanlış kabul ucuz** (ev, tehdit yok) —
ama yanlış kabul hafızaya yanlış atfetme riski üretiyor.

### 4. Ölçüm
Toplanan veriyle bu yöntemlerin kazancını **canlıya dokunmadan** nasıl ölçeriz?
Metrik: kaçırma oranı (bugün %70.5) VE yanlış atama oranı birlikte.

## Kısıtlar

* ⛔ **GÖRSELLEŞTİRME KAPSAM DIŞI.** Kullanıcı açıkça istemedi: spektrogram görüntüsü
  üretme, panel/dashboard, grafik, "gözle denetleme" arayüzü ÖNERME. Çıktı yalnız
  **sayısal öznitelikler ve karar mantığı** olacak. (Spektrogramın modelin İÇİNDE
  öznitelik olarak kullanılması ayrı şey — o zaten var, ona dokunmuyoruz.)
* Kod YAZMA, deploy YOK, canlı `.25`'e yazma (okuma serbest).
* Kişisel ses kaydı açma/dinleme/kopyalama YOK. Ham ses saklamayı önerme.
* VRAM'e yeni model önerme.
* Bizde çalışmayacak bir şeyi "literatürde var" diye önerme. Kaynak ver; emin
  değilsen "doğrulanmadı" yaz.

## Rapor (KISA — 22 satır)

* **[BİRİNCİL] Kaydediciye eklenecek alan listesi** — ad, dtype, boyut, ek bayt/oturum,
  neden bu ve neden diğerleri değil
* Duyguya duyarlılık değerlendirmesi (hangi öznitelik duygudan etkilenir)
* Kütüphane kararı (venv'de var mı, yoksa maliyeti ne)
* DTW fizibilitesi: uyandırma segmenti ayrıştırılabiliyor mu — EVET/HAYIR + gerekçe
* Füzyon önerisi
* Ölçüm planı
* Bilmediklerin / doğrulanmamış varsayımların
