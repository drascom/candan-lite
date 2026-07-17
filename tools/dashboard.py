#!/usr/bin/env python3
"""Candan panosu — oturum dökümü + hafıza + worker log'u (tarayıcıdan).

    python3 tools/dashboard.py        → http://192.168.0.25:8765

NEDEN: pi transkriptlerini (sessions/*.jsonl) ve ailenin hafızasını (memory/)
terminalden `grep`'le okumak yorucu. Bu pano ikisini de okunur hâlde gösterir.
Worker artık SUNUCUDA koşuyor; kullanıcı Mac'ten yalnızca CLI istemcisiyle bağlanıyor,
yani worker'ın terminal çıktısını GÖREMİYOR → "Worker Log" sayfası (worker/logs/agent.log).

NOT: eski "router karar defteri" sayfası KALDIRILDI — küçük tool-router'ın kendisi
kaldırıldı (tool seçimini artık tek yerel beyin kendi yapıyor).

TASARIM KARARLARI (bilerek):
  • Yalnızca STANDART KÜTÜPHANE. Yeni bağımlılık YOK, build adımı YOK, tek dosya.
  • REAL-TIME DEĞİL. Sayfa yenilenince veri yenilenir — websocket/polling yok.
      İSTİSNA: /log sayfası. Amacı canlı izlemek (worker'ın terminaline artık kimse
      bakamıyor); "yenile"ye basmak zorunda kalmak sayfayı işe yaramaz kılar. Çözüm
      basit tutuldu: kullanıcının AÇIP KAPATABİLDİĞİ <meta http-equiv=refresh>.
      Websocket/SSE YOK — bağımlılık ve karmaşıklık getirir, kazancı yok.
  • 0.0.0.0'a bağlanır (ev LAN'ı), kimlik doğrulama YOK — kullanıcı böyle İSTEDİ.
      NEDEN: pano artık sunucuda (.25) çalışıyor, çünkü baktığı veri (sessions/,
      memory/, worker/logs/) ORADA. Kullanıcı Mac'ten/telefondan bakıyor →
      127.0.0.1 panoyu erişilemez yapardı. Adres DASHBOARD_HOST ile değiştirilebilir.
      ⚠ Bu pano ailenin gerçek konuşmalarını gösterir ve şifre SORMAZ. Ev LAN'ında
      kalmalı; router'dan port yönlendirme ile internete ASLA açılmamalı.

═══════════════════════════════════════════════════════════════════════════════
 YAZMA YETKİSİ — DAR VE BİLİNÇLİ
═══════════════════════════════════════════════════════════════════════════════
Bu pano AİLENİN GERÇEK HAFIZASINA bakar. Yanlış bir buton geri alınamaz veri
kaybıdır. O yüzden yazma yolu TEKTİR:

  YEDEKLİ      sessions/*.jsonl → sessions/.trash/ içine TAŞINIR. Kalıcı silme YOK.
  ASLA         memory/ (family.md, users/, policy.json, events.db) ve
               worker/data/speakers.db → SALT-OKUNUR. Bu dosyalara yazan/silen
               HİÇBİR endpoint YOKTUR ve EKLENMEZ. SQLite'lar `mode=ro` ile açılır
               (yan etki olarak journal/WAL dosyası bile yaratmasınlar diye).
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import sys
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

SESSIONS_DIR = REPO / "sessions"
TRASH_DIR = SESSIONS_DIR / ".trash"
MEMORY_DIR = REPO / "memory"
EVENTS_DB = MEMORY_DIR / "events.db"
SPEAKERS_DB = REPO / "worker" / "data" / "speakers.db"
WORKER_LOG = REPO / "worker" / "logs" / "agent.log"

PORT = int(os.environ.get("DASHBOARD_PORT", "8765") or 8765)
# Varsayılan 0.0.0.0 = ev LAN'ı (gerekçe: dosya başındaki TASARIM KARARLARI).
HOST = os.environ.get("DASHBOARD_HOST") or "0.0.0.0"  # noqa: S104 — bilinçli, docstring'e bak

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
LOG_LINE_CHOICES = (100, 300, 1000, 3000)
# "2026-07-16 23:54:03 INFO     pid=21232 worker.speaker_tap mesaj... {json-eki}"
LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s+(?P<level>[A-Z]+)\s+"
    r"pid=(?P<pid>\d+)\s+(?P<logger>\S+)\s?(?P<msg>.*)$"
)


# ═══════════════════════════════════════════════════════════════════════════
#  VERİ — hepsi SALT-OKUNUR
# ═══════════════════════════════════════════════════════════════════════════
def session_files() -> list[dict]:
    """sessions/*.jsonl → [{name, person, date, size, messages}]. SADECE OKUR."""
    out = []
    if not SESSIONS_DIR.is_dir():
        return out
    for p in sorted(SESSIONS_DIR.glob("*.jsonl"), reverse=True):
        stem = p.stem
        date, _, person = stem.partition("_")
        n = 0
        try:
            with p.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"type":"message"' in line or '"type": "message"' in line:
                        n += 1
        except OSError:
            pass
        out.append({
            "name": p.name,
            "person": person or "?",
            "date": date.replace("T", " ")[:19],
            "size": p.stat().st_size,
            "messages": n,
        })
    return out


def session_path(name: str) -> Path | None:
    """Dosya adını GÜVENLİ çöz. Sadece sessions/ içindeki düz .jsonl adları —
    yol geçişi (../) imkânsız: adı listeden doğrularız, birleştirmeyiz."""
    if not name or "/" in name or "\\" in name or not name.endswith(".jsonl"):
        return None
    p = SESSIONS_DIR / name
    try:
        if p.resolve().parent != SESSIONS_DIR.resolve() or not p.is_file():
            return None
    except OSError:
        return None
    return p


def strip_system_prefix(text: str) -> str:
    """Modele enjekte edilen "(Sistem: şu an ...)" / "(Sistem notu: ...)" önekini kırp.

    worker/pi_brain.py her tura güncel saati (ve ilk turda selam direktifini) ekler —
    MODEL için gerekli, dökümde GÜRÜLTÜ. Parantez iç içe olabilir ("... (~22:00, gece) ...")
    → derinlik sayarak kapanışı buluyoruz; kapanmayan parantezde metne DOKUNMUYORUZ.
    Aynı kırpma web istemcisinde de var (web/lib/tool-events.ts → stripSystemPrefix)."""
    rest = text
    while True:
        s = rest.lstrip()
        if not s.startswith("(Sistem"):
            return s
        depth = 0
        end = -1
        for i, ch in enumerate(s):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return s          # kapanmayan parantez → olduğu gibi bırak
        rest = s[end + 1:]


def read_session(p: Path) -> list[dict]:
    """pi transkriptini ANLA (bozma!). Satırlar: session/model_change/message.
    message.content parçaları: text | thinking | toolCall. Roller: user/assistant/toolResult.
    """
    items: list[dict] = []
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not isinstance(o, dict) or o.get("type") != "message":
                continue
            m = o.get("message") or {}
            parts = []
            for c in (m.get("content") or []):
                if not isinstance(c, dict):
                    continue
                t = c.get("type")
                if t == "text":
                    txt = c.get("text") or ""
                    if m.get("role") == "user":       # "(Sistem: ...)" öneki gösterilmez
                        txt = strip_system_prefix(txt)
                    if not txt:                        # sadece sistem notuydu → satır yok
                        continue
                    parts.append(("text", txt))
                elif t == "thinking":
                    parts.append(("thinking", c.get("thinking") or ""))
                elif t == "toolCall":
                    args = json.dumps(c.get("arguments") or {}, ensure_ascii=False)
                    parts.append(("tool", f"{c.get('name')}({args})"))
            if parts:
                items.append({"role": m.get("role") or "?", "ts": o.get("timestamp") or "", "parts": parts})
    return items


def sqlite_ro(path: Path) -> sqlite3.Connection | None:
    """SALT-OKUNUR aç. mode=ro → SQLite bu dosyaya YAZMAZ, journal/WAL yan dosyası
    OLUŞTURMAZ. (Her iki db de journal_mode=delete; okuyucu hiçbir şey yaratmaz.)"""
    if not path.exists():
        return None
    try:
        return sqlite3.connect(f"file:{urllib.parse.quote(str(path))}?mode=ro", uri=True)
    except sqlite3.Error:
        return None


def read_events() -> list[dict]:
    con = sqlite_ro(EVENTS_DB)
    if con is None:
        return []
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT kind,user,text,requested_at,due_at,status,attempts,delivered_at,source "
            "FROM events ORDER BY (status='pending') DESC, due_at DESC LIMIT 200"
        )
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def read_speakers() -> list[dict]:
    con = sqlite_ro(SPEAKERS_DB)
    if con is None:
        return []
    try:
        con.row_factory = sqlite3.Row
        cur = con.execute(
            "SELECT s.name, s.sample_count, s.model_id, s.dim, s.enrolled_at, s.updated_at, "
            "       (SELECT COUNT(*) FROM speaker_samples x WHERE x.speaker_id=s.id) AS samples "
            "FROM speakers s ORDER BY s.name"
        )
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.Error:
        return []
    finally:
        con.close()


def read_memory() -> dict:
    """memory/ ağacını SALT-OKUNUR topla: family.md + users/<kişi>/* + policy.json."""
    def slurp(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def meta(p: Path) -> dict:
        try:
            st = p.stat()
            return {"size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")}
        except OSError:
            return {"size": 0, "mtime": "?"}

    family = None
    fam_p = MEMORY_DIR / "family.md"
    if fam_p.is_file():
        family = {"text": slurp(fam_p), **meta(fam_p)}

    policy = {}
    pol_p = MEMORY_DIR / "policy.json"
    if pol_p.is_file():
        try:
            policy = json.loads(slurp(pol_p) or "{}")
        except ValueError:
            policy = {}

    users: list[dict] = []
    udir = MEMORY_DIR / "users"
    if udir.is_dir():
        for person in sorted(p for p in udir.iterdir() if p.is_dir()):
            files = []
            # Kişi dizinini DİNAMİK gez (profile.md/soul.md/notes/... — hardcode YOK).
            for f in sorted(person.rglob("*")):
                if f.is_file() and not f.name.startswith("."):
                    files.append({"rel": str(f.relative_to(person)),
                                  "text": slurp(f), **meta(f)})
            users.append({"name": person.name,
                          "role": policy.get(person.name, "—"),
                          "files": files})
    return {"family": family, "policy": policy, "users": users}


def tail_lines(path: Path, n: int) -> tuple[list[str], bool]:
    """Dosyanın SONUNDAN geriye doğru blok blok okuyup son n satırı döner.

    NEDEN böyle: agent.log sınırsız büyür. read()/readlines() 500 MB'lık bir log'u
    belleğe alır ve panoyu kilitler. Burada sadece son ~n satırlık kuyruk okunur:
    seek(SEEK_END) + geriye doğru 64 KB'lık bloklar, yeterli satır birikince dur.
    SALT-OKUNUR — dosya "rb" açılır, yazılmaz/döndürülmez.

    Döner: (satırlar, kırpıldı_mı). Dosya yoksa/okunamıyorsa ([], False).
    """
    block = 64 * 1024
    buf = b""
    pos = 0
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            while pos > 0 and buf.count(b"\n") <= n:
                step = min(block, pos)
                pos -= step
                f.seek(pos, os.SEEK_SET)
                buf = f.read(step) + buf
    except OSError:
        return [], False
    lines = buf.decode("utf-8", errors="replace").splitlines()
    truncated = pos > 0
    if len(lines) > n:
        lines = lines[-n:]          # blok sınırında yarım kalan ilk satır da böylece gider
        truncated = True
    elif truncated and lines:
        lines = lines[1:]           # başa ulaşmadık → ilk satır yarım olabilir, atla
    return lines, truncated


def parse_log_line(line: str) -> dict:
    """Bir log satırını parçala: {ts, level, pid, logger, msg, extra}.

    Sondaki JSON eki (`{"pid": .., "job_id": .., "room": ..}`) her satırda tekrar eder
    → ayrı tutulur, varsayılan görünümde gösterilmez. Kalıba UYMAYAN satır (traceback
    devamı vb.) level="" ile ham olarak döner — yutulmaz, gösterilir.
    """
    m = LOG_RE.match(line)
    if not m:
        return {"ts": "", "level": "", "pid": "", "logger": "", "msg": line, "extra": ""}
    msg, extra = m["msg"], ""
    i = msg.rfind(' {"')
    if i >= 0:
        cand = msg[i + 1:]
        try:
            json.loads(cand)          # gerçekten JSON eki mi? değilse mesajın parçasıdır
        except ValueError:
            pass
        else:
            msg, extra = msg[:i], cand
    return {"ts": m["ts"], "level": m["level"], "pid": m["pid"],
            "logger": m["logger"], "msg": msg, "extra": extra}


# ═══════════════════════════════════════════════════════════════════════════
#  HTML
# ═══════════════════════════════════════════════════════════════════════════
E = html.escape

CSS = """
:root{--bg:#fff;--fg:#16181d;--mut:#6b7280;--line:#e3e6ea;--card:#f7f8fa;--acc:#1d4ed8;
      --warn:#b45309;--warnbg:#fff7ed;--err:#b91c1c;--errbg:#fef2f2;--ok:#15803d;}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--mut:#9aa1ac;--line:#272b33;
      --card:#171a20;--acc:#7aa2ff;--warn:#f0b072;--warnbg:#2a1f12;--err:#ff8f8f;--errbg:#2a1618;--ok:#7ddc9a;}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);
       padding:10px 18px;display:flex;gap:16px;align-items:center;z-index:5}
header b{font-size:15px}
nav a{color:var(--mut);text-decoration:none;margin-right:14px;padding:4px 0;border-bottom:2px solid transparent}
nav a:hover{color:var(--fg)} nav a.on{color:var(--fg);border-bottom-color:var(--acc)}
main{padding:18px;max-width:1500px;margin:0 auto}
h2{font-size:15px;margin:26px 0 10px;letter-spacing:.02em}
h2 small{font-weight:400;color:var(--mut);margin-left:8px}
.cards{display:flex;flex-wrap:wrap;gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:120px}
.card .n{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}
.card .l{color:var(--mut);font-size:12px}
.wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:var(--card);color:var(--mut);font-weight:600;position:sticky;top:0}
tr:last-child td{border-bottom:0}
td.num{text-align:right;font-variant-numeric:tabular-nums}
td.txt{max-width:520px}
code,pre{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
pre{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:10px;overflow-x:auto;white-space:pre-wrap;margin:6px 0}
.tag{display:inline-block;padding:1px 7px;border-radius:99px;font-size:11px;border:1px solid var(--line);background:var(--card)}
.tag.err{color:var(--err);background:var(--errbg);border-color:var(--err)}
.tag.ok{color:var(--ok)} .tag.warn{color:var(--warn);background:var(--warnbg);border-color:var(--warn)}
.box{border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin:10px 0;background:var(--card)}
.box.warn{border-color:var(--warn);background:var(--warnbg)}
.mut{color:var(--mut)} .empty{color:var(--mut);padding:14px}
input,select,button{font:inherit;padding:6px 9px;border-radius:6px;border:1px solid var(--line);
                    background:var(--bg);color:var(--fg)}
button{cursor:pointer} button.danger{border-color:var(--err);color:var(--err)}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
details{margin:6px 0} summary{cursor:pointer;color:var(--mut)}
.msg{border-left:3px solid var(--line);padding:2px 0 2px 12px;margin:12px 0}
.msg.user{border-color:var(--acc)} .msg.assistant{border-color:var(--ok)} .msg.toolResult{border-color:var(--warn)}
.msg .who{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.05em}
.ro{font-size:12px;color:var(--mut);border:1px dashed var(--line);border-radius:6px;padding:6px 10px;display:inline-block}
/* Worker log — hata ayıklama aracı: WARNING/ERROR göze çarpsın. */
table.log td{border-bottom:0;padding:3px 10px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
table.log tr:nth-child(even){background:var(--card)}
table.log td.lv{font-weight:600;white-space:nowrap}
table.log tr.WARNING td.lv{color:var(--warn)} table.log tr.WARNING{background:var(--warnbg)}
table.log tr.ERROR td.lv,table.log tr.CRITICAL td.lv{color:var(--err)}
table.log tr.ERROR,table.log tr.CRITICAL{background:var(--errbg)}
table.log td.when,table.log td.lg{color:var(--mut);white-space:nowrap}
table.log td.m{white-space:pre-wrap;word-break:break-word}
table.log .extra{color:var(--mut);opacity:.8}
"""

def page(title: str, active: str, body: str, extra_js: str = "", head: str = "") -> bytes:
    nav = [("/sessions", "Oturumlar"), ("/memory", "Hafıza (salt-okunur)"), ("/log", "Worker Log")]
    links = "".join(
        f'<a href="{h}" class="{"on" if h == active else ""}">{E(t)}</a>' for h, t in nav
    )
    now = datetime.now().strftime("%H:%M:%S")
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">{head}
<title>{E(title)} — Candan panosu</title><style>{CSS}</style></head><body>
<header><b>Candan panosu</b><nav>{links}</nav>
<span class="mut" style="margin-left:auto">yenilendi {now} · <a href="" style="color:var(--acc)">yenile</a></span>
</header><main>{body}</main><script>{extra_js}</script></body></html>""".encode()


def render_sessions() -> bytes:
    files = session_files()
    rows = "".join(
        f'<tr><td><a href="/session?f={urllib.parse.quote(f["name"])}" style="color:var(--acc)">'
        f'{E(f["person"])}</a></td>'
        f'<td class="mut">{E(f["date"])}</td>'
        f'<td class="num">{f["messages"]}</td>'
        f'<td class="num">{f["size"] / 1024:.0f} KB</td>'
        f'<td class="mut"><code>{E(f["name"])}</code></td>'
        f'<td><form method="post" action="/session/trash" '
        f'onsubmit="return confirm(\'{E(f["name"])} çöp kutusuna TAŞINACAK (sessions/.trash/). '
        f'Kalıcı silinmez. Emin misin?\')">'
        f'<input type="hidden" name="f" value="{E(f["name"])}">'
        f'<button class="danger" type="submit">Çöpe taşı</button></form></td></tr>'
        for f in files
    ) or '<tr><td colspan="6" class="mut">Oturum dosyası yok.</td></tr>'

    trashed = sorted(TRASH_DIR.glob("*.jsonl")) if TRASH_DIR.is_dir() else []
    tr = ""
    if trashed:
        tr = ('<h2>Çöp kutusu <small>sessions/.trash/ — pano buradan kalıcı SİLMEZ</small></h2>'
              '<div class="box mut">' + "<br>".join(E(p.name) for p in trashed) + "</div>")

    body = f"""
<h2>Oturumlar <small>pi transkriptleri · salt-okunur</small></h2>
<div class="wrap"><table><thead><tr><th>Kişi</th><th>Tarih</th><th class="num">Mesaj</th>
<th class="num">Boyut</th><th>Dosya</th><th></th></tr></thead><tbody>{rows}</tbody></table></div>
{tr}
"""
    return page("Oturumlar", "/sessions", body)


def render_session(name: str) -> bytes:
    p = session_path(name)
    if p is None:
        return page("Oturum", "/sessions", '<div class="box">Dosya bulunamadı.</div>')
    msgs = read_session(p)
    out = []
    for m in msgs:
        blocks = []
        for kind, val in m["parts"]:
            if kind == "text":
                blocks.append(f"<div>{E(val)}</div>")
            elif kind == "tool":
                blocks.append(f"<pre>🔧 {E(val)}</pre>")
            else:
                blocks.append(f"<details><summary>düşünme</summary><pre>{E(val)}</pre></details>")
        who = {"user": "kullanıcı", "assistant": "asistan", "toolResult": "tool sonucu"}.get(
            m["role"], m["role"])
        ts = str(m["ts"])[11:19]
        out.append(f'<div class="msg {E(m["role"])}"><div class="who">{E(who)} · {E(ts)}</div>'
                   f'{"".join(blocks)}</div>')
    body = (f'<h2>{E(p.name)} <small>{len(msgs)} mesaj · salt-okunur</small></h2>'
            f'<p><a href="/sessions" style="color:var(--acc)">← oturumlar</a></p>'
            + ("".join(out) or '<div class="empty">Mesaj yok.</div>'))
    return page(p.name, "/sessions", body)


def render_memory() -> bytes:
    m = read_memory()
    ev = read_events()
    sp = read_speakers()

    fam = ('<div class="box"><div class="mut">memory/family.md · '
           f'{m["family"]["size"]} B · {E(m["family"]["mtime"])}</div>'
           f'<pre>{E(m["family"]["text"])}</pre></div>') if m["family"] else \
        '<div class="empty">family.md yok.</div>'

    users = []
    for u in m["users"]:
        fl = "".join(
            f'<details open><summary><code>{E(f["rel"])}</code> '
            f'<span class="mut">· {f["size"]} B · {E(f["mtime"])}</span></summary>'
            f'<pre>{E(f["text"])}</pre></details>'
            for f in u["files"]
        ) or '<div class="mut">dosya yok</div>'
        users.append(f'<h2>👤 {E(u["name"])} <small>rol: {E(str(u["role"]))}</small></h2>'
                     f'<div class="box">{fl}</div>')
    users_html = "".join(users) or '<div class="empty">Kayıtlı kişi yok.</div>'

    pol = "".join(f'<tr><td>{E(k)}</td><td><span class="tag">{E(str(v))}</span></td></tr>'
                  for k, v in sorted(m["policy"].items())) or \
        '<tr><td colspan="2" class="mut">—</td></tr>'

    def _st(s: str) -> str:
        return f'<span class="tag {"ok" if s == "delivered" else "warn"}">{E(s)}</span>'

    evr = "".join(
        f'<tr><td>{E(e["kind"] or "")}</td><td>{E(e["user"] or "")}</td>'
        f'<td class="txt">{E(e["text"] or "")}</td>'
        f'<td class="mut">{E((e["due_at"] or "").replace("T", " ")[:19])}</td>'
        f'<td>{_st(e["status"] or "")}</td><td class="num">{e["attempts"]}</td>'
        f'<td class="mut">{E(e["source"] or "")}</td></tr>'
        for e in ev
    ) or '<tr><td colspan="7" class="mut">Kayıtlı olay yok.</td></tr>'

    def _ts(v) -> str:
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            return "—"

    spr = "".join(
        f'<tr><td>{E(r["name"] or "")}</td><td class="num">{r["samples"]}</td>'
        f'<td class="mut">{E(str(r["model_id"] or ""))}</td><td class="num">{r["dim"] or ""}</td>'
        f'<td class="mut">{E(_ts(r["enrolled_at"]))}</td>'
        f'<td class="mut">{E(_ts(r["updated_at"]))}</td></tr>'
        for r in sp
    ) or '<tr><td colspan="6" class="mut">speakers.db yok / boş.</td></tr>'

    body = f"""
<p><span class="ro">🔒 SALT-OKUNUR — bu sayfada hiçbir şey değiştirilemez/silinemez.
Ailenin gerçek hafızası burada; pano yalnızca gösterir.</span></p>

<h2>Aile hafızası <small>memory/family.md</small></h2>
{fam}

<h2>Kişiler <small>memory/users/&lt;kişi&gt;/</small></h2>
{users_html}

<h2>Roller <small>memory/policy.json — rol, hafızanın açılıp açılmayacağını belirler</small></h2>
<div class="wrap"><table><thead><tr><th>Kişi</th><th>Rol</th></tr></thead><tbody>{pol}</tbody></table></div>

<h2>Olaylar / hatırlatmalar <small>memory/events.db (mode=ro) · bekleyenler üstte · son 200</small></h2>
<div class="wrap"><table><thead><tr><th>Tür</th><th>Kime</th><th>Ne</th><th>Ne zaman</th>
<th>Durum</th><th class="num">Deneme</th><th>Kaynak</th></tr></thead><tbody>{evr}</tbody></table></div>

<h2>Tanınan sesler <small>worker/data/speakers.db (mode=ro) · örnek sayısı düşükse tanıma dalgalanır (eşik 0.45)</small></h2>
<div class="wrap"><table><thead><tr><th>Kişi</th><th class="num">Ses örneği</th><th>Model</th>
<th class="num">dim</th><th>Kayıt</th><th>Güncelleme</th></tr></thead><tbody>{spr}</tbody></table></div>
"""
    return page("Hafıza", "/memory", body)


def render_log(q: dict) -> bytes:
    """Worker Log — worker/logs/agent.log kuyruğu. SALT-OKUNUR (yazan endpoint YOK)."""
    def one(k: str, default: str = "") -> str:
        return (q.get(k) or [default])[0]

    level = one("level")
    logger = one("logger")
    auto = one("auto") == "1"
    show_json = one("json") == "1"
    try:
        n = int(one("n", "300"))
    except ValueError:
        n = 300
    n = max(10, min(n, 5000))

    if not WORKER_LOG.is_file():
        return page("Worker Log", "/log",
                    f'<h2>Worker Log <small>{E(str(WORKER_LOG))}</small></h2>'
                    '<div class="box">Log dosyası yok. Worker bu makinede hiç çalışmamış olabilir '
                    '— log sunucudaysa (.25) panoyu orada çalıştır.</div>')

    raw, truncated = tail_lines(WORKER_LOG, n)
    rows_all = [parse_log_line(ln) for ln in raw if ln.strip()]
    if not rows_all:
        return page("Worker Log", "/log",
                    f'<h2>Worker Log <small>{E(str(WORKER_LOG))}</small></h2>'
                    '<div class="box">Log dosyası boş.</div>')

    loggers = sorted({r["logger"] for r in rows_all if r["logger"]})
    rows = [r for r in rows_all
            if (not level or r["level"] == level) and (not logger or r["logger"] == logger)]
    rows.reverse()   # EN YENİ ÜSTTE — kullanıcı kaydırmasın

    trs = "".join(
        f'<tr class="{E(r["level"])}"><td class="when">{E(r["ts"][11:])}</td>'
        f'<td class="lv">{E(r["level"])}</td>'
        f'<td class="lg">{E(r["logger"])}</td>'
        f'<td class="m">{E(r["msg"])}'
        + (f'<div class="extra">{E(r["extra"])}</div>' if show_json and r["extra"] else "")
        + "</td></tr>"
        for r in rows
    ) or '<tr><td class="mut" colspan="4">Bu filtreye uyan satır yok.</td></tr>'

    def opts(vals, cur: str) -> str:
        return "".join(f'<option value="{E(str(v))}"{" selected" if str(v) == cur else ""}>'
                       f'{E(str(v) or "hepsi")}</option>' for v in vals)

    def ck(on: bool) -> str:
        return " checked" if on else ""

    bar = f"""<form class="bar" method="get" action="/log">
<label>Seviye <select name="level" onchange="this.form.submit()">{opts(["", *LOG_LEVELS], level)}</select></label>
<label>Logger <select name="logger" onchange="this.form.submit()">{opts(["", *loggers], logger)}</select></label>
<label>Satır <select name="n" onchange="this.form.submit()">{opts(LOG_LINE_CHOICES, str(n))}</select></label>
<label><input type="checkbox" name="json" value="1"{ck(show_json)} onchange="this.form.submit()"> JSON eki</label>
<label><input type="checkbox" name="auto" value="1"{ck(auto)} onchange="this.form.submit()"> Otomatik yenile (5 sn)</label>
<button type="submit">Uygula</button></form>"""

    warn = sum(1 for r in rows_all if r["level"] == "WARNING")
    err = sum(1 for r in rows_all if r["level"] in ("ERROR", "CRITICAL"))
    size = WORKER_LOG.stat().st_size / 1024
    note = " · dosya daha uzun, sadece kuyruk okundu" if truncated else ""

    # REAL-TIME DEĞİL kuralının BİLİNÇLİ istisnası (gerekçe: dosya başındaki docstring).
    head = '<meta http-equiv="refresh" content="5">' if auto else ""
    body = f"""
<h2>Worker Log <small>worker/logs/agent.log · {size:.0f} KB · salt-okunur{E(note)}</small></h2>
{bar}
<div class="cards">
  <div class="card"><div class="n">{len(rows)}</div><div class="l">gösterilen satır</div></div>
  <div class="card"><div class="n" style="color:var(--warn)">{warn}</div><div class="l">WARNING (kuyrukta)</div></div>
  <div class="card"><div class="n" style="color:var(--err)">{err}</div><div class="l">ERROR (kuyrukta)</div></div>
</div>
<p class="mut">En yeni üstte. Son {n} satırın kuyruğu okunur (dosya komple belleğe ALINMAZ).</p>
<div class="wrap"><table class="log"><tbody>{trs}</tbody></table></div>
"""
    return page("Worker Log", "/log", body, head=head)


# ═══════════════════════════════════════════════════════════════════════════
#  HTTP
# ═══════════════════════════════════════════════════════════════════════════
class Handler(BaseHTTPRequestHandler):
    server_version = "CandanDashboard"

    def _send(self, body: bytes, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, to: str) -> None:
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler sözleşmesi
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            if u.path == "/":
                self._redirect("/sessions")   # giriş sayfası: oturumlar
            elif u.path == "/sessions":
                self._send(render_sessions())
            elif u.path == "/session":
                self._send(render_session((q.get("f") or [""])[0]))
            elif u.path == "/memory":
                self._send(render_memory())
            elif u.path == "/log":
                self._send(render_log(q))
            elif u.path == "/favicon.ico":
                self._send(b"", 404)
            else:
                self._send(page("404", "", '<div class="box">Sayfa yok.</div>'), 404)
        except Exception as e:  # noqa: BLE001 — pano çökmesin, hatayı sayfada göster
            self._send(page("Hata", "", f'<div class="box"><b>Hata:</b> <pre>{E(repr(e))}</pre></div>'), 500)

    def do_POST(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "replace")) if n else {}

        # SADECE bu TEK yazma yolu vardır. memory/ ve worker/data/ için YOK — ekleme.
        if u.path == "/session/trash":
            p = session_path((form.get("f") or [""])[0])
            if p is None:
                self._send(page("Hata", "/sessions", '<div class="box">Geçersiz dosya.</div>'), 400)
                return
            try:
                # KALICI SİLME YOK — taşı. Aynı ad varsa üzerine yazma, zaman damgası ekle.
                TRASH_DIR.mkdir(parents=True, exist_ok=True)
                dst = TRASH_DIR / p.name
                if dst.exists():
                    dst = TRASH_DIR / f"{p.stem}.{datetime.now():%Y%m%d-%H%M%S}{p.suffix}"
                shutil.move(str(p), str(dst))
            except OSError as e:
                self._send(page("Hata", "/sessions",
                                f'<div class="box">Taşınamadı: <pre>{E(repr(e))}</pre></div>'), 500)
                return
            self._redirect("/sessions")
        else:
            self._send(page("404", "", '<div class="box">Yok.</div>'), 404)

    def log_message(self, fmt: str, *args) -> None:  # gürültüyü kes
        pass


def main() -> int:
    # Ev LAN'ına açık, şifresiz — bilinçli (bkz. dosya başı). Kısıtlamak için:
    #   DASHBOARD_HOST=127.0.0.1 python3 tools/dashboard.py
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        print(f"Port {PORT} açılamadı ({e}). Başka port: DASHBOARD_PORT=8766 python3 tools/dashboard.py")
        return 1
    print(f"Candan panosu → http://{HOST}:{PORT}")
    if HOST == "0.0.0.0":  # noqa: S104
        print("  ⚠ ev LAN'ına AÇIK, kimlik doğrulama YOK — internete yönlendirme YAPMA.")
    print(f"  oturumlar      : {SESSIONS_DIR}")
    print(f"  hafıza         : {MEMORY_DIR} (SALT-OKUNUR)")
    print(f"  worker log     : {WORKER_LOG} (SALT-OKUNUR)")
    print("Durdur: Ctrl-C")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nkapatıldı.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
