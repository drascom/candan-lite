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

## 2. DOĞRULAYAMADIKLARIM — BUNLAR SENDE

Mikrofonum ve kulağım yok. Aşağıdakileri **hiç** test edemedim:

| Ne | Nasıl test edersin | Beklenen |
|---|---|---|
| **Not gerçekten kaydediliyor mu** | "Candan, şunu not et: ..." de, sonra "az önce ne not ettim?" diye sor | Kaydetmeli ve geri okumalı |
| **Kaydedemezse dürüst mü** | Tanınmadığın bir durumda not aldır | *"Kaydedemedim, seni henüz tanımıyorum."* — "kaydettim" DEMEMELİ |
| **Tanıma oranı** | Normal konuş | Deploy sonrası 8 turda 5 tanıma, "pencere yok" hatası sıfırlandı — ama örneklem küçük |
| **Mod anahtarı** | "Dev moda geç" → sonra "normal moda dön" | İstediğin yöne gitmeli; ters yön no-op |
| **Compaction** | Uzun konuş, sıkıştırma tetiklensin | Soru kaybolmamalı, cevap sıkıştırma bitince gelmeli |
| **Ses kalitesi** | Dinle | Referans geri alındı, eski hızında olmalı |
| **Tur bölünmesi** | Cümle ortasında dural | Bölmemeli |

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
