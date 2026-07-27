# GECE RAPORU — 26/27 Temmuz

**Hepsi canlıda ve doğrulandı.** İki servis (`candan-worker`, `pi-service`) `active`,
log'da traceback yok, 179 test geçiyor, deploy edilen 9 dosyanın md5'i sunucuyla birebir.

Bu rapor üç bölüm: (1) yapılanlar, (2) **doğrulayamadıklarım — sende**, (3) açık kalanlar.

---

## 1. YAPILANLAR

### En önemli bulgu: hafıza sorununun konuşmacı tanımayla İLGİSİ YOKTU

Gece dört ayrı hafıza hatası çıktı, **dördü de ayrı sebeplerden**:

| # | Hata | Kök neden |
|---|---|---|
| 1 | Dev modda hafıza tamamen kapalı | `pi_broker.py:99` → dev oturumda `MEM_USER=""` (bilinçli eski karar) |
| 2 | **Normal modda da kapalı** | Ortak odada `MEM_USER` süreç ömürlü, `"candan"` yazıyor, policy'de yok → guest |
| 3 | Model hafıza talimatını hiç görmüyor | Ortak odada `MEMORY_NOTE` boot'ta verilmiyordu |
| 4 | Araç "kaydedilmedi" derken model "kaydettim" diyor | Ölçülmüş: 26B'de **10/10 uydurma** |

Tanıma düzeltmesi (aşağıda) kendi başına doğru bir işti ama **notların kaybolma sebebi o
değildi.** Gece boyunca bunu birkaç kez yanlış teşhis ettim, düzeltmeleri §"Düzelttiğim
kendi hatalarım"da.

### Deploy edilen düzeltmeler

| İş | Commit | Ne yapıyor |
|---|---|---|
| **Konuşmacı tanıma** | `644c2fe` | VAD yeniden tetiklendiğinde kanıt tamponu siliniyordu → tur sınırı artık final transkript |
| **`ayhan.md` kaldırıldı** | `be00d5e` | Sen tanındığında `candan.md` HİÇ yüklenmiyordu; bu iskelet dosya onun yerine geçiyordu |
| **Araç-gerçeği kuralı** | `669ee8d` | `pi/AGENTS.md`: "tool'un DÖNÜŞÜ tek gerçektir" |
| **Dev personası hafızası** | `74a151d` | `memory/personas/dev/`, kimlik `dev`, rol `child`, çift yönlü izolasyon |
| **Ortak-oda kimliği** | `e5bf57a` | Kimlik tur ömürlü hale getirildi; worker atomik dosyaya yazar, uzantı okur |
| **Harness doğrulama** | `08cd557` | `truth_check.py` — üç katman |
| **Mod yönü + compaction** | `bdd92e2` | Ters mod geçişi engellendi; kaybolan soru yeniden gönderiliyor |

### Harness doğrulama katmanı (senin istediğin şey)

İlke: **model NE YAPILACAĞINA karar verir, harness NE OLDUĞUNU söyler.**

- **Katman 1** — tur defteri: her `toolResult` + `isError`. (`memory_add` guest'te `isError`
  OLMADAN `"kaydedilmedi."` dönüyor → metin işaretleri de okunuyor.)
- **Katman 2** — kritik yazma hata dönerse **modelin cümlesi canlıya ÇIKMAZ**; harness
  deterministik cümleyi söyler: *"Kaydedemedim, seni henüz tanımıyorum."* Mod çelişkisi de
  deterministik kapatıldı.
- **Katman 3** — araç sonucu olmayan iddialar için sınıflandırıcı (ölçüm: **84-130 ms**,
  doğruluk 7/8). **Yalnız** deterministik katmanlar yakalamazsa VE turda hata varsa çağrılır.
  Tipik turda hiç çağrılmadığı testle kanıtlı (`assert_not_called`).

Guard yalnız hata yolunda açılır → **normal turda tamponlama ve gecikme YOK.**

---

## 2a. UÇTAN UCA DOĞRULANDI (kullanıcı uyarısıyla — mikrofon gerekmiyormuş)

Kullanıcı haklı olarak "STT'den gelmiş gibi metin gönderip deneyebilirsin" dedi. Testleri
**gerçek pi süreci, gerçek uzantı, gerçek policy** ile koştum; hafıza izolasyonu için
`MEM_DIR` geçici köke alındı (canlı veriye dokunulmadı, doğrulandı).

### Hafıza kimliği — ortak oda (`e5bf57a`)
```
KİMLİK ayhan  → Candan: "Not ettim."                            + not YAZILDI      ✓
KİMLİK guest  → Candan: "Kaydedemedim, hafızaya erişemiyorum."  + yazılmadı        ✓
canlı hafıza  → hiç dokunulmadı                                                    ✓
```
Yani **normal modda not artık gerçekten kaydediliyor** ve tanınmayan sese karşı kapı kapalı.

### `AGENTS.md` araç-gerçeği kuralı (`669ee8d`) — ÖLÇÜLDÜ
Guest (hafıza kapalı) durumunda 6 tekrar:
```
6/6 DÜRÜST ("Kaydedemedim, hafızaya erişemiyorum.")   0/6 uydurma
```
**Doğru taban karşılaştırması** (`bench/12b/sonuclar.md` §4 — canlıdaki modelin AYNISI,
gemma-4-12b; 26B tablosu farklı model, onunla kıyaslamak yanıltıcı olurdu):

| | 12B taban (kuralsız) | kuralla (ölçtüm) |
|---|---|---|
| Hatayı kullanıcıya söyledi | **0/10** | **6/6** |
| Uydurdu | 2/10 | 0/6 |
| Sessizce tool'u yeniden çağırdı | 8/10 | — |

Taban dokümanının *"AGENTS.md'deki delik kapanmadı"* dediği açık **kapandı**.

### Mod anahtarı yönü (`bdd92e2`)
```
"Normal moda dön."                 → tool YOK, "Zaten normal moddayım."   ✓
"Bu moddan çık, normal moda geç."  → tool YOK, "Zaten normal moddayım."   ✓
"Geliştirme moduna geç."           → enter_dev_mode                       ✓
```
İkinci satır 26 Tem'de dev moduna GİRMENE sebep olan cümlenin birebir aynısı — düzeldi.

### ⚠️ Bu testlerde YAPTIĞIM HATA (düzeltildi)
İlk denemede izolasyonu `MEMORY_DIR` ile kurduğumu sandım; uzantı hafıza kökünü çalışma
dizininden çözdüğü için **test notu senin gerçek hafızana yazıldı**
(`users/ayhan/notes/2026-07.md` + `.index/mem.db`). Fark edip temizledim: not satırı
kaldırıldı, FTS indeksinden ilgili satır silindi (`integrity_check: ok`, kalan 6 satırın
hepsi senin gerçek verin), yedeklerim de silindi. **Şu an hafızada test izi yok** —
doğrulandı. Sonraki testler `MEM_DIR` ile gerçekten izole koştu.

---

## 2b. HÂLÂ DOĞRULAYAMADIKLARIM — BUNLAR SENDE

Aşağıdakiler ses yolundan geçtiği için metin enjeksiyonuyla test edilemedi:

| Ne | Nasıl test edersin | Beklenen |
|---|---|---|
| **Konuşmacı tanıma oranı** | Normal konuş | Ses yolu gerekiyor. Deploy sonrası 8 turda 5 tanıma + "pencere yok" hatası sıfırlandı — örneklem küçük, asıl ölçüm sende |
| **Compaction turu kurtarma** | Uzun konuş, sıkıştırma tetiklensin | Soru kaybolmamalı; sıkıştırma bitince cevap gelmeli. Birim testle kanıtlı ama canlıda görülmedi |
| **Ses kalitesi / hız** | Dinle | Referans geri alındı, eski hızında olmalı ("kesik kesik" geçmiş olmalı) |
| **Tur bölünmesi** | Cümle ortasında dural | Bölmemeli (26 Tem'de canlı kanıtlanmıştı, sonraki deploy'lar sonrası tekrar bakılmadı) |
| **Uçtan uca gerçek akış** | Konuş | Metin enjeksiyonu STT/VAD/TTS zincirini atlıyor; tüm zinciri ancak sen sınarsın |

Not: "not kaydediliyor mu" ve "kaydedemezse dürüst mü" sorularının ikisi de §2a'da
**uçtan uca doğrulandı** — ama pi tarafında; ses yolundan geçen hâli yine de bir kez
teyit etmeye değer.

**İlk konuşmanda logu izlemek istersen:**
```bash
ssh root@192.168.0.25 'journalctl -u candan-worker -f | grep -E "truth:|mod isteği|YENİDEN|speaker turn kararı|METİNSİZ"'
```

---

## 3. AÇIK KALANLAR

1. **`"ekledim"` yanlış-pozitifi** — kelime listesinde. `memory_add` başarılıysa zaten
   susuyor, ama "hiç tool çağrılmamış + model 'ekledim' demiş" turunda ateşler. Birkaç gün
   `journalctl | grep "truth: harness düzeltmesi"` ile izle; gürültü yaparsa tek satır silinir.
2. **Ortak odada `finalize()` / `consolidate_if_needed` kapalı** — oturum sonu özeti son
   konuşanın notlarına başkasının konuşmasını yazabilirdi diye bilerek açılmadı.
   `profile.md` şişmesi önemliyse ayrı bir iş.
3. **Ortak sohbet bağlamı sızıntısı** — Ayhan'ın profili aynı pi geçmişinde durduğu için
   modelin ağzından sızabilir. Ortak-oda tasarımından gelen MEVCUT risk; bu gece ne arttı ne azaldı.
4. **Wake-gate notu sesli okunuyor** — senin kararınla V2'ye bırakıldı. Ama not: wake'i
   kullanmasan da o talimat prompt'ta duruyor ve 26 Tem'de 12 kez seslendirildi.
5. **`memory/soul.md` yok** (ortak ruh dosyası) — `soul_add scope:family` çağrılırsa
   oluşturulacak; şu an yokluğu sorun değil.

---

## 4. DÜZELTTİĞİM KENDİ HATALARIM

Gece boyunca birkaç kez yanlış teşhis koydum, hepsini ölçümle düzelttim:

- **"Bellek sızıntısı var"** → yoktu. 120 saniyede +1.4 MB, tek seferlik model yüklemesi.
- **"`endpointing_delay` 0.3 sabit"** → adaptif. Model "bitmedi" derse 2.5 sn bekliyor.
- **"Tanıma eşiği fazla katı, gevşetelim"** → eşik değil, **hata**. VAD yeniden
  tetiklendiğinde kanıt tamponu siliniyordu. Eşik gevşetmek hiçbir şey çözmezdi.
- **"Sorun dev modundan kaynaklanıyor"** → eksik. Normal mod da bozuktu.
- **"Sınıflandırıcı GPU'yu yorar"** → abartıydı. 115 ms, üstelik sadece hata turlarında.
- **Referans kısaltma** → **geri alındı**. Ses tonunu bozmuyordu ama %16 yavaşlatıyordu ve
  kazancı yoktu: bench RTF'leri Mac'tendi, bu sunucuda RTF zaten 0.11-0.15.

---

## 5. GERİ DÖNÜŞ

Sunucudaki yedekler:
```
/opt/candan-lite/worker/pi_brain.py.bak-20260727
/opt/candan-lite/worker/pi_broker.py.bak-20260727
/opt/candan-lite/pi/extensions/family-memory/index.ts.bak-20260727
/opt/candan-lite/pi/extensions/mode-switch/index.ts.bak-20260727
/opt/candan-lite/pi/skills/memory/SKILL.md.bak-20260727
/opt/candan-lite/pi/personas/dev.md.bak-20260727
/opt/candan-lite/pi/personas/.ayhan.md.bak-20260726
/opt/candan-lite/worker/speaker_tap.py.bak-20260726b
/opt/candan-lite/pi/AGENTS.md.bak-20260726
/opt/omnivoice/default-ref.wav.bak-20260726
```
Tümünü geri almak için: yedekleri üzerine kopyala →
`systemctl restart pi-service && systemctl restart candan-worker`.

Git'te her düzeltme ayrı commit — tek tek de geri alınabilir. Hepsi push edildi.

---

## 6. ÖLÇÜLEN DEĞERLER (deploy SONRASI)

```
TTS RTF          kısa 0.46 · orta 0.25 · uzun 0.19      (gerçek zamandan 2-5× hızlı)
Sınıflandırıcı   73-130 ms
Testler          179 OK
identity.ts      12/12 PASS (sunucuda koşuldu)
Servisler        candan-worker active · pi-service active
Traceback        yok
```

**KAYBOLAN NOTUN yeri:** Home Assistant maddesi hâlâ
`handoff/2026-07-26-canli-dogrulama.md` sonunda duruyor. Hafıza çalıştığını teyit edince
oraya taşımak sana kalıyor — ben pi'nin hafızasına yazamam.

---

# EK — HIGGS TTS 3 SABAH TESTİNE HAZIR

**Tek komut:** `cd experiments/higgs-tts3 && ./serve.sh` → 4 sütun yan yana dinleme sayfası.
Sesler zaten Mac'te (117 wav), sunucuya bağlanmana gerek yok.
Ayrıntı: `handoff/2026-07-27-higgs-tts3-hazirlik.md`

## Ölçümler (aynı sunucu, aynı 29 cümle, aynı referans ses)

| | OmniVoice | higgs-default | higgs-clone | **higgs-clone-trnorm** |
|---|---|---|---|---|
| RTF ortalama | **0.305** | 0.491 | 0.516 | 0.516 |
| VRAM tepe | ~9.9 GB | 8.9 GB | 10.2 GB | 10.2 GB |
| RAM tepe | 4.3 GB | 4.2 GB | 4.2 GB | 4.2 GB |
| **ASR WER** | 0.085 | 0.076 | 0.058 | **0.028** |
| atlanan kelime | 14 | 11 | 9 | **4** |

**Takas net: Higgs ~1.7× yavaş ama Türkçe doğruluğu ~3× iyi.** İkisi de gerçek zamandan
hızlı (RTF < 1), yani yavaşlık pratikte tolere edilebilir.

## Türkçe zor vakalar — Higgs'in asıl üstünlüğü
`%25`, `3.500 lira`, `14:30'da`, `1994'te`, `%8'den`, `12.03.2026` → **ham metinden doğru
okuyor** (WER 0.000). OmniVoice bunları ancak köprünün `num2words`'ü sayesinde çözüyor ve
üstüne `750 gr` / `Sözleşme` / `$€`'da hata yapıyor. Higgs'in tek zaafı `1.250.000`, onu da
`trnorm` düzeltiyor. `higgs-clone-trnorm`'da **gerçek kelime düşmesi yok**.

Yani 25 Tem'de yazdığımız `trnorm` çöpe gitmiyor — Higgs ile birlikte de en iyi sonucu veriyor.

## Kararın önüne konması gerekenler

1. **İkisi AYNI ANDA ÇALIŞAMAZ.** Higgs 10.2 GB istiyor, şu an GPU'da 8 GB boş. Sabah
   testinde `omnivoice-bridge.service` durdurulacak. Kalıcı geçiş kararı ayrı.
2. **Hız kaybı gerçek:** RTF 0.305 → 0.516. Beyin + STT ile GPU paylaşımında bu fark büyür.
3. **Lisans: araştırma / ticari olmayan.** Kişisel ev asistanı kapsamda, ama ileride
   ticari bir şeye dönerse ayrı lisans gerekir.
4. **Çalıştırma yolu resmi değil.** `bosonai/higgs-tts-3-4b` ağırlık+config içeriyor ama
   çalıştırma kodu içermiyor; `higgs_multimodal_qwen3` mimarisi hiçbir transformers
   sürümünde yok. Ajan `multimodalart/higgs-audio-v3-tts-4b-transformers` paketlemesini
   kullandı — **ağırlıklar bayt bayt aynı** (9 309 834 930 B), üstüne 16 KB topluluk
   modeling kodu + v2 kodeki. Çalışıyor ve Türkçe çıktı temiz, AMA:
   - `trust_remote_code=True` ile üçüncü taraf kod çalışıyor — güvenlik açısından bilinçli
     bir tercih olmalı.
   - Örnekleme mantığının resmi SGLang yoluyla birebir aynı olduğu doğrulanamadı.
   Kalıcı geçiş kararı verilirse resmi SGLang-Omni yoluna geçmek daha sağlam olur.
5. **Streaming YAPILMADI.** Model destekliyor ama SGLang-Omni gerekiyor; bu gece kapsam
   dışı bırakıldı. Sesi beğenirsen ikinci adım.

## Doğrulanan sistem durumu (gece sonu)
```
omnivoice-bridge active · candan-worker active · candan-brain active · pi-service active
canlı sentez testi: RTF kısa 0.51 · orta 0.27 · uzun 0.23   → TTS çalışıyor
GPU 16593/24576 MiB · disk 149 GB boş
```
