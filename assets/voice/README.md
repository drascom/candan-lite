# assets/voice — Candan'ın ses kimliği

Bu dizin **referans sesi** tutar: Higgs TTS'in her cümleyi klonladığı kaynak.
Bu dosya giderse **Candan'ın sesi gider** ve geri gelmez.

| ne | değer |
|---|---|
| dosya | `default-ref.wav` |
| md5 | `429e867bcad6cf6e8c4109efe88f5e6f` |
| biçim | WAV, 48 kHz, mono, 16 bit, **7.20 sn**, 691.244 bayt |
| referans metni | `Merhaba, bu bir Türkçe seslendirme testidir. VoxCPM 2 ile uzun kitapları sesli kitaba dönüştürebilirsiniz.` |
| kökeni | `mate/vox/turkce_test.wav` → 9 Tem'de OmniVoice klon referansı olarak sabitlendi |

## Nerede duruyor

| yer | yol |
|---|---|
| bu repo (çalışma kopyası) | `assets/voice/default-ref.wav` |
| sunucu (canlı, `HIGGS_REF_AUDIO`) | `root@192.168.0.25:/opt/candan-lite/assets/voice/default-ref.wav` |

28 Tem'de `/opt/omnivoice/`'dan **buraya taşındı** — üçüncü bir servisin dizininde
durmasın diye. Eski üç kopya (`/opt/omnivoice/default-ref.wav`,
`…/default-ref.wav.bak-20260726`, `/opt/omnivoice-ref.wav`) **md5'i birebir aynıydı**
ve OmniVoice kaldırılırken silindi.

## ⚠️ GIT'E GİRMİYOR — bilinçli, kullanıcı onayı bekliyor

Depo **PUBLIC** (`github.com/drascom/candan-lite`) ve kökteki `.gitignore` üç ayrı
yerde referans wav'larını *"biyometrik veri, girmez"* diye dışlıyor. Bir ses-klon
referansını public geçmişe yazmak **geri alınamaz** (herkes Candan'ın sesini
klonlayabilir), o yüzden bu worker dosyayı commit ETMEDİ.

Kullanıcı yine de istiyorsa tek adım:

```bash
git add -f assets/voice/default-ref.wav && git commit -m "ses: referans wav"
```

Depo private'a çekilirse bu tereddüt ortadan kalkar.

## Referans kodları (`default-ref.codes.pt`) — türetilmiş dosya

Higgs çalışma anında wav'ı **okumaz**: `HIGGS_REF_CODES`
(`/opt/higgs-tts/refs/default-ref.codes.pt`, 13 KB) önceden hesaplanmış kodları
tutar. Kod dosyası **silinirse servis açılışta wav'dan yeniden üretir** ve aynı
yere yazar (28 Tem'de ölçülerek doğrulandı — yeniden üretilen kodlar öncekiyle
**byte-birebir aynı** çıktı, `sha256` eşleşti).

Yeniden üretmek için:

```bash
ssh root@192.168.0.25 'rm /opt/higgs-tts/refs/default-ref.codes.pt && \
  systemctl restart higgs-tts && journalctl -u higgs-tts -n 20 --no-pager'
# log satırı: "referans kodlandı: <wav> (N kare, X sn) → <codes.pt>"
```

Yani **kritik olan wav'dır**; kodlar her zaman ondan geri gelir.
