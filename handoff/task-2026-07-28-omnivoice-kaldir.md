# Görev — OmniVoice'u KOMPLE kaldır (sunucu + kod)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

Kullanıcı kararı: *"Önce OmniVoice'u komple silip kaldıralım. Sonra Piper'a bakalım."*
Kapsam: **sunucu artıkları + kod**. Higgs tek motor olacak, `TTS_ENGINE` dallanması bitecek.

Gerekçe (27 Tem, kullanıcının kendi sözü): OmniVoice'a dönüş planlanmıyor — model
büyük ve ağır, RAM bütçesi yok. Kalıcı arıza hâlinde yol Piper (ayrı iş, henüz yok).

## ⚠️ ÖNCE BUNU OKU — silme sırası hayati

`/opt/higgs-tts/higgs.env` şu satırı içeriyor:
```
HIGGS_REF_AUDIO=/opt/omnivoice/default-ref.wav
```
**Candan'ın SES KİMLİĞİ bu dosyadan geliyor.** `/opt/omnivoice`'u önce silersen Higgs'in
sesi gider. Önce taşı, doğrula, sonra sil.

(`/opt/higgs-tts/refs/default-ref.codes.pt` önceden hesaplanmış kodları tutuyor;
çalışma anında o kullanılıyor olabilir. Yine de wav kaynak dosyadır — İKİSİNİ de koru.)

## Sunucudaki durum (ölçüldü)

| ne | boyut | not |
|---|---|---|
| `/opt/omnivoice-venv` | 7.3 GB | sil |
| `/opt/hf-cache/hub/models--k2-fsa--OmniVoice` | 3.1 GB | sil |
| `/opt/omnivoice` | 1.5 MB | **referans wav taşındıktan SONRA** sil |
| `omnivoice-bridge.service` | — | zaten `inactive` + `disabled`; disable + kaldır |
| `/opt/omnivoice-ref.wav`, `/opt/omnivoice-torch-install.log` | — | kontrol et, artıksa sil |

`/opt/higgs-exp/` ve `/opt/candan-lite-selfdev/` içinde de omnivoice izleri var —
bunlar DENEY/self-dev alanları, canlı değil. **Dokunma**, sadece raporda belirt.

## Sıra

### 1. Referans wav'ı PROJENİN İÇİNE taşı (ÖNCE)

⚠️ **Kullanıcının açık isteği:** referans dosyası bir daha taşınmak zorunda kalmasın
diye **projenin ana klasörüne** konsun, üçüncü bir servisin dizinine değil. Gerekçesi
sağlam: `/opt/higgs-tts/` Higgs'e ait: yarın Higgs'i yeniden kurar/kaldırırsak
(SGLang-Omni geçişi gündemde) Candan'ın ses kimliği onunla birlikte gider.

* Hedef: `/opt/candan-lite/assets/voice/default-ref.wav`
  (dizin adı sende; ama **proje kökünün altında** ve amacı adından belli olsun)
* **Git'e de ekle** — 691 KB, sürüm kontrolüne girmesi ses kimliğini kalıcı kılar ve
  yerelde de yedeği olur. Kökteki `.gitignore` engelliyorsa istisna ekle.
  Girmemesi gerektiğini düşünüyorsan gerekçeni yaz ve kullanıcıya sor.
* `.bak-20260726` yedeğini de taşı — Candan'ın sesinin tek kaynağı, kaybetme
* `/opt/higgs-tts/refs/default-ref.codes.pt` (önceden hesaplanmış kodlar) Higgs'e ait
  türetilmiş dosya, yerinde kalabilir — ama wav'dan yeniden üretilebildiğini doğrula
  ve nasıl üretileceğini belgeye yaz. Kaynak kaybolmadıkça kod dosyası kritik değil.
* `higgs.env`'de `HIGGS_REF_AUDIO` yeni yola çevrilsin (eski satır yedeklensin)
* **`higgs-tts` restart** — bu görevdeki TEK higgs restart'ı
* Restart sonrası doğrula: `POST /api/tts/stream` ile bir cümle üret, ses BOŞ DEĞİL,
  ve **ses kimliği aynı** (aynı cümlenin restart öncesi/sonrası çıktısını karşılaştır;
  ölçü olarak F0 ortalaması + süre yeter, birebir bayt beklenmiyor)
* ⚠️ **ZAMANLAMA:** başka bir ajan `:8809` üzerinden ölçüm sesleri üretiyor olabilir.
  Restart'tan ÖNCE `journalctl -u higgs-tts` son 3 dakikada istek var mı bak; varsa
  BEKLE ve tekrar dene. Ölçüm koşumunu kesme.

### 2. Sunucu artıklarını sil
`systemctl disable --now omnivoice-bridge` (zaten kapalı) → unit dosyasını kaldır →
`daemon-reload` → venv, HF model, `/opt/omnivoice` sil → `df -h` ile kazanılan yeri raporla.

⚠️ Silme GERİ ALINAMAZ (10+ GB yeniden indirme demek). Silmeden önce her yolun
gerçekten OmniVoice'a ait olduğunu DOĞRULA; şüphedeysen silme, raporla.

### 3. Koddan çıkar
* `worker/omnivoice_tts.py` sil
* `worker/agent.py`'deki motor seçimi (`TTS_ENGINE`) dallanması kalksın — Higgs tek yol
* `worker/.env` ve `.env.example`'daki OmniVoice satırları
* `higgs_tts.py`, `tts_cache.py`, `pi_brain.py`, `trnorm.py`, `truth_check.py`,
  `log_utils.py`, `wake_stt.py` içindeki OmniVoice atıfları: **işlevsel bağımlılık
  varsa kaldır**; yalnız yorum/tarih notuysa YANILTICI olanları düzelt, tarihsel
  kayıt niteliğinde olanları koru (ör. "OmniVoice'ta instruct ölüydü" gibi bir
  ölçüm kaydı silinmemeli, ama "geri dönüş için TTS_ENGINE=omnivoice" gibi artık
  yanlış olan bir talimat silinmeli/işaretlenmeli).
* `handoff/2026-07-27-DEVIR.md` §6'daki OmniVoice bloğu zaten "geçersiz" işaretliydi —
  artık tamamen kaldırılabilir, yerine "OmniVoice sistemden kaldırıldı (28 Tem),
  yol Piper" notu.

### 4. Test + deploy
* Tüm takım koşsun (şu an **399**). `TTS_ENGINE` dallanması kalktığı için ona bağlı
  testler olabilir — düşerse düzelt, sayıyı raporla.
* Deploy: yedek → gönder → import doğrula → **yalnız `candan-worker` restart** → log → md5.
* `pi-service`'e ve `candan-brain`'e DOKUNMA.
* Deploy sonrası canlı doğrulama: bir cümle seslendirilsin, ses geliyor mu.

## Belgeleme

`handoff/2026-07-28-omnivoice-kaldir.md`: taşınan referans, silinenler ve boyutları,
kazanılan disk, koddan çıkanlar, **geri dönüş NOTU** (silme geri alınamaz; ses
kimliği için yedeğin nerede olduğu net yazılsın). DEVİR güncellensin, başka ajanların
maddeleri SİLİNMESİN. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Referans wav taşındı mı, ses kimliği restart sonrası aynı mı (ölçüyle)
* Silinenler + kazanılan disk
* Koddan çıkanlar, test sayısı
* Deploy sonucu, tek blok geri dönüş, commit hash
