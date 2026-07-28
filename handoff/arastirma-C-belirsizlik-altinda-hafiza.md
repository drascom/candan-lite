# ARAŞTIRMA C — Kimlik belirsizken hafıza: atfetme ve veri kaybı

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

**SALT-OKUNUR / TASARIM.** Kod değiştirme, deploy etme, sunucuya yazma.
Çıktın bir **tasarım önerisi**.

## Önce codebase-memory

`codebase-memory-mcp` ile başla (`search_graph`, `trace_path`, `get_code_snippet`).
İndeks yoksa `index_repository`. Grep/Glob sonra.

## Kanıtlanmış hata — 28 Tem 16:26, canlı

```
16:26:25  [Ayhan]       memory_add scope=private  → "Kaydedildi (private)."
16:26:33  [Ayhan]       memory_add scope=family   → "Taşındı/güncellendi → family"
16:26:51  [Bilinmeyen]  memory_add scope=family   → "guest: hafıza yok, kaydedilmedi"
16:26:55  Candan: "Kaydedemedim, seni henüz tanımıyorum."
```

Aynı kişi, aynı konuşma, 18 saniye arayla. "Evimde Havi ve Neva ile beraber yaşıyorum,
bunu aileye kaydet" isteği, sırf o turun ses etiketi düştüğü için **sessizce çöpe gitti**.

Ayrıca 16:25:05-16:25:10'da bir sözlü onay turu geçti ("sen Ayhan mısın?" → "evet" →
"artık sesini daha iyi tanıyacağım") ama bu onay **kalıcı olmadı** — 90 saniye sonra
hâlâ guest.

## Cevaplanacaklar

1. **Yolu çıkar:** `memory_add` çağrısı kimliği nereden okuyor? "guest" kararı hangi
   `dosya:satır`'da veriliyor? Turun ses etiketi ile hafıza atfetme aynı değişkeni mi
   kullanıyor?
2. **Sessiz düşüş nerede?** Kayıt reddedildiğinde kullanıcıya ne söyleniyor, istek
   nereye gidiyor? Kaybolan istek için kuyruk/yeniden deneme var mı? (Kanıta göre YOK.)
3. **Sözlü onay neden kalıcı olmuyor?** 16:25:10'daki "artık sesini daha iyi
   tanıyacağım" ne yapıyor — gerçekten bir şey yazıyor mu, yoksa sadece cümle mi?
   `dosya:satır` ile göster.

## Tasarla — asıl iş

Kullanıcının koyduğu çerçeve (birebir uy):
* Tehdit modeli zayıf: ev, aile. **Güvenlik kaygısı değil.**
* Asıl risk: *"hafızaya alınacak işlemler konusunda karışıklık yaratabilir"* —
  yani yanlış kişiye atfedilen kayıt.
* Yapışkan kimlik masada, ama bu riski üretiyor.

Şunu çöz: **kimlik belirsizken hafıza isteği ne olmalı?** Seçenekleri değerlendir:

* **Ertelenmiş atfetme:** isteği kaydet ama "atfedilmemiş" olarak beklet; oturumun
  ilerleyen turlarında kimlik netleşince geriye dönük ata. (Oturum içi kümeleme —
  Araştırma B'nin 5. maddesiyle bağlantılı.)
* **Sor, atma:** "Bunu Ayhan olarak mı kaydedeyim?" — tek soruyla çöz. Kullanıcı zaten
  bunu bekliyor; sessiz kayıp en kötü seçenek.
* **Oturum kilidi:** oturumda kimlik bir kez güvenle kurulunca o oturum boyunca sabitle;
  yeni bir SES kümesi belirene kadar bozma.
* **Geri alınabilirlik:** yanlış atfetme olursa "bunu ben demedim" ile düzeltilebilsin.
  Yapışkanlığı kabul edilebilir kılan şey bu — hata pahalı değilse yapışkanlık ucuzlar.

Her seçenek için: kullanıcı deneyimi, yanlış atfetme riski, uygulama maliyeti.

**Değişmez kural olarak öner:** hiçbir hafıza isteği sessizce düşmemeli. Ya yazılır,
ya sorulur, ya beklemeye alınır — ama sessizce atılmaz.

## Kısıtlar

* Kod YAZMA (bu tur uygulama yok). Deploy YOK. Canlı `.25`'e yazma.
* Kullanıcının gerçek hafızasına (`memory/`, aile hafızası) **YAZMA**. Okuman gerekirse
  yapısını oku, içeriğini rapora KOPYALAMA — kişisel/aile verisi, depo PUBLIC.
* DEVIR §7'deki uyarı: hafıza uzantısı kökü `MEMORY_DIR`'den değil ÇALIŞMA DİZİNİNDEN
  çözüyor; 27 Tem'de bu yüzden gerçek hafızaya test notu yazılmış. Dikkat et.

## Rapor (KISA — 20 satırı geçme)

* `memory_add` kimlik yolu (`dosya:satır`) ve "guest" kararının yeri
* Sözlü onay neden kalıcı olmuyor (`dosya:satır`)
* Seçenek karşılaştırması (tablo)
* **Önerin:** hangi tasarım, tek paragraf gerekçe
* Hemen yapılabilecek en küçük düzeltme (sessiz kaybı durduran) — kodu yazma, yerini göster
