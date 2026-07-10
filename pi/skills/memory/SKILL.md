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
- Aile ortak hafızası: YALNIZCA kullanıcı açıkça isterse ("aileye not et",
  "herkes bilsin") `memory_add({ text, scope: "family" })`. Emin değilsen SOR.
  Kendi kararınla asla özel bilgiyi ortak hafızaya taşıma.
- Proje notu (yalnız yetişkin): `memory_add({ text, scope: "project:<ad>" })`.
- Başka kullanıcının hafızasına yazamazsın; tool zaten kimliğine göre yazar.

## Arama
Arama için `memory_search` tool'unu kullan.
- Boot'ta yüklü olan (profil + aile) yetmezse:
  `memory_search({ query: "<anahtar>", limit: 5 })`. Yalnız senin erişebildiğin
  kapsamı (kendi private + family + [yetişkinsen] projeler) döner; Türkçe
  diakritik-duyarsız (çocuk↔cocuk). Başka kullanıcının notu çıkmaz.
- Kısa cevap ver; sonucu olduğu gibi okuma, sesli yanıt için özetle.

Politika (default-private, aile-yalnız-açık-istekte, izolasyon) tool'larda
zorlanır; bu kurallara uy. `$MEM_USER` boşsa (guest) tool'lar "hafıza yok" döner.
