#!/usr/bin/env python3
"""Candan panosu — router karar defteri + oturumlar + hafıza (yerel, tarayıcıdan).

    python3 tools/dashboard.py        → http://127.0.0.1:8765

NEDEN: gölge moddaki router'ın tek ürünü KARARIDIR. Terminalde `grep` ile karar
avlamak yorucu; oran/dağılım/gecikme hesaplanamaz. Bu pano deftere (JSONL) bakar,
özetler ve ŞÜPHELİ kararları öne çıkarır.

TASARIM KARARLARI (bilerek):
  • Yalnızca STANDART KÜTÜPHANE. Yeni bağımlılık YOK, build adımı YOK, tek dosya.
  • REAL-TIME DEĞİL. Sayfa yenilenince veri yenilenir — websocket/polling yok.
  • Yalnızca 127.0.0.1'e bağlanır. Bu bir ev panosu, internete açılmaz.

═══════════════════════════════════════════════════════════════════════════════
 YAZMA YETKİSİ — DAR VE BİLİNÇLİ
═══════════════════════════════════════════════════════════════════════════════
Bu pano AİLENİN GERÇEK HAFIZASINA bakar. Yanlış bir buton geri alınamaz veri
kaybıdır. O yüzden yazma yolları sayılıdır:

  SİLİNEBİLİR  router karar defteri (logs/*.jsonl) — sadece bir log, yeniden üretilir.
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
import shutil
import sqlite3
import statistics
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# router.py ile AYNI varsayılan + aynı çapa (göreli yol → repo kökü).
_raw = os.environ.get("ROUTER_LOG_PATH", "logs/router-decisions.jsonl").strip()
ROUTER_LOG = Path(_raw).expanduser()
if not ROUTER_LOG.is_absolute():
    ROUTER_LOG = REPO / ROUTER_LOG

SESSIONS_DIR = REPO / "sessions"
TRASH_DIR = SESSIONS_DIR / ".trash"
MEMORY_DIR = REPO / "memory"
EVENTS_DB = MEMORY_DIR / "events.db"
SPEAKERS_DB = REPO / "worker" / "data" / "speakers.db"

PORT = int(os.environ.get("DASHBOARD_PORT", "8765") or 8765)
MAX_ROWS = 3000          # defter büyürse yalnız son N karar gösterilir (sayfa şişmesin)
SLOW_MS = 1000.0         # bunun üstü "yavaş" — router'ın ölçülen p50'si ~400ms
MAGNET_MIN_COUNT = 3     # "mıknatıs tool" eşiği: en az bu kadar seçilmiş...
MAGNET_MIN_SHARE = 0.25  # ...ve tool seçilen kararların en az bu kadarını kapmış


# ═══════════════════════════════════════════════════════════════════════════
#  VERİ — hepsi SALT-OKUNUR
# ═══════════════════════════════════════════════════════════════════════════
def read_decisions() -> list[dict]:
    """Karar defterini oku. Bozuk satır varsa ATLA (defter kısmen bozuksa bile pano açılır)."""
    if not ROUTER_LOG.exists():
        return []
    out: list[dict] = []
    try:
        with ROUTER_LOG.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out[-MAX_ROWS:]


def summarize(rows: list[dict]) -> dict:
    """Toplam / abstain oranı / tool dağılımı / gecikme p50-p95 / multi / hata."""
    lat = sorted(float(r.get("latency_ms") or 0) for r in rows)
    tools = Counter(r["tool"] for r in rows if r.get("tool"))
    outcomes = Counter(r.get("outcome") or "?" for r in rows)
    n = len(rows)

    def pct(i: float) -> float:
        if not lat:
            return 0.0
        k = min(len(lat) - 1, max(0, round(i * (len(lat) - 1))))
        return lat[k]

    tool_selected = sum(tools.values())
    return {
        "total": n,
        "abstain": outcomes.get("abstain", 0),
        "abstain_pct": (outcomes.get("abstain", 0) / n * 100) if n else 0.0,
        "tool_selected": tool_selected,
        "tools": tools.most_common(),
        "outcomes": outcomes.most_common(),
        "multi": sum(1 for r in rows if r.get("multi_intent")),
        "errors": outcomes.get("error", 0),
        "timeouts": outcomes.get("timeout", 0),
        "avg_ms": statistics.fmean(lat) if lat else 0.0,
        "p50_ms": pct(0.50),
        "p95_ms": pct(0.95),
    }


def suspicious(rows: list[dict], summary: dict) -> dict:
    """ŞÜPHELİ KARARLAR — panonun en değerli kısmı.

    Bilinen zayıflığımız SEMANTİK KOMŞU TUZAĞI: canlı testte "Kombi aç" ve
    "Perdeleri kapat" cümlelerinin İKİSİ de yanlışlıkla `light_control` seçti.
    Bu hatayı makine tek başına bilemez (doğru cevabı bilmiyoruz) — ama şu ipucu
    güçlü: bir tool kararların ORANSIZ BÜYÜK kısmını kapıyorsa muhtemelen bir
    "çöp kutusu"dur, komşu niyetleri kendine çekiyordur. O yüzden kesin hüküm
    vermek yerine tool'a düşen CÜMLELERİ yan yana listeliyoruz → kullanıcı gözle
    tarar ve "bunun burada işi yok" der. Karar insanın.
    """
    failed = [r for r in rows if (r.get("outcome") in ("error", "timeout")) or r.get("err")]
    slow_gate = max(SLOW_MS, summary["p95_ms"])
    slow = sorted(
        (r for r in rows if float(r.get("latency_ms") or 0) >= slow_gate),
        key=lambda r: float(r.get("latency_ms") or 0), reverse=True,
    )[:25]

    by_tool: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("tool"):
            by_tool[r["tool"]].append(r)
    sel = summary["tool_selected"] or 1
    magnets = sorted(
        (t for t, rs in by_tool.items()
         if len(rs) >= MAGNET_MIN_COUNT and len(rs) / sel >= MAGNET_MIN_SHARE),
        key=lambda t: -len(by_tool[t]),
    )
    return {
        "failed": failed[-25:][::-1],
        "slow": slow,
        "slow_gate": slow_gate,
        "magnets": magnets,
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: -len(kv[1]))),
    }


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
                    parts.append(("text", c.get("text") or ""))
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
"""

JS_FILTER = """
function ff(){
  const o=document.getElementById('f-out').value,
        t=document.getElementById('f-tool').value,
        q=document.getElementById('f-q').value.toLowerCase();
  let n=0;
  document.querySelectorAll('#rt tbody tr').forEach(tr=>{
    const oc=tr.dataset.out, tl=tr.dataset.tool;
    let ok=true;
    if(o==='tool'){ ok = tl!==''; } else if(o && o!=='all'){ ok = oc===o; }
    if(ok && t) ok = tl===t;
    if(ok && q) ok = tr.dataset.q.indexOf(q)>=0;
    tr.style.display = ok?'':'none'; if(ok) n++;
  });
  document.getElementById('cnt').textContent = n;
}
"""


def page(title: str, active: str, body: str, extra_js: str = "") -> bytes:
    nav = [("/", "Router kararları"), ("/sessions", "Oturumlar"), ("/memory", "Hafıza (salt-okunur)")]
    links = "".join(
        f'<a href="{h}" class="{"on" if h == active else ""}">{E(t)}</a>' for h, t in nav
    )
    now = datetime.now().strftime("%H:%M:%S")
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(title)} — Candan panosu</title><style>{CSS}</style></head><body>
<header><b>Candan panosu</b><nav>{links}</nav>
<span class="mut" style="margin-left:auto">yenilendi {now} · <a href="" style="color:var(--acc)">yenile</a></span>
</header><main>{body}</main><script>{extra_js}</script></body></html>""".encode()


def _outcome_tag(oc: str) -> str:
    cls = {"error": "err", "timeout": "err", "no_exec": "warn", "multi": "warn",
           "executed": "ok", "shadow": "ok"}.get(oc, "")
    return f'<span class="tag {cls}">{E(oc)}</span>'


def _dec_table(rows: list[dict], tid: str = "") -> str:
    if not rows:
        return '<div class="empty">Kayıt yok.</div>'
    trs = []
    for r in rows:
        tool = r.get("tool") or ""
        text = r.get("text") or ""
        oc = r.get("outcome") or "?"
        err = r.get("err") or ""
        args = r.get("args") or {}
        args_s = json.dumps(args, ensure_ascii=False) if args else ""
        # Çeviri katmanı açıkken (ROUTER_TRANSLATE) router cümleyi TR+EN görür; hata
        # ayıklamada "model neyi okudu" sorusunun cevabı bu satırdır.
        text_en = r.get("text_en") or ""
        q = E((text + " " + text_en + " " + tool + " " + (r.get("speaker") or "") + " " + err).lower())
        text_cell = E(text)
        if text_en:
            text_cell += f'<div class="mut">→ {E(text_en)}</div>'
        tool_cell = f"<code>{E(tool)}</code>" if tool else '<span class="mut">—</span>'
        if args_s:
            tool_cell += f'<div class="mut"><code>{E(args_s)}</code></div>'
        multi_cell = '<span class="tag warn">multi</span>' if r.get("multi_intent") else ""
        err_cell = f'<div class="mut">{E(err)}</div>' if err else ""
        trs.append(
            f'<tr data-out="{E(oc)}" data-tool="{E(tool)}" data-q="{q}">'
            f'<td class="mut">{E((r.get("ts") or "")[11:19])}</td>'
            f'<td>{E(r.get("speaker") or "—")}</td>'
            f'<td class="txt">{text_cell}</td>'
            f'<td>{tool_cell}</td>'
            f'<td>{multi_cell}</td>'
            f'<td>{_outcome_tag(oc)}{err_cell}</td>'
            f'<td class="num">{float(r.get("latency_ms") or 0):.0f}</td></tr>'
        )
    idattr = f' id="{tid}"' if tid else ""
    return (f'<div class="wrap"><table{idattr}><thead><tr><th>Saat</th><th>Konuşan</th><th>Cümle</th>'
            f'<th>Tool / args</th><th>Multi</th><th>Sonuç</th><th class="num">ms</th></tr></thead>'
            f'<tbody>{"".join(trs)}</tbody></table></div>')


def render_router() -> bytes:
    rows = read_decisions()
    s = summarize(rows)
    susp = suspicious(rows, s)
    newest = rows[::-1]  # en yeniler üstte

    cards = "".join(
        f'<div class="card"><div class="n">{n}</div><div class="l">{E(lbl)}</div></div>'
        for n, lbl in [
            (f'{s["total"]}', "toplam karar"),
            (f'{s["abstain_pct"]:.0f}%', f'abstain ({s["abstain"]})'),
            (f'{s["tool_selected"]}', "tool seçildi"),
            (f'{s["multi"]}', "multi_intent"),
            (f'{s["errors"] + s["timeouts"]}', "hata + timeout"),
            (f'{s["p50_ms"]:.0f}', "p50 ms"),
            (f'{s["p95_ms"]:.0f}', "p95 ms"),
            (f'{s["avg_ms"]:.0f}', "ort. ms"),
        ]
    )

    dist = "".join(
        f'<tr><td><code>{E(t)}</code></td><td class="num">{c}</td>'
        f'<td class="num">{c / (s["tool_selected"] or 1) * 100:.0f}%</td></tr>'
        for t, c in s["tools"]
    ) or '<tr><td colspan="3" class="mut">—</td></tr>'
    oc_dist = "".join(
        f'<tr><td>{_outcome_tag(o)}</td><td class="num">{c}</td>'
        f'<td class="num">{c / (s["total"] or 1) * 100:.0f}%</td></tr>'
        for o, c in s["outcomes"]
    ) or '<tr><td colspan="3" class="mut">—</td></tr>'

    # ── şüpheli ──
    sus_html = []
    if susp["magnets"]:
        for t in susp["magnets"]:
            rs = susp["by_tool"][t]
            share = len(rs) / (s["tool_selected"] or 1) * 100
            lis = "".join(f'<li>{E(r.get("text") or "")} '
                          f'<span class="mut">— {E(json.dumps(r.get("args") or {}, ensure_ascii=False))}</span></li>'
                          for r in rs[-40:][::-1])
            sus_html.append(
                f'<div class="box warn"><b>Mıknatıs tool: <code>{E(t)}</code></b> '
                f'<span class="mut">— tool seçilen kararların %{share:.0f}\'ini ({len(rs)}) kaptı. '
                f'Semantik komşu tuzağı olabilir (ör. "Kombi aç" → light_control). '
                f'Cümleleri gözle tara: buraya AİT OLMAYAN var mı?</span>'
                f'<ul>{lis}</ul></div>'
            )
    if susp["failed"]:
        sus_html.append(f'<h2>Hatalı / timeout kararlar <small>{len(susp["failed"])}</small></h2>'
                        + _dec_table(susp["failed"]))
    if susp["slow"]:
        sus_html.append(f'<h2>Yavaş kararlar <small>≥ {susp["slow_gate"]:.0f} ms</small></h2>'
                        + _dec_table(susp["slow"]))
    if not sus_html:
        sus_html.append('<div class="box mut">Şüpheli bir şey yok (ya da defter henüz boş).</div>')

    # tool → cümleler (gözle tarama; mıknatıs olmasa da faydalı)
    groups = "".join(
        f'<details><summary><code>{E(t)}</code> — {len(rs)} cümle</summary><ul>'
        + "".join(f'<li>{E(r.get("text") or "")}</li>' for r in rs[-40:][::-1])
        + "</ul></details>"
        for t, rs in susp["by_tool"].items()
    ) or '<div class="mut">Henüz hiçbir tool seçilmedi.</div>'

    tool_opts = "".join(f'<option value="{E(t)}">{E(t)} ({c})</option>' for t, c in s["tools"])
    oc_opts = "".join(f'<option value="{E(o)}">{E(o)} ({c})</option>' for o, c in s["outcomes"])

    body = f"""
<h2>Özet <small>{E(str(ROUTER_LOG))}{" · defter yok" if not ROUTER_LOG.exists() else ""}</small></h2>
<div class="cards">{cards}</div>

<div style="display:flex;gap:18px;flex-wrap:wrap;margin-top:14px">
  <div style="flex:1;min-width:280px"><h2>Tool dağılımı</h2><div class="wrap"><table>
    <thead><tr><th>Tool</th><th class="num">Kez</th><th class="num">Pay</th></tr></thead>
    <tbody>{dist}</tbody></table></div></div>
  <div style="flex:1;min-width:280px"><h2>Sonuç dağılımı</h2><div class="wrap"><table>
    <thead><tr><th>Sonuç</th><th class="num">Kez</th><th class="num">Pay</th></tr></thead>
    <tbody>{oc_dist}</tbody></table></div></div>
</div>

<h2>⚠ Şüpheli kararlar <small>gözden geçir</small></h2>
{"".join(sus_html)}

<h2>Tool → seçilen cümleler <small>gözle tara</small></h2>
{groups}

<h2>Tüm kararlar <small>en yeniler üstte · gösterilen: <span id="cnt">{len(newest)}</span></small></h2>
<div class="bar">
  <select id="f-out" onchange="ff()">
    <option value="all">Tüm sonuçlar</option>
    <option value="tool">Tool seçilenler</option>
    {oc_opts}
  </select>
  <select id="f-tool" onchange="ff()"><option value="">Tüm tool'lar</option>{tool_opts}</select>
  <input id="f-q" placeholder="metinde ara…" oninput="ff()" size="30">
  <form method="post" action="/router/clear" style="margin-left:auto"
        onsubmit="return confirm('Router karar defteri SİLİNECEK ({len(rows)} kayıt). Emin misin?')">
    <button class="danger" type="submit">Karar defterini temizle</button>
  </form>
</div>
{_dec_table(newest, tid="rt")}
"""
    return page("Router kararları", "/", body, JS_FILTER)


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
                self._send(render_router())
            elif u.path == "/sessions":
                self._send(render_sessions())
            elif u.path == "/session":
                self._send(render_session((q.get("f") or [""])[0]))
            elif u.path == "/memory":
                self._send(render_memory())
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

        # SADECE bu iki yazma yolu vardır. memory/ ve worker/data/ için YOK — ekleme.
        if u.path == "/router/clear":
            try:
                if ROUTER_LOG.exists():
                    ROUTER_LOG.unlink()  # sadece bir log; router bir sonraki kararda yeniden yaratır
            except OSError as e:
                self._send(page("Hata", "/", f'<div class="box">Silinemedi: <pre>{E(repr(e))}</pre></div>'), 500)
                return
            self._redirect("/")
        elif u.path == "/session/trash":
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
    host = "127.0.0.1"  # SADECE yerel. Bu pano aile verisi gösterir; ağa açılmaz.
    try:
        srv = ThreadingHTTPServer((host, PORT), Handler)
    except OSError as e:
        print(f"Port {PORT} açılamadı ({e}). Başka port: DASHBOARD_PORT=8766 python3 tools/dashboard.py")
        return 1
    print(f"Candan panosu → http://{host}:{PORT}")
    print(f"  router defteri : {ROUTER_LOG}")
    print(f"  oturumlar      : {SESSIONS_DIR}")
    print(f"  hafıza         : {MEMORY_DIR} (SALT-OKUNUR)")
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
