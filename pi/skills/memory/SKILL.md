---
name: memory
description: Kalıcı hafıza kaydet/ara. Kullanıcı hatırlanmasını istediği bir şey
  söylediğinde, önemli kalıcı bir gerçek öğrendiğinde veya geçmişe dair soru
  sorduğunda kullan.
---

# Hafıza kuralları

Kimliğin: `$MEM_USER` ortam değişkenindeki kullanıcı adına çalışıyorsun.
Boot'ta aktif kullanıcı ve hafıza yolu sana açıkça verildi; o yolu kullan.
`$MEM_USER` boşsa (guest) hafıza YOK — yazma, arama, dosya açma.

## Yazma
Yazma için `memory_add` tool'unu kullan (elle grep/append/dosya-yazma YOK).
- VARSAYILAN: özel. `memory_add({ text: "<tek satırlık gerçek>" })` → kullanıcının
  kendi notlarına (private) yazar.
- Aile ortak hafızası: `memory_add({ text, scope: "family" })`.
  Kullanıcı açıkça isterse ("aileye not et", "herkes bilsin") doğrudan yaz.
- **İçerik ailenin ORTAK hayatını ilgilendiriyorsa** — ailece etkinlik, herkesi
  ilgilendiren plan/tarih, ev ile ilgili işler, ortak alışveriş/ziyaret — sessizce
  private'a YAZMA: **tek cümlelik KISA soru sor**, sonra cevabına göre kaydet.
  - "yarın akşam ailece yemek yiyeceğiz, not al" → *"Bunu aile notuna mı yazayım,
    yoksa sana özel mi kalsın?"* → cevabına göre `scope: "family"` veya private.
  - "cumartesi diş randevum var" → kişisel, SORMA, private'a yaz.
  - Şüpheliyse SOR. Soru bir cümleyi geçmesin (sesli konuşma).
- Kendi kararınla asla özel bilgiyi ortak hafızaya taşıma; onay olmadan `family` YOK.
- Proje notu (yalnız yetişkin): `memory_add({ text, scope: "project:<ad>" })`.
- Başka kullanıcının hafızasına yazamazsın; tool zaten kimliğine göre yazar.

## Düzeltme / taşıma (yeni kayıt EKLEME)
Kullanıcı bir notun **yerini veya içeriğini düzeltirse** ("hayır, bunu aile notuna yaz",
"orayı şöyle değiştir", "yanlış yazmışsın") → **YENİ kayıt ekleme, TAŞI/GÜNCELLE**:
- `memory_add({ text: "<yeni/aynı not>", scope: "family", replaces: "<eski notun metni>" })`
  → eski kayıt SİLİNİR, yenisi hedef kapsama yazılır. Aynı not iki yerde kalmaz.
- `replaces` metni yaklaşık olabilir (diakritik/büyük-küçük harf önemsiz).
- Metin birebir aynıysa `replaces` şart değil: aynı not başka kapsamdaysa tool zaten
  kopyalamaz, **taşır** (eskisini siler).
- Tekrar: aynı/çok benzer not zaten varsa tool tekrar EKLEMEZ ("Zaten kayıtlı" döner) —
  bunu hata sanma, kullanıcıya "zaten not almıştım" de. Aynı şeyi ikinci kez kaydetmeye çalışma.

## Arama
Arama için `memory_search` tool'unu kullan.
- Boot'ta yüklü olan (profil + aile) yetmezse:
  `memory_search({ query: "<anahtar>", limit: 5 })`. Yalnız senin erişebildiğin
  kapsamı (kendi private + family + [yetişkinsen] projeler) döner; Türkçe
  diakritik-duyarsız (çocuk↔cocuk). Başka kullanıcının notu çıkmaz.
- Kısa cevap ver; sonucu olduğu gibi okuma, sesli yanıt için özetle.

Politika (default-private, aile-yalnız-açık-istekte, izolasyon) tool'larda
zorlanır; bu kurallara uy. `$MEM_USER` boşsa (guest) tool'lar "hafıza yok" döner.
