#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Router benchmark v3 — GENISLETILMIS set (137 vaka, %36.5 tuzak), EN + TR.

Kosullar:
  --cond grammar : ollama `format` = JSON schema (XGrammar). Cikti {"tool": <name|null>, "args": {...}}.
                   ABSTAIN = tool:null.  (URETIM ADAYI KOSULU)
  --cond free    : modelin kendi tool-call formati (modele ozel parser)

  --tier full    : 30 tool (7 high dahil). high vakalarda router'in DOGRUDAN cagirip
                   cagirmadigini olcer (yetki kademelemesi riski).
  --tier low     : yalnizca 23 low tool. high vakalar otomatik olarak "semantik komsu
                   tuzagi"na doner -> router abstain etmeli. (URETIM ADAYI KOSULU)

  --lang en|tr   : kullanici cumlesinin dili. Tool katalogu HER ZAMAN Ingilizce
                   (uretimde de oyle olacak: tool aciklamalari koddan gelir).

num_ctx=8192 (2048'de ollama istemi SESSIZCE kirpiyor). Thinking KAPALI.
Jinja tojson -> json.dumps override (HTML-escape bug'i).
"""
import argparse, ast, json, re, statistics, subprocess, time
from collections import Counter

import requests
from jinja2 import Environment, BaseLoader

from router_set import (CASES, catalog_for, TOOL_TIER, ABSTAIN_CATS, TOOL_CATS,
                        OLD35, HIGH_TOOLS)

B = "http://localhost:11434"

GRAMMAR_INSTR = (
    "\n\nAnswer ONLY with a JSON object of the form "
    '{"tool": "<one of the tool names above, or null>", "args": {<arguments>}}. '
    'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
    'or the request is something you cannot do with these tools, answer {"tool": null, "args": {}}.'
)

MODELS = {
    # --- Qwen3.5-4B: quant taramasi ---
    "qwen35-4b-q8": dict(mid="qwen35-4b-q8", quant="Q8_0", tmpl="tmpl/qwen35.jinja",
                         parser="xmltc", stop=["<|im_end|>"], vars={"enable_thinking": False}),
    "qwen35-4b-q6": dict(mid="hf.co/unsloth/Qwen3.5-4B-GGUF:Q6_K", quant="Q6_K", tmpl="tmpl/qwen35.jinja",
                         parser="xmltc", stop=["<|im_end|>"], vars={"enable_thinking": False}),
    "qwen35-4b-q5": dict(mid="hf.co/unsloth/Qwen3.5-4B-GGUF:Q5_K_M", quant="Q5_K_M", tmpl="tmpl/qwen35.jinja",
                         parser="xmltc", stop=["<|im_end|>"], vars={"enable_thinking": False}),
    "qwen35-4b-q4": dict(mid="hf.co/unsloth/Qwen3.5-4B-GGUF:Q4_K_M", quant="Q4_K_M", tmpl="tmpl/qwen35.jinja",
                         parser="xmltc", stop=["<|im_end|>"], vars={"enable_thinking": False}),
    # --- xLAM-2-3b: TAM ADAY (ev ici kullanim -> CC-BY-NC engel degil) ---
    "xlam2-3b-q8": dict(mid="hf.co/Salesforce/xLAM-2-3b-fc-r-gguf:Q8_0", quant="Q8_0",
                        tmpl="xlam_template.jinja", parser="xlam",
                        stop=["<|im_end|>", "<|im_start|>"], vars={}),
    "xlam2-3b-q6": dict(mid="hf.co/Salesforce/xLAM-2-3b-fc-r-gguf:Q6_K", quant="Q6_K",
                        tmpl="xlam_template.jinja", parser="xlam",
                        stop=["<|im_end|>", "<|im_start|>"], vars={}),
    "xlam2-3b-q5": dict(mid="hf.co/Salesforce/xLAM-2-3b-fc-r-gguf:Q5_K_M", quant="Q5_K_M",
                        tmpl="xlam_template.jinja", parser="xlam",
                        stop=["<|im_end|>", "<|im_start|>"], vars={}),
    # --- Nemotron: en ucuz (3.3 GB) ---
    "nemotron3-nano-4b": dict(mid="hf.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF:Q4_K_M", quant="Q4_K_M",
                              tmpl="tmpl/nemotron.jinja", parser="xmltc",
                              stop=["<|im_end|>"], vars={"enable_thinking": False}),
}


# ---------------------------------------------------------------- yardimcilar
def vram_used():
    try:
        o = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
        return int(o.strip().splitlines()[0])
    except Exception:
        return None


def unload(mid):
    try:
        requests.post(B + "/api/generate",
                      json={"model": mid, "prompt": "", "raw": True, "keep_alive": 0, "stream": False},
                      timeout=60)
    except Exception:
        pass


def load_tmpl(path):
    env = Environment(loader=BaseLoader())
    env.policies["json.dumps_kwargs"] = {"ensure_ascii": False}
    # KRITIK: jinja'nin tojson'u Markup dondurur -> HTML-escape bug'i
    env.filters["tojson"] = lambda v, indent=None: json.dumps(v, ensure_ascii=False, indent=indent)
    env.globals["raise_exception"] = lambda m: (_ for _ in ()).throw(RuntimeError(m))
    env.globals["strftime_now"] = lambda f: time.strftime(f)
    return env.from_string(open(path).read())


def strip_think(s):
    s = re.sub(r"<think>.*?</think>", "", s, flags=re.S)
    return s.replace("<think>", "").replace("</think>", "").strip()


def norm(s):
    return str(s).lower().translate(str.maketrans("ışğüöçİ", "isguoci"))


# ---------------------------------------------------------------- parserlar
def p_xmltc(s):
    m = re.search(r"<function=([^>\s]+)\s*>(.*?)</function>", s, re.S)
    if not m:
        if "<tool_call>" in s or "<function" in s:
            return {"tool": None, "args": {}, "err": True}
        return {"tool": None, "args": {}, "err": False}
    args = {k: v.strip() for k, v in
            re.findall(r"<parameter=([^>\s]+)\s*>(.*?)</parameter>", m.group(2), re.S)}
    return {"tool": m.group(1).strip(), "args": args, "err": False}


def p_xlam(s):
    body = s.strip()
    mm = re.search(r"```(?:json|python)?\s*(.*?)```", body, re.S)
    if mm:
        body = mm.group(1).strip()
    obj = None
    for parser in (json.loads, ast.literal_eval):
        try:
            obj = parser(body); break
        except Exception:
            pass
    if obj is None:
        m2 = re.search(r"\[\s*\{.*\}\s*\]", body, re.S)
        if m2:
            for parser in (json.loads, ast.literal_eval):
                try:
                    obj = parser(m2.group(0)); break
                except Exception:
                    pass
    if obj is None:
        if '"name"' not in body and "'name'" not in body:
            return {"tool": None, "args": {}, "err": False}
        return {"tool": None, "args": {}, "err": True}
    if isinstance(obj, dict):
        obj = [obj]
    if isinstance(obj, list):
        if not obj:
            return {"tool": None, "args": {}, "err": False}
        f = obj[0]
        if isinstance(f, dict):
            if isinstance(f.get("function"), dict):
                f = f["function"]
            return {"tool": f.get("name"), "args": f.get("arguments") or f.get("parameters") or {},
                    "err": False}
    return {"tool": None, "args": {}, "err": True}


def p_grammar(s):
    try:
        obj = json.loads(s.strip())
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return {"tool": None, "args": {}, "err": True}
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {"tool": None, "args": {}, "err": True}
    tool = obj.get("tool")
    if isinstance(tool, str) and tool.strip().lower() in ("null", "none", ""):
        tool = None
    args = obj.get("args")
    return {"tool": tool, "args": args if isinstance(args, dict) else {}, "err": False}


PARSERS = {"xmltc": p_xmltc, "xlam": p_xlam}


# ---------------------------------------------------------------- cagri
def build_prompt(tmpl, tvars, text, cond, catalog):
    user = text + (GRAMMAR_INSTR if cond == "grammar" else "")
    # tier/origin alanlari template'e sizmasin -> temiz OpenAI-tarzi tool listesi
    tools = [{"type": "function", "function": t["function"]} for t in catalog]
    return tmpl.render(messages=[{"role": "user", "content": user}],
                       tools=tools, add_generation_prompt=True, **tvars)


def call(mid, prompt, stop, cond, schema, npred=384):
    p = {"model": mid, "prompt": prompt, "raw": True, "stream": False, "keep_alive": "10m",
         "options": {"temperature": 0, "num_predict": npred, "stop": stop, "num_ctx": 8192}}
    if cond == "grammar":
        p["format"] = schema
    t0 = time.time()
    r = requests.post(B + "/api/generate", json=p, timeout=300).json()
    return r, (time.time() - t0) * 1000


# ---------------------------------------------------------------- skorlama
def score_args(pred_args, accept):
    if not accept:
        return True
    blob = norm(json.dumps(pred_args, ensure_ascii=False))
    return all(any(norm(x) in blob for x in subs) for subs in accept.values())


def summarize(per_case, lat, tier):
    cats = Counter(c["cat"] for c, _ in per_case)
    ok = Counter()          # kategori basina "dogru" sayisi
    errors = 0
    splat = Counter()       # abstain gerekirken cagrilan tool'lar (semantik komsu analizi)
    bad = []
    arg_total = arg_ok = 0
    high_fired = high_fired_hightool = 0
    old_ok = old_tot = 0

    for c, pred in per_case:
        cat, t = c["cat"], pred["tool"]
        if pred["err"]:
            errors += 1
        good = False

        if cat in TOOL_CATS:
            good = t in c["gold"]
            if good:
                arg_total += 1
                a = score_args(pred["args"], c["accept"])
                arg_ok += a
                if not a:
                    bad.append((c["id"], cat, c["_text"], "ARG-miss", t, pred["args"]))
            else:
                bad.append((c["id"], cat, c["_text"], "gold=" + "/".join(sorted(c["gold"])),
                            t, pred["args"]))
        else:
            # ABSTAIN beklenen: high, multi, trap_*
            good = (t is None and not pred["err"])
            if not good:
                splat[t] += 1
                bad.append((c["id"], cat, c["_text"], "ABSTAIN bekleniyordu", t, pred["args"]))
            if cat == "high":
                if t is not None:
                    high_fired += 1
                    if t in HIGH_TOOLS:
                        high_fired_hightool += 1

        ok[cat] += good
        if c["id"] in OLD35:
            old_tot += 1
            old_ok += good

    def pct(x, n):
        return round(100.0 * x / n, 1) if n else 0.0

    tool_tot = sum(cats[k] for k in TOOL_CATS)
    tool_ok = sum(ok[k] for k in TOOL_CATS)
    trap_cats = [k for k in cats if k.startswith("trap")]
    trap_tot = sum(cats[k] for k in trap_cats)
    trap_ok = sum(ok[k] for k in trap_cats)

    return {
        "tier": tier,
        "n": len(per_case),
        # ana metrikler
        "recall_pct": pct(tool_ok, tool_tot),                       # tool secimi (tool+pair+arg)
        "arg_ok_pct": pct(arg_ok, tool_tot),                        # tool_total uzerinden (eski ile ayni)
        "arg_ok_given_right_tool_pct": pct(arg_ok, arg_total),
        "trap_abstain_pct": pct(trap_ok, trap_tot),                 # tuzakta abstain
        "trap_wrong_pct": pct(trap_tot - trap_ok, trap_tot),        # <=20 hedef
        "trap_wrong_n": trap_tot - trap_ok,
        "trap_total": trap_tot,
        "multi_abstain_pct": pct(ok["multi"], cats["multi"]),
        "high_abstain_pct": pct(ok["high"], cats["high"]),
        "high_fired_pct": pct(high_fired, cats["high"]),            # KRITIK: yetki kademelemesi
        "high_fired_hightool_n": high_fired_hightool,
        "parse_errors": errors,
        # kategori kirilimi
        "by_cat": {k: {"n": cats[k], "ok": ok[k], "pct": pct(ok[k], cats[k])} for k in sorted(cats)},
        # semantik komsu analizi: abstain gerekirken NEYE yapistirdi
        "splat_targets": dict(splat.most_common()),
        # eski 35 alt kumesi
        "old35_pct": pct(old_ok, old_tot),
        "old35": "%d/%d" % (old_ok, old_tot),
        # gecikme
        "lat_p50_ms": round(statistics.median(lat)) if lat else None,
        "lat_mean_ms": round(statistics.mean(lat)) if lat else None,
        "lat_p95_ms": round(sorted(lat)[int(0.95 * len(lat))]) if len(lat) > 3 else None,
        "bad": bad,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--cond", default="grammar", choices=["free", "grammar"])
    ap.add_argument("--lang", default="en", choices=["en", "tr"])
    ap.add_argument("--tier", default="full", choices=["full", "low"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cfg = MODELS[a.model]
    catalog = catalog_for(a.tier)
    tool_names = [t["function"]["name"] for t in catalog]
    schema = {"type": "object",
              "properties": {
                  "tool": {"anyOf": [{"type": "string", "enum": tool_names}, {"type": "null"}]},
                  "args": {"type": "object"}},
              "required": ["tool", "args"]}
    tmpl = load_tmpl(cfg["tmpl"])
    parse = p_grammar if a.cond == "grammar" else PARSERS[cfg["parser"]]

    print(">>> %s [%s/%s/tier=%s] %s  (%d tool, %d vaka)" % (
        a.model, a.cond, a.lang, a.tier, cfg["mid"], len(catalog), len(CASES)), flush=True)

    vram_before = vram_used()
    for _ in range(3):
        try:
            call(cfg["mid"], build_prompt(tmpl, cfg["vars"], "what time is it right now",
                                          a.cond, catalog), cfg["stop"], a.cond, schema, npred=32)
        except Exception as e:
            print("  warmup err:", e)
    time.sleep(2)
    vram_after = vram_used()
    vram_delta = (vram_after - vram_before) if (vram_before is not None and vram_after is not None) else None
    print("  VRAM: %s -> %s MiB (model ~%s MiB)" % (vram_before, vram_after, vram_delta), flush=True)

    per_case, lat, raws = [], [], []
    for c in CASES:
        text = c[a.lang]
        cc = dict(c)
        cc["_text"] = text
        cc["accept"] = c["accept"] if a.lang == "en" else c["accept_tr"]
        try:
            r, dt = call(cfg["mid"], build_prompt(tmpl, cfg["vars"], text, a.cond, catalog),
                         cfg["stop"], a.cond, schema)
            if r.get("error"):
                print("  %s API ERR: %s" % (c["id"], str(r["error"])[:110]))
                pred, out = {"tool": None, "args": {}, "err": True}, ""
            else:
                out = r.get("response") or ""
                pred = parse(strip_think(out) if a.cond == "free" else out)
                lat.append(dt)
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], e))
            pred, out = {"tool": None, "args": {}, "err": True}, ""
        per_case.append((cc, pred))
        raws.append(out[:300])

    s = summarize(per_case, lat, a.tier)
    out = {"model": a.model, "model_id": cfg["mid"], "quant": cfg["quant"],
           "cond": a.cond, "lang": a.lang, "tier": a.tier,
           "catalog_size": len(catalog), "n_cases": len(CASES),
           "vram_before_mib": vram_before, "vram_after_mib": vram_after,
           "vram_model_mib": vram_delta,
           "summary": {k: v for k, v in s.items() if k != "bad"},
           "errors": [{"id": b[0], "cat": b[1], "text": b[2], "why": b[3],
                       "pred_tool": b[4], "pred_args": b[5]} for b in s["bad"]],
           "raw": [{"id": c["id"], "cat": c["cat"], "text": c["_text"],
                    "pred_tool": p["tool"], "pred_args": p["args"], "err": p["err"], "out": rw}
                   for (c, p), rw in zip(per_case, raws)],
           "latencies_ms": [round(x) for x in lat]}
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n===== %s [%s / %s / tier=%s] =====" % (a.model, a.cond, a.lang, a.tier))
    for k in ("recall_pct", "arg_ok_pct", "trap_abstain_pct", "trap_wrong_pct", "trap_wrong_n",
              "multi_abstain_pct", "high_abstain_pct", "high_fired_pct",
              "old35", "parse_errors", "lat_p50_ms"):
        print("  %-22s %s" % (k, s[k]))
    print("  vram_model_mib         %s" % vram_delta)
    print("  --- kategori kirilimi ---")
    for k, v in s["by_cat"].items():
        print("    %-11s %2d/%2d  %5.1f%%" % (k, v["ok"], v["n"], v["pct"]))
    if s["splat_targets"]:
        print("  --- abstain gerekirken cagrilan tool'lar ---")
        for t, n in s["splat_targets"].items():
            print("    %-24s %d" % (t, n))
    print("out:", a.out)
    unload(cfg["mid"])


if __name__ == "__main__":
    main()
