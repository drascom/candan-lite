# Görev — Kimlik/hafıza küçük düzeltmeleri (pi_brain + family-memory)

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

## ⛔ DOSYA SAHİPLİĞİ

Paralel başka bir worker `worker/speaker_tap.py` ve `worker/speaker_id.py` üzerinde
çalışıyor. **O İKİ DOSYAYA DOKUNMA.** Senin dosyaların:
`worker/pi_brain.py`, `pi/extensions/family-memory/index.ts`, `worker/truth_check.py`.

## Önce codebase-memory

`codebase-memory-mcp` ile başla (`search_graph`, `get_code_snippet`, `trace_path`).
İndeks yoksa `index_repository`. Grep/Glob sonra.

## Düzeltme 1 — sözlü onay oturumu bağlamıyor (TEK SATIR)

**Kanıt (canlı, 28 Tem):** 16:25:05'te Candan "sen Ayhan mısın?" sordu, kullanıcı
16:25:10'da "evet ben Ayhan'ım" dedi, Candan "artık sesini daha iyi tanıyacağım" dedi.
90 saniye sonra hâlâ guest'ti ve hafıza yazımı reddedildi.

**Sebep:** `_confirm_learn` (`pi_brain.py:4671-4714`) o turun pencerelerini
`speaker_samples`'a yazıp centroid'i tazeliyor ama **`self._speaker_state.current`'ı
ASLA set etmiyor.** Kardeş fonksiyonlar ediyor: `_enroll_new` (`:4380`),
`_merge_into` (`:4447`). Sadece bu yol atlanmış.

**Yap:** `:4702` civarında (`if wrote:` bloğu) onay başarılıysa `current`'ı da set et.
Kardeş fonksiyonların kullandığı deseni birebir taklit et — yeni bir stil icat etme,
onların set ederken yaptığı yan işlemleri (varsa zaman damgası, log) atlama.

## Düzeltme 2 — hafıza notu sessizce kayboluyor

**Kanıt (canlı, 28 Tem 16:26):**
```
16:26:25  [Ayhan]       memory_add private → "Kaydedildi"
16:26:33  [Ayhan]       memory_add family  → "Taşındı/güncellendi"
16:26:51  [Bilinmeyen]  memory_add family  → "guest: hafıza yok, kaydedilmedi"
```
"Evimde Havi ve Neva ile beraber yaşıyorum, bunu aileye kaydet" isteği çöpe gitti.
Aynı kişi, 18 saniye sonra.

**Sebep:** `pi/extensions/family-memory/index.ts:345-348` — `memUser()` boş/guest ise
erken `return`, not hiçbir yere yazılmıyor. Kuyruk/yeniden deneme YOK.

**Yap — değişmez kural: hafıza isteği ya yazılır, ya sorulur, ya beklemeye alınır;
ASLA atılmaz.**
* Notu `memDir(ctx.cwd)/pending/unattributed.md`'ye ekle (zaman damgası + istenen scope
  + metin). `isError: true` ile "kimlik yok, beklemede" dön.
* `worker/truth_check.py:98,106`'daki kullanıcıya giden metni buna uydur:
  "Kaydedemedim, seni henüz tanımıyorum" yerine kimden geldiğini çıkaramadığını söyleyip
  **onay isteyen** bir cümle ("Bunu Ayhan olarak mı kaydedeyim?" gibi — ismi uydurma,
  eldeki adayı kullan, aday yoksa genel sor).
* ⚠️ `pending/` dosyası kişisel veri tutar → **repoya girmemeli.** `.gitignore`'da
  `/memory/` zaten var; yazdığın yolun onun altında kaldığını DOĞRULA, kalmıyorsa
  `.gitignore`'a ekle.
* ⚠️ DEVIR §7 uyarısı: hafıza uzantısı kökü `MEMORY_DIR`'den değil ÇALIŞMA DİZİNİNDEN
  çözüyor. Test ederken gerçek hafızaya yazma — `MEM_DIR` ile izole et, sonra
  gerçek `memory/`'ye sızıntı olmadığını DOĞRULA ve raporda söyle.

## Kısıtlar

* **Deploy YOK. `systemctl` YOK. Canlı `.25`'e YAZMA.**
* Gerçek `speakers.db`'ye ve gerçek `memory/`'ye yazma.
* **Görsel/canlı test YAPMA** — kullanıcı kendi yapar. Sen test suite'i koştur.
* Şu an **413 test** geçiyor. Sayı DÜŞMESİN.
* `./check.sh` temiz geçsin (ruff + tsc). TypeScript değiştirdiğin için tsc önemli.
* Commit at (main'de kal, branch açma), **PUSH ETME**. İki ayrı commit: düzeltme 1, düzeltme 2.
* `worker/speaker_tap.py` ve `worker/speaker_id.py`'ye DOKUNMA.

## Rapor (KISA — 10 satır)

* Düzeltme 1: hangi satır, kardeş fonksiyon deseni takip edildi mi
* Düzeltme 2: pending yolu, gitignore durumu, sızıntı doğrulaması
* Kullanıcıya giden yeni cümle (birebir)
* Test sayısı önce/sonra, `./check.sh` sonucu
* Commit hash'leri
