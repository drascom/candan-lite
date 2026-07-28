# Görev — "Kibar kayıp": bekleyen not cevaplanıyor ama kaydedilmiyor

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

## ⛔ DOSYA SAHİPLİĞİ

Paralel worker **`worker/pi_brain.py`** üzerinde çalışıyor. **O DOSYAYA DOKUNMA.**
Senin dosyaların: `pi/extensions/family-memory/index.ts`, `worker/truth_check.py`.

## Önce codebase-memory

`codebase-memory-mcp` ile başla. İndeks yoksa `index_repository`. Grep/Glob sonra.

## DÜZELTME 1 — cevap alınıyor ama not yazılmıyor (ASIL İŞ)

Bugün eklediğimiz spool (commit `5807004`) sessiz kaybı durdurdu ama **cevabı işleyen
yol yok**. Canlı kanıt, iki kez:

```
17:56:27  memory_add → "Kimlik çözülemedi: not beklemeye alındı (pending/unattributed.md)"
17:56:30  Candan: "kimin söylediğini çıkaramadım — Havi olarak mı kaydedeyim?"
17:56:37  Ayhan:  "Hayır, Ayhan olarak kaydedeceksin."
17:56:40  Candan: "Anladım Ayhan..."                    ← memory_add ÇAĞRISI YOK
```
```
18:06:20  memory_add → "not beklemeye alındı"
18:06:22  Candan: "kimin söylediğini çıkaramadım — Havi olarak mı kaydedeyim?"
18:06:28  Havi:   "Evet."
18:06:29  Candan: "Anladım Havi. Başka bir isteğin olursa buradayım."   ← KAYIT YOK
```

Neva'nın adı ve elma kalorisi hâlâ `pending/unattributed.md`'de bekliyor.
**Sessiz kaybı kibar kayba çevirdik — kayıp devam ediyor.**

**Yap:**
* Bekleyen notu çözecek bir yol kur. En temizi: modelin çağırabileceği bir araç
  (örn. `memory_attribute_pending`) — kullanıcı "Ayhan olarak kaydet" / "evet" dediğinde
  model onu çağırsın, bekleyen not gerçek sahibine yazılsın ve `pending`'den DÜŞSÜN.
* Aracın açıklaması (`description`/`promptSnippet`) modelin ne zaman çağıracağını
  net anlatsın — mevcut araçların üslubunu taklit et.
* Birden fazla bekleyen not varsa davranışı tanımla (en son mu, hepsi mi, hangisi?).
  Basit ve öngörülebilir olsun; belirsizse en sondakini al.
* Kullanıcı reddederse ("hayır, kaydetme") not `pending`'den düşsün, çöpe değil
  **çözüldü/atıldı** olarak işaretlensin — sessizce silme.
* Bekleyen not YOKKEN araç çağrılırsa nazikçe "bekleyen not yok" dönsün, hata fırlatma.
* ⚠️ `pending/unattributed.md` formatı zaten var (`- [ISO ts] (scope=…) (kimlik=…) metin`).
  Ayrıştırmayı ona göre yap; formatı değiştireceksen geriye dönük oku.

## DÜZELTME 2 — kapsam (scope) yanlış seçiliyor

**Kanıt (canlı, 18:06:19):** Havi *"Bunu **benim** hafızama kaydet"* dedi,
araç `scope: family` ile çağrıldı. `private` olmalıydı.

Bu bir kod hatası değil, modelin kapsam seçimi — yani araç açıklamasındaki belirsizlik.
`memory_add`'in `scope` alanının açıklamasını netleştir: "benim/bana ait" → `private`,
"aile/hepimiz/ortak" → `family`. **Açıklamayı şişirme**, bir iki cümle yeter.

## DÜZELTME 3 — kullanıcıya giden cümle

`worker/truth_check.py`'deki soru cümlesi paralel worker'ın aday eşiği değişikliğiyle
uyumlu kalsın: **aday yoksa isim uydurma**, genel sor. (O worker `pi_brain.py`'de
zayıf adayı yayınlamayı kesiyor; senin tarafın `None` adayı zaten karşılıyorsa
dokunma, karşılamıyorsa düzelt.)

## Kısıtlar

* **Deploy YOK. `systemctl` YOK. Canlı `.25`'e YAZMA.**
* Gerçek `memory/`'ye YAZMA. Test `MEM_DIR` ile izole edilsin, sonra gerçek
  `memory/`'ye sızıntı olmadığını DOĞRULA ve raporda söyle (DEVIR §7 uyarısı).
* **Görsel/canlı test YAPMA.** Test suite'i koştur.
* Şu an **426 test** geçiyor. DÜŞMESİN. Yeni akış için test EKLE:
  bekleyen not → cevap → gerçekten yazıldı mı, ve red → düştü mü.
* `./check.sh` — `web/node_modules` artık kurulu, `tsc` adımı koşmalı.
  ⚠️ Not: `index.ts` tsconfig kapsamında DEĞİL; değişikliğini ayrıca elle tip kontrolünden
  geçir ve **yeni** hata olmadığını raporla.
* Commit at (main'de kal), **PUSH ETME**. İki-üç ayrı commit.
* `worker/pi_brain.py`'ye DOKUNMA. `git stash` KULLANMA.

## Rapor (KISA — 12 satır)

* Bekleyen notu çözen mekanizma (araç adı + nasıl tetikleniyor)
* Çoklu bekleyen not davranışı, red davranışı
* Scope açıklaması nasıl netleşti
* `MEM_DIR` izolasyonu + gerçek `memory/` sızıntı doğrulaması
* Eklenen testler
* Test sayısı önce/sonra, `./check.sh` (tsc dahil), yeni tsc hatası var mı
* Commit hash'leri
