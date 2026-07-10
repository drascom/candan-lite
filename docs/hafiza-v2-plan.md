> ⚠️ **TARİHSEL / GEÇERSİZ (2026-07-10).** Bu plan Hermes-çerçeveliydi. Gerçek uygulama **Pi-native**: kendi lokal extension `pi/extensions/mem/` (`memory_add`/`memory_search`), `pi-hermes-memory` KALDIRILDI. Güncel durum: `HANDOFF.md`. Bu dosya yalnız fikir/kavram referansı.

# Hafıza Sistemi v2 — Lite Plan (2026-07-10)

Kaynak: `~/work/Moduler_Cok_Kullanicili_Hafiza_Sistemi_Plani.md` (v1).
v1'in **kavramsal modeli korunur** (scope'lar, default-private, kimlik, agent-bağımsızlık),
**teknoloji yığını candan-lite gerçeğine indirgenir**: 5 servis yerine dosyalar + tek CLI.

## 1. v1'in sorunu

v1 = Memory Gateway + Mem0 + PostgreSQL + Qdrant + Redis + JWT + multi-tenant.
4-6 kişilik aile asistanı için:

- **Yavaş:** Mem0 her turda LLM'le extraction yapar (tur başına +1-2 sn + maliyet).
  Gateway + Qdrant = her hafıza erişimi ağ çağrısı. Ses asistanında gecikme = ölüm.
- **Ağır:** 5 container, şema migrasyonları, JWT altyapısı — bakım yükü tek kişilik ekibe fazla.
- **Gereksiz:** tenant hep 1 (aile). pi zaten session, skill ve system-prompt altyapısı taşıyor;
  Mem0'un yaptığını memory-skill + dosyalar yapar (Hermes'in "agent-managed memory" ilkesi zaten bu).

## 2. v2 mimari

```
ses → speaker-ID → <user>
             │
worker ──► pi spawn (warm rpc):
             --append-system-prompt  pi/AGENTS.md
             --append-system-prompt  pi/personas/<user>.md
             --append-system-prompt  memory/users/<user>/profile.md   ← YENİ
             --append-system-prompt  memory/family.md                 ← YENİ
             │
pi (tur içinde) ──► memory-skill ──► `mem` CLI ──► memory/ dosyaları
                                          │              │
                                     SQLite FTS5      git commit (audit)
```

Tek enforcement noktası: **`mem` CLI** (v1'deki Gateway'in 200 satırlık hali).
pi hafıza dosyalarına elle değil, skill→CLI üzerinden dokunur.

## 3. Kavram eşlemesi (v1 → v2)

| v1 | v2 | Neden |
|---|---|---|
| Memory Gateway (FastAPI) | `tools/mem` CLI | Aynı görevler (scope, izin, audit), sıfır servis |
| Mem0 | Markdown dosyaları + memory-skill | Agent-managed; turda LLM extraction yok |
| Qdrant | SQLite FTS5 (gerekirse sqlite-vec) | Lokal, <10 ms, sıfır kurulum |
| PostgreSQL | SQLite (tek dosya, türetilmiş) + dizin yapısı | Otoriter kaynak = dosyalar |
| Redis | — | pi süreci zaten warm; session state orada |
| JWT + Identity Resolver | speaker-ID → `MEM_USER` env | LAN içi güvenilir ortam |
| Permission Engine | dizin kuralları + mem CLI scope kontrolü + load-time seçim | |
| Audit Logger | git auto-commit | Kim/ne/ne zaman + diff bedava |
| Graphiti / Neo4j | YAGNI | İhtiyaç kanıtlanırsa Faz D |
| Multi-tenant | tek tenant = repo | İleride tenant = ayrı repo/dizin |

## 4. Dizin yapısı (otoriter kaynak)

```
memory/
  family.md                 # ortak aile hafızası — herkes boot'ta yükler (≤ 2 KB tut)
  policy.json               # roller: {"ayhan":"adult", "cocuk1":"child", ...}
  users/
    <user>/
      profile.md            # kim, tercihler, kalıcı gerçekler — boot'ta yüklenir (≤ 2 KB)
      notes/
        2026-07.md          # episodik birikim — yüklenmez, ARANIR
  projects/
    <name>.md               # proje hafızası — aranır, ilgili session'da yüklenebilir
  .index/
    mem.db                  # FTS5 indeksi (türetilmiş, gitignore)
```

- **Küçük çekirdek, aranabilir kuyruk** (Hermes ilkesi): boot'ta ~4 KB metin, gerisi FTS.
- Session memory = pi'nin kendi `sessions/<user>/` JSONL'i (zaten var, dokunma).

## 5. Gizlilik ve yetki

- **Varsayılan: private.** Yeni bilgi `users/<user>/notes/`e yazılır.
- family/project'e yazma → mem CLI `--shared` bayrağı ister; memory-skill bu bayrağı
  **yalnızca kullanıcı açıkça isteyince** ("aileye not et", proje oturumu) kullanır.
  Agent kendi kararıyla private→shared taşıyamaz (v1 kural 6 aynen).
- Okuma scope'ları (mem CLI hesaplar, `MEM_USER` + `policy.json`):
  - `adult` → kendi private + family + tüm projeler
  - `child` → kendi private + family
  - `guest` → hiçbiri (yalnız session; profile/family yüklenmez, guest persona)
- Load-time seçim: worker yalnızca o kullanıcının profile'ını enjekte eder;
  başkasının profile'ı sürece hiç girmez.

## 6. Okuma akışı (progressive)

1. **Boot (0 gecikme):** persona + profile.md + family.md — system prompt'ta hazır.
2. **Talep üzerine:** pi memory-skill ile `mem search "..."` (FTS5, lokal, <10 ms).
3. **Session içi:** pi'nin kendi konuşma bağlamı.

Turda **hiç ağ çağrısı yok** — v1'deki 6 aşamalı arama zinciri tek SQL sorgusuna iner
(scope filtresi WHERE'de).

## 7. Yazma akışı (2 an + konsolidasyon)

1. **Tur içi (anında):** kullanıcı önemli bir şey söylerse pi skill ile `mem add` çağırır.
2. **Session finalize:** bağlantı koparken worker pi'ye "kalıcı not çıkar" prompt'u yollar
   → 3-5 madde `notes/YYYY-MM.md`e eklenir.
3. **Gece konsolidasyonu (offline):** cron, pi'yi headless çalıştırır:
   notes'u tara → profile.md'yi güncelle, tekrarları birleştir, çelişkide yeniyi tut
   (eskiyi tarihiyle düş). v1'deki dedup/conflict/importance pipeline'ı **turdan çıkarılıp
   geceye taşınır** — hız buradan gelir. (Hermes learning loop'un lite hali.)

## 8. Agent-bağımsızlık ve motor değişimi

- Ortak API = **`mem` CLI arayüzü** (`add / search / reindex / export`). pi, Hermes,
  başka bir agent veya insan aynı komutu kullanır.
- Motor değişimi: CLI arayüzü sabit kalır; arkası dosya+FTS → HTTP servis (Faz D)
  olduğunda hiçbir skill/adapter değişmez (v1'in "değiştirilebilir motor" şartı).

## 9. Fazlar

- **Faz A — Çekirdek (yarım gün, kod ~10 satır):** dizin + tohum profile/family +
  worker'da 2 ek `--append-system-prompt` + memory-skill v0 (politika + dosyaya append).
- **Faz B — Arama ve finalize (1 gün):** `mem` CLI (add/search/reindex, scope enforce),
  FTS5 indeks, session finalize, git auto-commit.
- **Faz C — Kalite:** gece konsolidasyonu, dedup/çelişki, sqlite-vec (FTS yetmezse),
  basit yönetim (mem list/edit → dosyayı aç).
- **Faz D — Gerekirse:** mem'i HTTP servise sarma (aynı API), remote deploy, multi-tenant,
  Graphiti. **v1 planı bu fazın tasarım dokümanıdır** — çöpe gitmedi, ertelendi.

## 10. Test senaryoları (v1'in 9'unun karşılığı)

1. ayhan özel bilgi söyler → `memory/users/ayhan/notes/2026-07.md`e düşer
2. başka kullanıcıyla `MEM_USER=x mem search` → bulamaz
3. "aileye not et" → `family.md`e yazılır (`--shared` yolu)
4. diğer yetişkin `mem search` → bulur; `child` rolü private'ları göremez
5. iki session izolasyonu → pi `--session-id` zaten ayırıyor
6-7. agent-bağımsızlık → mem CLI'yi elle/ikinci agent'la çağır → aynı sonuç
8. güncelleme → `git log -p memory/` değişikliği gösterir (audit)
9. silme/export → `mem export <user>` = dizin kopyası; silme = dosya sil + commit

Detaylı uygulama: `docs/hafiza-implementasyon-rehberi.md`.
