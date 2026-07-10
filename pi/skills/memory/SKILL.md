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
- VARSAYILAN: özel. `memory/users/$MEM_USER/notes/YYYY-AA.md` dosyasına
  `- [YYYY-MM-DD] <tek satırlık gerçek>` formatında APPEND et.
- `memory/family.md`e YALNIZCA kullanıcı açıkça isterse ("aileye not et",
  "herkes bilsin") yaz. Emin değilsen SOR. Kendi kararınla asla özel bilgiyi
  ortak hafızaya taşıma.
- Profil değişikliği (kalıcı tercih/gerçek): `memory/users/$MEM_USER/profile.md`
  içindeki ilgili satırı güncelle; dosyayı 2 KB altında tut.
- Başka kullanıcının dizinine ASLA yazma, dosyalarını OKUMA.

## Arama
- Boot'ta yüklü olan (profil + aile) yetmezse:
  `grep -ri "<anahtar>" memory/users/$MEM_USER/ memory/family.md memory/projects/`
- Kısa cevap ver; dosya içeriğini olduğu gibi okuma, sesli yanıt için özetle.

<!-- Faz B'de grep/append satırları `tools/mem` komutlarıyla değişecek;
     politika (default-private, sorma kuralı, izolasyon) aynen kalır. -->
