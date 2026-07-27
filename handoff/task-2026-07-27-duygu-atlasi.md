# Görev — DUYGU ATLASI (yeni proje, 1. tur)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Neden bu proje var

Bugüne kadarki ölçüm **"anlaşılıyor mu"** sorusunu cevapladı (43 token, WER, baş yeme).
Cevaplamadığı soru: **"doğru DUYULUYOR mu?"** Şaşırma vakası bunu kanıtladı —
`<|emotion:surprise|>` ölçümde 12/12 tertemizdi ama kulakta hiç şaşkın duyulmuyordu;
kullanıcı dinleyince `<|emotion:awe|>`e geçtik.

Ayrıca şu ana kadar her token **aynı nötr cümlede** dinlendi. Bir duygu, ona uymayan
cümlede zaten kendini gösteremez. Kullanıcı: *"tüm duyguları UYGUN cümleler içinde
demo sayfasında oluşturalım, boş zamanımda hepsini dinlemek istiyorum."*

Bu **ayrı ve uzun soluklu** bir proje — yavaş geliştirilecek. Bu 1. tur.

## Konum

Yeni dizin: `experiments/duygu-atlasi/`
`experiments/higgs-tts3/` içindeki takımı **yeniden yazma, içe aktar**:
`token_probe.py::synth` (canlı `POST /api/tts/stream`) ve `_wav`, `serve.sh` deseni,
`demo.html` sayfa yapısı. Ses ÜRETİMİ mutlaka canlı streaming ucundan olacak —
deney koşumu yanıltıyor (eski `elation` yanlış teşhisi tam bundan çıktı).

Dizine `README.md` yaz: projenin amacı (yukarıdaki "neden"), nasıl koşulur, nasıl
dinlenir, turların kaydı (1. tur şunu kapsadı, sırada ne var).

## 1. turun kapsamı

### A) Katalogdaki HER token için ONA UYGUN bir cümle

Katalog (43): `experiments/higgs-tts3/token_probe.py` içindeki `EMOTIONS` (21),
`PROSODY_PREFIX` (8), `PROSODY_INLINE` (2), `STYLES` (3), `SFX` (2).

* Cümleler **Türkçe**, Candan'ın ağzına yakışan gündelik ev-asistanı cümleleri olsun
  (bugünkü kazanan `awe` cümlesi gibi: *"Sınavdan tam not almışsın, hem de tek başına
  çalışarak."*). Duygu cümlenin İÇERİĞİNDE de olsun ki token'ın katkısı duyulsun.
* `anger/disgust/fear/shame` gibi canlıda KULLANILMAYAN duygular da girsin — bu bir
  atlas, canlı eşleme değil. Sayfada "canlıda kullanılmıyor" diye işaretle.
* Yerleşim kurallarına uy: emotion/style/prosody-önek **cümle başında bitişik**;
  `sfx` taklide bitişik (`<|sfx:laughter|>Haha, …`); `pause`/`long_pause` **cümle
  ortasında, iki yanı boşluksuz ve en az 3 kelimeden sonra** (ölçülmüş kurallar —
  `handoff/2026-07-27-duygu-katmani.md` §4).
* Her satırın **etiketsiz düz eşi** de üretilsin; fark yalnız token'dan gelsin.

### B) Kombo bölümü (tadımlık, ~8 tane)

Kullanıcı komboları merak ediyor. Anlamlı olanlardan seç, örneğin:
`awe+expressive_high`, `surprise+expressive_high` (A cümlesinde beğenilmişti),
`sadness+speed_slow`, `enthusiasm+speed_fast`, `affection+pitch_low`,
`contentment+expressive_low`, `pride+expressive_high`, `confusion+pause`.

⚠️ Kombolar **ÖLÇÜLMEMİŞTİR**. Sayfada bu net görünsün ("ölçülmedi — canlıya
girmeden önce `token_probe.py`/`token_eval.py` turu şart"). Kombo yalnız atlasta durur,
`worker/higgs_tts.py` eşlemesine BU TURDA hiçbir şey eklenmez.

### C) Sayfa

`duygu-atlasi.html` (ya da `index.html`), `./serve.sh` ile açılabilsin:

* Kategorilere ayrılmış (emotion / prosody / style / sfx / kombo), açılır başlıklar.
* Her satırda: token adı · gönderilen ham metin · **etiketli ve düz** iki oynatıcı
  yan yana · süre ve Δsüre · "canlıda kullanılıyor / kullanılmıyor / ölçülmedi" rozeti.
* Kullanıcı BOŞ ZAMANINDA tek tek dinleyecek — sayfa uzun ve gezinilebilir olsun,
  hangi satırı dinlediğini kaybetmesin (kategori içi numaralandırma yeter).
* **Not alma yeri:** her satırda kullanıcının kulak kararını işaretleyebileceği
  basit bir alan olsun (iyi / idare eder / kötü + serbest not), `localStorage`'a
  yazsın ve sayfanın altında "notları JSON kopyala" düğmesi olsun. Bir sonraki turda
  o JSON'u bana getirecek. Sunucuya/dosyaya yazma YOK, tamamen tarayıcıda.

## Sınırlar

* **Sunucuya HİÇBİR değişiklik yapma.** Yalnız `POST /api/tts/stream`. systemctl,
  dosya kopyalama, servis dokunuşu YOK.
* `worker/` altındaki canlı koda dokunma — bu tur salt üretim ve sayfa.
* Görsel/işitsel testi SEN yapmazsın; wav'ları üret, dinlemeyi kullanıcı yapar.
* Üretim uzun sürebilir (~100 wav). Sırayla koş, hata olursa DUR ve raporla;
  yarım kalırsa kaldığı yerden devam edebilsin (üretilmiş wav'ı yeniden üretme).

## Belgeleme

* `experiments/duygu-atlasi/README.md` (yukarıda tarif edildi).
* `handoff/2026-07-27-DEVIR.md` §5'e yeni madde: "Duygu atlası — ayrı proje,
  yavaş geliştiriliyor, 1. tur bitti, sırada kullanıcının kulak notları var."
* Tek commit, açıklayıcı Türkçe mesaj. Push ETME.

## Rapor (KISA)

* Kaç wav üretildi, hata var mı
* Dinleme komutu
* Kombo bölümünün ölçülmemiş olduğu notu
* Commit hash
