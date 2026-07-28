# Görev — 8 sn tavanı düzeltmesi + gölge embedding kaydedici

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

## ⛔ DOSYA SAHİPLİĞİ

Paralel başka bir worker `worker/pi_brain.py`, `pi/extensions/family-memory/index.ts`
ve `worker/truth_check.py` üzerinde çalışıyor. **O ÜÇ DOSYAYA DOKUNMA.**
Senin dosyaların: `worker/speaker_tap.py`, `worker/speaker_id.py` (+ gerekirse
`worker/.env.example`).

## Önce codebase-memory

`codebase-memory-mcp` ile başla. İndeks yoksa `index_repository`. Grep/Glob sonra.

## İŞ 1 — `TURN_MAX_SECONDS` tavanı en güçlü kanıtı çöpe atıyor

**Kanıt (canlı, 28 Tem 16:25:05):**
> 15 pencere, **hepsi Ayhan**, ortalama skor 5.69 → karar: **Bilinmeyen**
> Sebep: run 15 sn > `TURN_MAX_SECONDS=8`

Ölçüm bunu genel bir desen olarak doğruluyor: pencere sayısı ≥7 olan turlarda başarı
%84'ten **%43'e düşüyor** — yani uzun konuşmak tanınma şansını AZALTIYOR. Tasarımın
kendisiyle çelişiyor.

**Yap:** tavan aşıldığında turun topladığı kanıt ATILMASIN. Tavanın asıl amacı
(çok eski/bayat pencereyi karara sokmamak) korunsun ama çözüm "hepsini at" olmasın —
örneğin son N saniyelik pencereleri kullan, ya da tavanı kayan pencereye çevir.

⚠️ **Tavanın neden konduğunu önce ANLA** (`speaker_tap.py:221-300`, git geçmişi).
Körlemesine kaldırma — bayat pencere karışması gerçek bir risk. Amacı koruyan en küçük
değişikliği yap ve raporda gerekçesini yaz.

## İŞ 2 — gölge embedding kaydedici (ölçümün ön koşulu)

**Neden:** pencere embedding'leri hiçbir yere yazılmıyor, sadece RAM'de duruyor ve her
`begin_turn()`'de siliniyor (`speaker_tap.py:137,148`). Reddedilen pencerelerin skorları
`log.debug`'a gidiyor, varsayılan seviye INFO (`agent.py:132`) → diske düşmüyor.
Sonuç: "komşu pencereyle karşılaştırma" fikrini geçmiş veride deneyemiyoruz.

**Yap:** oturum başına `worker/data/session-emb/<oturum-id>.npz` yaz.
Pencere başına: zaman damgası, tur numarası, embedding (**float16**), en iyi eşleşen
isim + skor, ikinci en iyi isim + skor, pencere süresi, RMS. **Reddedilen pencereler de
yazılsın** — asıl değerli veri onlar.

Kısıtlar:
* **Karar mantığına DOKUNMA.** Bu salt gözlem. Hızlı yolun davranışı bit düzeyinde aynı kalmalı.
* **Gerçek zamanlı yolu BLOKLAMA** — tamponla ve tur sonunda/periyodik yaz.
  Diske yazma gecikmesi konuşmaya yansımasın.
* Yeniden embed etme — hızlı yolun zaten ürettiği vektörü tekrar kullan.
* `.env` ile kapatılabilsin (varsayılan AÇIK). Anahtar adını mevcut isimlendirmeye uydur.
* ⚠️ **Env okuma tuzağı:** modül seviyesinde `os.environ` okuma = `.env` ETKİSİZ
  (DEVIR §7; bu depo bir kez yandı, `c9d0d27`). Fonksiyon içinde çağrı anında oku,
  varsayılan argümana bağlama.
* **TTL 7 gün** — eski dosyaları temizleyen bir yol koy (açılışta süpürme yeterli).

### ⚠️ BİYOMETRİK VERİ — en kritik kısıt

Embedding biyometrik veridir, depo **PUBLIC**.
* `worker/data/` `.gitignore`'da olduğunu **DOĞRULA** (`git check-ignore -v` ile).
  Değilse ekle. Yazdığın yol kesinlikle ignore kapsamında olmalı.
* Embedding'i log'a, transcript'e, rapora **YAZMA**. Ham ses kaydetme.
* Dosya sunucuda kalır, repoya asla kopyalanmaz.

## Kısıtlar

* **Deploy YOK. `systemctl` YOK. Canlı `.25`'e YAZMA.**
* Gerçek `speakers.db`'ye yazma.
* **Görsel/canlı test YAPMA** — kullanıcı kendi yapar. Sen test suite'i koştur.
* Şu an **413 test** geçiyor. Sayı DÜŞMESİN. `./check.sh` temiz geçsin.
* Commit at (main'de kal), **PUSH ETME**. İki ayrı commit: tavan düzeltmesi, kaydedici.
* `worker/pi_brain.py`, `pi/extensions/family-memory/index.ts`, `worker/truth_check.py`'ye
  DOKUNMA.

## Rapor (KISA — 12 satır)

* Tavanın ORİJİNAL amacı ne (git geçmişinden), nasıl korudun
* Tavan düzeltmesi: hangi satır, davranış nasıl değişti
* Kaydedici: dosya yolu, alan listesi, oturum başına tahmini boyut
* `git check-ignore` çıktısı — biyometrik yol gerçekten ignore'da mı
* Gerçek zamanlı yolu bloklamadığının gerekçesi
* `.env` anahtarı + varsayılan, env tuzağından nasıl kaçındın
* Test sayısı önce/sonra, `./check.sh`, commit hash'leri
