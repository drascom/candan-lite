> ⚠️ **TARİHSEL / GEÇERSİZ (2026-07-10).** Bu plan Hermes-çerçeveliydi. Gerçek uygulama **Pi-native**: kendi lokal extension `pi/extensions/family-memory/` (`memory_add`/`memory_search`), `pi-hermes-memory` KALDIRILDI. Güncel durum: `HANDOFF.md`. Bu dosya yalnız fikir/kavram referansı.

# Hafıza v2 — İmplementasyon Rehberi

Plan: `docs/hafiza-v2-plan.md`. Bu rehber faz faz, dosya dosya ilerler.
Her fazın sonunda **kabul kriteri** var; geçmeden sonrakine başlama.

---

## Faz A — Çekirdek (yarım gün)

### A1. Dizin ve tohum dosyalar

```bash
mkdir -p memory/users/ayhan/notes memory/projects memory/.index
echo 'memory/.index/' >> .gitignore
```

`memory/policy.json`:
```json
{ "ayhan": "adult", "esra": "adult", "cocuk": "child" }
```
(İsimler speaker-ID slug'larıyla AYNI olmalı — `pi_brain._slug` çıktısı.)

`memory/family.md` (şablon — ≤ 2 KB tut, şişerse konsolidasyonda buda):
```markdown
# Aile Ortak Hafızası
<!-- Herkes boot'ta yükler. KISA tut; detay notes/'a, buraya sadece kalıcı ortak gerçekler. -->
- Ev: ...
- Ortak takvim alışkanlıkları: ...
```

`memory/users/ayhan/profile.md` (şablon):
```markdown
# ayhan — Profil
<!-- Boot'ta yüklenir. ≤ 2 KB. Sadece kalıcı gerçek + tercih; olaylar notes/'a. -->
- Rol: adult
- Tercihler: kısa cevap sever, ...
```

### A2. Worker: boot enjeksiyonu (`worker/pi_brain.py`)

`_build_pi_args` (satır 51-65) persona overlay'den SONRA hafıza çekirdeğini eklesin:

```python
MEMORY_DIR = os.environ.get("MEMORY_DIR", "memory")

def _build_pi_args(persona: str, session_id: str) -> list[str]:
    ...
    if persona_file.is_file():
        args += ["--append-system-prompt", str(persona_file)]
    # YENİ: hafıza çekirdeği (küçük çekirdek, aranabilir kuyruk)
    mem = REPO_ROOT / MEMORY_DIR
    profile = mem / "users" / persona / "profile.md"
    if profile.is_file():
        args += ["--append-system-prompt", str(profile)]
    family = mem / "family.md"
    if family.is_file() and _role(persona) != "guest":
        args += ["--append-system-prompt", str(family)]
    ...
```

`_role()` = `memory/policy.json` oku, yoksa `"guest"` döndür:
```python
import json

def _role(user: str) -> str:
    try:
        pol = json.loads((REPO_ROOT / MEMORY_DIR / "policy.json").read_text())
        return pol.get(user, "guest")
    except FileNotFoundError:
        return "guest"
```

Ayrıca `PiRpcClient` spawn'ında alt-sürece kimliği geçir (Faz B'de mem CLI bunu kullanacak):
```python
env = {**os.environ, "MEM_USER": persona}
# create_subprocess_exec(..., env=env)
```

> Not: şu an `persona == user` (speaker-ID slug'ı). Speaker-ID portu tamamlanınca da
> bu eşleme geçerli kalır; guest'te `MEM_USER` boş bırak.

### A3. memory-skill v0 (`pi/skills/memory/SKILL.md`)

Faz A'da CLI yok; skill sadece politika + doğrudan dosya append tarif eder:

```markdown
---
name: memory
description: Kalıcı hafıza kaydet/ara. Kullanıcı hatırlanmasını istediği bir şey
  söylediğinde, önemli kalıcı bir gerçek öğrendiğinde veya geçmişe dair soru
  sorduğunda kullan.
---

# Hafıza kuralları

Kimliğin: $MEM_USER ortam değişkenindeki kullanıcı adına çalışıyorsun.

## Yazma
- VARSAYILAN: özel. `memory/users/$MEM_USER/notes/YYYY-AA.md` dosyasına
  `- [YYYY-MM-DD] <tek satır gerçek>` formatında APPEND et.
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
```

(Faz B'de grep/append satırları `mem` komutlarıyla değişecek — arayüz tek noktada.)

### A4. Kabul kriterleri (Faz A)

- [ ] `python worker/pi_brain.py smoke` PASS (regresyon yok)
- [ ] `pi_brain.py prompt "ben kimim?"` → profil içeriğini biliyor (boot enjeksiyonu çalışıyor)
- [ ] "Şunu hatırla: ..." → `notes/2026-07.md`e satır düştü
- [ ] "Aileye not et: ..." → `family.md`e düştü; sormadan asla düşmüyor
- [ ] Sesli uçtan uca (kullanıcı test eder — Claude runtime testi yapmaz)

---

## Faz B — `mem` CLI + FTS5 + finalize (1 gün)

### B1. `tools/mem` (tek dosya Python, ~200 satır)

Arayüz (SABİT — Faz D'de HTTP'ye taşınsa da değişmez):
```
mem add  "<metin>" [--shared | --project <ad>]   # default: private
mem search "<sorgu>" [--limit 5]
mem reindex
mem export [<user>]
```

İskelet:
```python
#!/usr/bin/env python3
"""mem — candan-lite hafıza CLI. Tek enforcement noktası (gateway'in lite hali)."""
import argparse, datetime, json, os, sqlite3, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEM = REPO / "memory"
DB = MEM / ".index" / "mem.db"

def user() -> str:
    u = os.environ.get("MEM_USER", "").strip()
    if not u:
        sys.exit("HATA: MEM_USER tanımsız (guest hafıza kullanamaz)")
    return u

def role(u: str) -> str:
    pol = json.loads((MEM / "policy.json").read_text())
    return pol.get(u, "guest")

def allowed_read_paths(u: str) -> list[Path]:
    r = role(u)
    if r == "guest":
        return []
    paths = [MEM / "users" / u, MEM / "family.md"]
    if r == "adult":
        paths.append(MEM / "projects")
    return paths

def cmd_add(text: str, shared: bool, project: str | None):
    u = user()
    today = datetime.date.today()
    if shared:
        if role(u) == "guest":
            sys.exit("HATA: guest ortak hafızaya yazamaz")
        target = MEM / "family.md"
    elif project:
        if role(u) != "adult":
            sys.exit("HATA: proje hafızası adult gerektirir")
        target = MEM / "projects" / f"{project}.md"
    else:
        target = MEM / "users" / u / "notes" / f"{today:%Y-%m}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a") as f:
        f.write(f"- [{today}] {text.strip()}\n")
    index_file(target)          # artımlı indeks
    git_commit(u, target)       # audit
    print(f"OK → {target.relative_to(REPO)}")

def git_commit(u: str, path: Path):
    """Audit log = git geçmişi. Best-effort; hata yazmayı engellemesin."""
    try:
        subprocess.run(["git", "-C", str(REPO), "add", str(path)],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(REPO), "commit", "-q",
                        "-m", f"mem({u}): {path.relative_to(MEM)}"],
                       check=True, capture_output=True)
    except Exception as e:
        print(f"uyarı: audit commit atlandı: {e}", file=sys.stderr)
```

FTS5 şema + indeksleme (madde = satır; her `- [tarih] ...` satırı bir kayıt):
```python
SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS mem_fts USING fts5(
  content, path UNINDEXED, owner UNINDEXED, scope UNINDEXED,
  tokenize = "unicode61 remove_diacritics 2"
);"""

def scope_of(path: Path) -> tuple[str, str]:
    rel = path.relative_to(MEM)
    if rel.parts[0] == "users":    return rel.parts[1], "private"
    if rel.parts[0] == "projects": return "", "project"
    return "", "family"

def cmd_search(query: str, limit: int):
    u, r = user(), role(user())
    db = sqlite3.connect(DB)
    scopes = {"adult": ("family", "project"), "child": ("family",)}.get(r, ())
    rows = db.execute(
        """SELECT content, path FROM mem_fts
           WHERE mem_fts MATCH ?
             AND (owner = ? OR scope IN (%s))
           ORDER BY rank LIMIT ?""" % ",".join("?" * len(scopes)),
        (query, u, *scopes, limit)).fetchall()
    for content, path in rows:
        print(f"{content}   ({Path(path).relative_to(MEM)})")
```

- `remove_diacritics 2` → "cocuk" sorgusu "çocuk"u bulur (Türkçe için önemli).
- Yetki filtresi **SQL içinde** — v1'in Permission Filter aşaması tek WHERE.
- `reindex`: `memory/` altındaki tüm .md'leri sil-baştan indeksle (idempotent);
  `index_file`: tek dosyanın satırlarını sil + yeniden ekle (add sonrası artımlı).
- `export`: `memory/users/<u>/` + kullanıcının erişebildiği ortak dosyaları
  bir dizine kopyala (v1 senaryo 9).

### B2. Skill'i CLI'ye geçir (`pi/skills/memory/SKILL.md`)

A3'teki grep/append satırlarını değiştir:
```markdown
## Yazma
- Özel (varsayılan):  tools/mem add "<tek satırlık gerçek>"
- Ortak (SADECE açık istekte): tools/mem add "<gerçek>" --shared
- Proje: tools/mem add "<gerçek>" --project <ad>

## Arama
- tools/mem search "<anahtar kelimeler>"
```
Politika paragrafları (default-private, sorma kuralı) AYNEN kalır.

### B3. Session finalize (`worker/agent.py`)

Oturum kapanırken, pi sürecini öldürmeden ÖNCE tek prompt:

```python
FINALIZE_PROMPT = (
    "Oturum bitiyor. Bu konuşmadan kalıcı olarak hatırlanmaya değer şeyler varsa "
    "memory skill ile kaydet (en fazla 3-5 madde, tek satırlık gerçekler). "
    "Yoksa sadece 'yok' de. Sesli yanıt üretme."
)

async def _finalize(brain):
    try:
        await asyncio.wait_for(brain.request({"type": "prompt",
                                              "message": FINALIZE_PROMPT}), timeout=30)
    except Exception:
        pass  # finalize best-effort; kapanışı asla bloklamasın
```

Bağlama noktası: `agent.py`de session/participant disconnect callback'i
(worker'ın pi sürecini kapattığı yer). 30 sn timeout + best-effort şart —
kullanıcı sekmeyi kapatınca worker asılı kalmamalı.

### B4. Kabul kriterleri (Faz B)

- [ ] `MEM_USER=ayhan tools/mem add "test"` → dosya + git commit + indekste
- [ ] `MEM_USER=esra tools/mem search "test"` → BULAMAZ (private izolasyon)
- [ ] `MEM_USER=ayhan tools/mem add "x" --shared` → esra da bulur
- [ ] `MEM_USER=cocuk tools/mem search` → yalnız family + kendi notları
- [ ] `MEM_USER=` (boş) → hata (guest yazamaz/arayamaz)
- [ ] `tools/mem search "çocuk"` ve `"cocuk"` aynı sonucu verir
- [ ] Oturum kapat → 30 sn içinde notes'a özet düştü, worker temiz kapandı
- [ ] `git log --oneline -- memory/` audit'i gösteriyor

---

## Faz C — Konsolidasyon ve kalite

### C1. Gece konsolidasyonu (`tools/consolidate.sh` + cron/launchd)

Kullanıcı başına headless pi turu (print modu; rpc gerekmez):
```bash
#!/bin/bash
cd "$(dirname "$0")/.."
for u in $(python3 -c 'import json;print(" ".join(json.load(open("memory/policy.json"))))'); do
  MEM_USER=$u pi -p --model "$PI_MODEL" --append-system-prompt pi/AGENTS.md --skill pi/skills \
    "memory/users/$u/notes/ dosyalarını oku. Tekrarları birleştir, çelişkide yeniyi tut \
     (eskiyi '~~eski~~ (tarih)' olarak düş). Kalıcı hale gelen gerçekleri profile.md'ye \
     taşı ve profile.md'yi 2 KB altında tut. Sonra tools/mem reindex çalıştır."
done
git -C . add memory && git commit -qm "mem: gece konsolidasyonu" || true
```
- v1'in dedup/conflict/importance pipeline'ının tamamı burada, **turdan uzakta**.
- Sıklık: gecede 1 (launchd `~/Library/LaunchAgents/` plist). İlk hafta elle çalıştırıp
  çıktıyı gözle denetle, sonra otomatikleştir.

### C2. Vektör arama (SADECE FTS yetmezse)

Belirti: "geçen ay ne konuşmuştuk?" tarzı anlamsal sorgular FTS'te boş dönüyor.
Çözüm: `sqlite-vec` + lokal embedding (örn. `bge-m3`, Türkçe iyi) → `mem_vec` tablosu,
`mem search`te FTS ∪ vec birleşimi (RRF). CLI arayüzü DEĞİŞMEZ. Erken kurma — FTS +
küçük çekirdek çoğu aile sorgusunu karşılar.

### C3. Yönetim

- `mem list [--user u]` → dosya listesi + son değişiklik; düzenleme = dosyayı elle aç
  (Markdown olması admin paneli ihtiyacını siler).
- Silme: satırı sil + commit. `mem export` zaten var (Faz B).

---

## Faz D — Servisleşme (ancak ihtiyaç kanıtlanınca)

Tetikleyiciler: worker+pi remote'a taşındı VE dosya sistemi paylaşımı yetmiyor,
YA DA aile dışı tenant eklendi. O zaman:
1. `mem` CLI'nin arkasına FastAPI koy (`mem` → HTTP istemcisi olur; skill değişmez).
2. Kimlik: LAN dışına çıkınca `MEM_USER` env yerine token (v1 §4 kimlik modeli).
3. Gerekirse SQLite → Postgres, FTS → Qdrant, Graphiti (v1 planı §9, §16 Faz 4-5).
v1 dokümanı bu fazın tasarımıdır; oraya kadar HİÇBİR servis kurulmaz.

---

## Sıra ve tahmin

| Adım | Süre | Bağımlılık |
|---|---|---|
| A1-A4 çekirdek | yarım gün | yok |
| B1 mem CLI | 3-4 saat | A |
| B2 skill geçişi | 30 dk | B1 |
| B3 finalize | 1-2 saat | agent.py disconnect hook'u |
| C1 konsolidasyon | 2 saat | B, birkaç günlük gerçek notes birikimi |
| C2/C3, D | ihtiyaca göre | — |

Speaker-ID portu (HANDOFF sıra 5) tamamlanana kadar her şey tek kullanıcıyla
(`PI_DEFAULT_PERSONA=candan` → `MEM_USER=candan`) çalışır; port gelince
`persona=<user>` eşlemesi hafızayı otomatik kişiselleştirir — ek iş yok.
