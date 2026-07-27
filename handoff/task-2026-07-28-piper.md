# Görev — PIPER: emniyet ağını kur ve ÖLÇ (bağlama YOK)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Neden

Higgs artık **tek motor**. 28 Tem'de OmniVoice komple kaldırıldı (10 GB kazanıldı) ve
`TTS_ENGINE` dallanması da koddan çıktı. Yani Higgs'te kalıcı bir arıza olursa
Candan **hiç konuşamaz**. Kullanıcının kararı: emniyet ağı **Piper** olsun
(küçük, CPU'da koşar, RAM bütçesini yemez — OmniVoice'un kaldırılma sebebi buydu).

## Bu turun sınırı — ÖNEMLİ

Bu tur **yalnız kurar ve ölçer. Canlı sisteme BAĞLAMA.**
`worker/` altındaki hiçbir dosya değişmeyecek, `candan-worker` restart edilmeyecek,
`.env`'e motor anahtarı eklenmeyecek. Sebep: Piper'ın Türkçesi duyulmadan nasıl
bağlanacağına karar vermek erken. Bağlama ayrı tur, kullanıcı sesi dinledikten sonra.

## 1. Kurulum

* Kendi izole yerinde: `/opt/piper` + `/opt/piper-venv` (ya da tek binary yeterliyse
  venv'e gerek yok — hangisi sadeyse). Sistem paketlerini kirletme.
* **Türkçe ses modeli** indir. Piper'ın Türkçe seslerinden hangileri varsa listele;
  birden fazlaysa hepsini indirip karşılaştır (dosyalar küçük).
* systemd servisi **KURMA** ya da kurduysan `disabled` bırak. Bu tur boot'a girmiyor.
* Disk ve RAM ayak izini ölç ve raporla (OmniVoice 10 GB idi; Piper'ın ne olduğu
  kararın parçası).

## 2. Ölçüm — Higgs ile aynı ölçütlerle

Karşılaştırma anlamlı olsun diye **aynı cümleleri** kullan: `experiments/higgs-tts3/`
altındaki Türkçe ölçüm cümleleri (`sentences.json` / `token_probe.py` içindeki S1-S3)
ve `experiments/duygu-atlasi/` cümlelerinden birkaçı.

Ölç:
* **Anlaşılırlık**: Whisper (`mlx-community/whisper-large-v3-turbo`) geri-dönüşü, **WER**.
  Higgs'in Türkçe WER'i **0.028** (OmniVoice 0.085 idi) — Piper'ınki bunun neresinde?
* **Hız**: RTF ve ilk ses gecikmesi. Higgs streaming'de ilk ses **0.55 s**.
* **Türkçe özel durumlar**: Higgs'i seçme sebebimiz buydu — `%25`, `3.500 lira`,
  `14:30'da`, `1994'te` HAM METİNDEN doğru okunuyor mu? Piper okuyamıyorsa
  `worker/trnorm.py` (metin normalleştirme) devreye girmeli; bunu TESPİT et, ama
  bu turda BAĞLAMA.
* **Duygu/kontrol token'ları**: Piper'da yok, bunu açıkça yaz — düşüşün boyutu bu.

## 3. Kulak seti

Kullanıcının dinleyeceği küçük bir sayfa (`duygu-atlasi`/`vurgu` deseni):
aynı cümle **Higgs** ve **Piper** ile yan yana. 6-8 cümle yeter, kulak notu +
JSON kopyalama olsun. Kullanıcı "bu ses acil durumda kabul edilebilir mi" sorusuna
cevap verecek.

Higgs örneklerini yeniden üretmen gerekirse `:8809`'dan al; **`higgs-tts`'i RESTART ETME**
ve istek atmadan önce son 3 dakikada başka koşum var mı bak (başka ajan olabilir).

## 4. Karar için veri

Raporun sonunda şu üç soruyu cevapla:
1. Piper Türkçesi **acil durum için** yeterli mi (WER + kulak)?
2. Bağlanırsa nasıl olmalı — otomatik devreye giren yedek mi (Higgs sağlıksızsa),
   yoksa elle çevrilen bir kol mu? Gerekçeni yaz; **uygulama YOK**, öneri.
3. `trnorm` gerekli mi, gerekiyorsa ne kadarı?

## Sınırlar

* `worker/`, `pi-service`, `candan-brain`, `higgs-tts` — hiçbirine DOKUNMA.
* Canlı `.env` değişmez, servis restart edilmez.
* Disk: sunucuda 159 GB boş, sıkıntı yok ama indirdiğini raporla.
* Kurulum başarısız olursa DUR ve raporla — yarım kurulum bırakma, temizle.

## Belgeleme

`handoff/2026-07-28-piper.md`: kurulum yolu, ölçüm tabloları (Higgs ile yan yana),
üç sorunun cevabı, kaldırma komutu (Piper'ı silmek gerekirse tek blok).
DEVİR'e kısa madde; başka ajanların maddelerini SİLME. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Kurulum yeri, disk/RAM ayak izi
* WER · RTF · ilk ses — Higgs ile yan yana
* Türkçe sayı/tarih okuma sonucu
* Kulak setinin komutu
* Üç sorunun cevabı
* Commit hash
