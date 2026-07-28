# Görev — Onay döngüsü zehirlenmesi + tanıdığı hâlde inkâr (pi_brain.py)

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

## ⛔ DOSYA SAHİPLİĞİ

Paralel worker `pi/extensions/family-memory/index.ts` ve `worker/truth_check.py`
üzerinde çalışıyor. **O İKİ DOSYAYA DOKUNMA.**
Senin dosyan: **`worker/pi_brain.py`** (gerekirse `worker/.env.example`).

## Önce codebase-memory

`codebase-memory-mcp` ile başla. İndeks yoksa `index_repository`. Grep/Glob sonra.

## DÜZELTME 1 — onay döngüsü yanlış tetikleniyor ve profili ZEHİRLİYOR (ACİL)

**Kanıt (canlı, 28 Tem 18:06, kaydın sonu):**
```
18:06:45  [Bilinmeyen] "Neden ayırt edemiyor sesi? Konuşuyorum bir saattir."   ← YAKINMA
18:06:46  Candan: "Pardon,"
18:06:47  [Bilinmeyen] "Evet.  Evet."
18:06:47  Candan: "Tamam, artık sesini daha iyi tanıyacağım."                   ← ÖĞRENDİ
```
**Veritabanı kanıtı:** `speakers.db`'ye o an **Havi** profiline 2 `confirmed-learn`
örneği yazıldı (id 35, 36 — `2026-07-28 17:06:47 UTC`). Konuşan muhtemelen Ayhan'dı.
Havi'nin toplam 11 örneği var → **%18'i şüpheli**.

**Neden ağırlaştı:** bugün `_confirm_learn`'e eklediğimiz `current` set etme
(commit `314fd22`) doğru tetiklendiğinde faydalı, YANLIŞ tetiklendiğinde hem
`speakers.db`'yi kirletiyor hem oturumu yanlış kimliğe bağlıyor.

**Yap:**
* Onay sorusunun ne zaman sorulacağını ve "evet"in ne zaman onay sayılacağını SIKILAŞTIR.
  Bir yakınma/serbest cümle onay bağlamı açmamalı; "Evet" ancak **açıkça sorulmuş bir
  kimlik sorusunun hemen ardından** ve dar bir zaman penceresinde onay sayılmalı.
* Onay bağlamının bir **ömrü** olsun (soru sorulduktan sonra N saniye / N tur),
  süre geçince "evet" onay saymasın.
* Onaydan öğrenme (`speaker_samples`'a yazma) için ek kapı: o turun ses kanıtı
  yeterince güçlü değilse **ÖĞRENME** — kimliği set etmek ayrı, profile örnek yazmak
  ayrı olmalı. Zayıf kanıtla profile YAZMA.
* ⚠️ `_enroll_new` / `_merge_into` / `_confirm_learn` üçlüsünün davranışını karşılaştır;
  düzeltme hepsini tutarlı bıraksın.

## DÜZELTME 2 — tanıdığı hâlde inkâr ediyor (benim atladığım düzeltme)

**Kanıt (canlı, 28 Tem 17:51):**
```
17:51:33  [Ayhan] "Selam Candan. Sesim geliyor mu?"        ← ETİKET: Ayhan
17:51:47  [Ayhan] "Direkt beni sesinden tanıyabildin mi?"  ← ETİKET: Ayhan
17:51:51  Candan: "teknik olarak hala sesini sisteme tam olarak tanıtmadık...
                   sistemimdeki kayıtlarla henüz eşleşme yapamadığım için..."
```
Ses eşleşmesi TUTMUŞ (etiket Ayhan), model yine inkâr ediyor.

**Kök sebep (Araştırma C):** `_identity_note` (`:4477-4507`) `:4486-4487`'de
`if self._enroll_active: return text` ile "bu ses X ile eşleşti" sinyalini TAMAMEN
düşürüyor. İsim taşıyan yollar (`_maybe_greet` `:4458`, `_personal_memory_note` `:4509`)
guard'sız çalışmaya devam ediyor → context'te isim VAR, "tanındı" YOK.
Ayrıca `_enroll_hint` (`:4095`) ilk turda geçmişe "Bu sesi TANIMIYORSUN" yazıyor ve
süreç sıcak olduğu için o metin sonraki turlarda da duruyor.

**Yap:**
* `_identity_note`'un guard'ını `_enroll_active` yerine **kimlik gerçekten bilinmiyorsa**
  (`current is None`) sussacak şekilde değiştir.
* `current` dolduğu anda `_enroll_hint`'in "TANIMIYORSUN" bağlamı **geçersiz kılınsın**
  — yoksa geçmişteki kopya çelişkiyi sürdürür.
* **Prompt satır sayısını şişirme** (DEVIR'de bu uyarı var).

## DÜZELTME 3 — yanlış kimlik adayı yayınlanıyor

**Kanıt (canlı, 17:56:30):** Ayhan konuşurken sistem "**Havi** olarak mı kaydedeyim?"
diye sordu. Havi o sırada odada bile DEĞİLDİ (ilk kez 17:59:40'ta geliyor).
`set_identity_candidate(decision.candidate)` yanlış aday vermiş.

**Yap:** aday yayınlamanın bir güven eşiği olsun. Zayıf/çelişkili adayı yayınlama —
aday yoksa `None` yayınla (paralel worker "aday yoksa genel sor" dalını zaten kuruyor).
Yanlış aday önermek, aday önermemekten KÖTÜDÜR: dalgın bir "evet" yanlış kişiye yazar.

## Kısıtlar

* **Deploy YOK. `systemctl` YOK. Canlı `.25`'e YAZMA.**
* Gerçek `speakers.db`'ye YAZMA/SİLME (temizlik ayrı iş, kullanıcı onayı bekliyor).
* **Görsel/canlı test YAPMA.** Test suite'i koştur.
* Şu an **426 test** geçiyor. DÜŞMESİN. Yeni davranışlar için test EKLE —
  özellikle "yakınma cümlesi onay sayılmamalı" ve "zayıf kanıtla profile yazılmamalı".
* `./check.sh` — kendi dosyanda temiz olsun. HEAD'de zaten var olan 4 ruff bulgusu senin değil.
* Env okuma tuzağı (DEVIR §7, `c9d0d27`): modül seviyesinde okuma YOK.
* Commit at (main'de kal), **PUSH ETME**. Üç ayrı commit.
* `index.ts` ve `truth_check.py`'ye DOKUNMA. `git stash` KULLANMA.

## Rapor (KISA — 12 satır)

* Düzeltme 1: onay artık hangi koşulda sayılıyor, öğrenme kapısı ne
* Düzeltme 2: guard nasıl değişti, `_enroll_hint` bağlamı nasıl iptal ediliyor
* Düzeltme 3: aday eşiği ne
* Eklenen testler (özellikle yakınma-onay-değil testi)
* Test sayısı önce/sonra, `./check.sh`, commit hash'leri
