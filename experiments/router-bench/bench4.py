#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Router benchmark v4 — SEMA deneyi: tek-tool vs TOOL LISTESI vs tek-tool+multi_intent bayragi.

bench3.py ile ayni model/prompt/katalog/vaka seti. TEK degisen: cikti SEMASI.

  --schema single : {"tool": <name|null>, "args": {}}                  (bench3 ile ayni — KONTROL)
  --schema list   : {"tools": [{"tool": <name>, "args": {}}, ...]}     (bos liste = abstain)
  --schema flag   : {"tool": <name|null>, "args": {}, "multi_intent": bool}   (secenek b)

Enum HER UC SEMADA korunur -> gecersiz tool adi imkansiz.
Uretim kosulu: --cond grammar --tier low (yalnizca 23 low tool).

YENI METRIKLER
  extra_tool_pct : TEK tool gereken vakada (tool/pair/arg) 2+ tool dondurme orani.
                   "sema modeli fazladan bir sey secmeye itiyor mu?" sorusunun olcusu.
  multi_full_pct : cok-niyetli vakada TUM niyetleri yakalama (list semasi).
  multi_flag_*   : flag semasinda multi_intent bayraginin recall / false-positive orani.
"""
import argparse, json, re, statistics, subprocess, time
from collections import Counter

import requests
from jinja2 import Environment, BaseLoader

from router_set import CASES, catalog_for, TOOL_CATS, OLD35, HIGH_TOOLS

B = "http://localhost:11434"

MODELS = {
    "qwen35-4b-q8": dict(mid="qwen35-4b-q8", quant="Q8_0", tmpl="tmpl/qwen35.jinja",
                         stop=["<|im_end|>"], vars={"enable_thinking": False}),
    "qwen35-4b-q6": dict(mid="hf.co/unsloth/Qwen3.5-4B-GGUF:Q6_K", quant="Q6_K", tmpl="tmpl/qwen35.jinja",
                         stop=["<|im_end|>"], vars={"enable_thinking": False}),
}

# --------------------------------------------------------------------------
# cok-niyetli vakalarin GERCEK niyet ayrisimi (low-tier katalog uzerinden)
#   need   : low katalogda IFADE EDILEBILIR tool'lar
#   unreach: ikinci niyet HIGH tier -> router'a hic gosterilmiyor, ifade EDILEMEZ
# m01'in 2. niyeti (message_leave) HIGH tier. Router isi TAM yapamaz -> dogru
# davranis abstain ([]). Yarim is ([light_control]) = TAM OLARAK kacindigimiz hata.
# --------------------------------------------------------------------------
MULTI_GOLD = {
    "m01": dict(need=["light_control"], unreach=["message_leave"]),
    "m02": dict(need=["shopping_add", "reminder_add"], unreach=[]),
    "m03": dict(need=["weather", "reminder_add"], unreach=[]),
    "m04": dict(need=["diet_log", "diet_summary"], unreach=[]),
    "m05": dict(need=["media_play", "light_control"], unreach=[]),
    "m06": dict(need=["memory_add", "reminder_add"], unreach=[]),
}

INSTR = {
    "single": (
        "\n\nAnswer ONLY with a JSON object of the form "
        '{"tool": "<one of the tool names above, or null>", "args": {<arguments>}}. '
        'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
        'or the request is something you cannot do with these tools, answer {"tool": null, "args": {}}.'),
    "list": (
        "\n\nAnswer ONLY with a JSON object of the form "
        '{"tools": [{"tool": "<one of the tool names above>", "args": {<arguments>}}, ...]}. '
        'Add one entry for each thing the user is asking you to do. '
        'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
        'or the request is something you cannot do with these tools, answer {"tools": []}.'),
    # "list" + fazladan-tool frenleyicisi (yalnizca extra_tool yuksek cikarsa denenir)
    "list_guard": (
        "\n\nAnswer ONLY with a JSON object of the form "
        '{"tools": [{"tool": "<one of the tool names above>", "args": {<arguments>}}, ...]}. '
        'Add one entry for each SEPARATE thing the user is asking you to do. '
        'Almost every request needs exactly ONE entry; use two only when the user clearly asked '
        'for two different things in one sentence. '
        'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
        'or the request is something you cannot do with these tools, answer {"tools": []}.'),
    # "list" ama abstain = null (bos dizi DEGIL). Amac: "dizi mi, yoksa liste fikri mi
    # abstain'i bozuyor" sorusunu ayirmak. null, baseline'daki gibi BIRINCI SINIF bir
    # "hicbir sey" secenegi.
    "list_null": (
        "\n\nAnswer ONLY with a JSON object of the form "
        '{"tools": [{"tool": "<one of the tool names above>", "args": {<arguments>}}, ...]}. '
        'Add one entry for each thing the user is asking you to do. '
        'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
        'or the request is something you cannot do with these tools, answer {"tools": null}.'),
    "flag": (
        "\n\nAnswer ONLY with a JSON object of the form "
        '{"tool": "<one of the tool names above, or null>", "args": {<arguments>}, '
        '"multi_intent": <true|false>}. '
        'Set "multi_intent" to true if the user asked for MORE THAN ONE separate thing in this '
        'sentence, false otherwise. '
        'If none of the tools fit, or the user is just chatting / venting / asking your opinion, '
        'or the request is something you cannot do with these tools, answer '
        '{"tool": null, "args": {}, "multi_intent": false}.'),
}


def schema_for(kind, names):
    ent = {"type": "object",
           "properties": {"tool": {"type": "string", "enum": names},
                          "args": {"type": "object"}},
           "required": ["tool", "args"]}
    if kind in ("list", "list_guard"):
        return {"type": "object",
                "properties": {"tools": {"type": "array", "items": ent}},
                "required": ["tools"]}
    if kind == "list_null":
        return {"type": "object",
                "properties": {"tools": {"anyOf": [{"type": "array", "items": ent},
                                                   {"type": "null"}]}},
                "required": ["tools"]}
    base = {"type": "object",
            "properties": {"tool": {"anyOf": [{"type": "string", "enum": names}, {"type": "null"}]},
                           "args": {"type": "object"}},
            "required": ["tool", "args"]}
    if kind == "flag":
        base["properties"]["multi_intent"] = {"type": "boolean"}
        base["required"] = ["tool", "args", "multi_intent"]
    return base


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
    env.filters["tojson"] = lambda v, indent=None: json.dumps(v, ensure_ascii=False, indent=indent)
    env.globals["raise_exception"] = lambda m: (_ for _ in ()).throw(RuntimeError(m))
    env.globals["strftime_now"] = lambda f: time.strftime(f)
    return env.from_string(open(path).read())


def norm(s):
    return str(s).lower().translate(str.maketrans("ışğüöçİ", "isguoci"))


def parse(kind, s):
    """-> {"calls": [{"tool":..,"args":{}}], "multi": bool|None, "err": bool}"""
    try:
        obj = json.loads(s.strip())
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return {"calls": [], "multi": None, "err": True}
    if kind in ("list", "list_guard", "list_null"):
        calls = []
        for e in (obj.get("tools") or []):
            if isinstance(e, dict) and isinstance(e.get("tool"), str) and e["tool"]:
                calls.append({"tool": e["tool"],
                              "args": e.get("args") if isinstance(e.get("args"), dict) else {}})
        return {"calls": calls, "multi": None, "err": False}
    t = obj.get("tool")
    if isinstance(t, str) and t.strip().lower() in ("null", "none", ""):
        t = None
    calls = [] if t is None else [{"tool": t,
                                  "args": obj.get("args") if isinstance(obj.get("args"), dict) else {}}]
    mi = obj.get("multi_intent") if kind == "flag" else None
    return {"calls": calls, "multi": bool(mi) if isinstance(mi, bool) else None, "err": False}


def build_prompt(tmpl, tvars, text, kind, catalog):
    user = text + INSTR[kind]
    tools = [{"type": "function", "function": t["function"]} for t in catalog]
    return tmpl.render(messages=[{"role": "user", "content": user}],
                       tools=tools, add_generation_prompt=True, **tvars)


def call(mid, prompt, stop, schema, npred=512):
    p = {"model": mid, "prompt": prompt, "raw": True, "stream": False, "keep_alive": "10m",
         "format": schema,
         "options": {"temperature": 0, "num_predict": npred, "stop": stop, "num_ctx": 8192}}
    t0 = time.time()
    r = requests.post(B + "/api/generate", json=p, timeout=300).json()
    return r, (time.time() - t0) * 1000


# ---------------------------------------------------------------- skorlama
def score_args(pred_args, accept):
    if not accept:
        return True
    blob = norm(json.dumps(pred_args, ensure_ascii=False))
    return all(any(norm(x) in blob for x in subs) for subs in accept.values())


def summarize(per_case, lat, kind):
    cats = Counter(c["cat"] for c, _ in per_case)
    ok = Counter()
    errors = 0
    splat = Counter()
    bad = []
    arg_total = arg_ok = 0
    extra_tool_n = extra_tool_tot = 0        # tek-tool gerekirken 2+ dondurme
    high_fired = high_fired_hightool = 0
    old_ok = old_tot = 0
    # multi kirilimi
    m_full = m_partial = m_none = m_abst = 0
    m01_abstain = m01_half = None
    # flag semasi
    fl_multi_tp = fl_multi_fn = fl_multi_fp = fl_multi_tn = 0

    for c, pred in per_case:
        cat = c["cat"]
        calls = pred["calls"]
        tools = [x["tool"] for x in calls]
        if pred["err"]:
            errors += 1
        good = False

        if kind == "flag" and pred["multi"] is not None:
            if cat == "multi":
                fl_multi_tp += pred["multi"]; fl_multi_fn += (not pred["multi"])
            else:
                fl_multi_fp += pred["multi"]; fl_multi_tn += (not pred["multi"])

        if cat in TOOL_CATS:                       # TEK tool bekleniyor
            extra_tool_tot += 1
            if len(tools) >= 2:
                extra_tool_n += 1
            hit = [x for x in calls if x["tool"] in c["gold"]]
            good = bool(hit) and len(tools) == 1   # STRICT: tam olarak 1 tool ve dogru
            lenient = bool(hit)
            if hit:
                arg_total += 1
                a = score_args(hit[0]["args"], c["accept"])
                arg_ok += a
                if not a:
                    bad.append((c["id"], cat, c["_text"], "ARG-miss", tools, hit[0]["args"]))
            if not good:
                why = "FAZLADAN-TOOL" if lenient else "gold=" + "/".join(sorted(c["gold"]))
                bad.append((c["id"], cat, c["_text"], why, tools,
                            [x["args"] for x in calls]))

        elif cat == "multi":
            g = MULTI_GOLD[c["id"]]
            need, unreach = set(g["need"]), set(g["unreach"])
            got = set(tools)
            if unreach:
                # ikinci niyet ifade EDILEMEZ -> tek dogru cevap abstain
                good = (len(tools) == 0)
                m01_abstain = good
                m01_half = (got == need)      # yarim is: sadece low tool'u cagirdi
                if not good:
                    bad.append((c["id"], cat, c["_text"],
                                "YARIM-IS (2. niyet HIGH tool, abstain bekleniyordu)", tools, None))
                m_abst += good
            else:
                good = (got == need)          # TUM niyetler + fazlasi yok
                if good:
                    m_full += 1
                elif len(got & need) == 1 and len(tools) <= 1:
                    m_partial += 1
                    bad.append((c["id"], cat, c["_text"],
                                "YARIM-IS (need=%s)" % "+".join(sorted(need)), tools, None))
                elif not tools:
                    m_none += 1
                    bad.append((c["id"], cat, c["_text"],
                                "ABSTAIN (need=%s)" % "+".join(sorted(need)), tools, None))
                else:
                    bad.append((c["id"], cat, c["_text"],
                                "YANLIS (need=%s)" % "+".join(sorted(need)), tools, None))

        else:                                       # high + trap_* -> ABSTAIN
            good = (not tools) and not pred["err"]
            if not good:
                for t in tools:
                    splat[t] += 1
                bad.append((c["id"], cat, c["_text"], "ABSTAIN bekleniyordu", tools,
                            [x["args"] for x in calls]))
            if cat == "high" and tools:
                high_fired += 1
                if any(t in HIGH_TOOLS for t in tools):
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
        "schema": kind,
        "n": len(per_case),
        "recall_pct": pct(tool_ok, tool_tot),
        "arg_ok_pct": pct(arg_ok, tool_tot),
        "arg_ok_given_right_tool_pct": pct(arg_ok, arg_total),
        # >>> YENI: fazladan tool
        "extra_tool_pct": pct(extra_tool_n, extra_tool_tot),
        "extra_tool_n": extra_tool_n,
        "extra_tool_total": extra_tool_tot,
        "trap_abstain_pct": pct(trap_ok, trap_tot),
        "trap_wrong_n": trap_tot - trap_ok,
        "trap_total": trap_tot,
        "high_abstain_pct": pct(ok["high"], cats["high"]),
        "high_fired_pct": pct(high_fired, cats["high"]),
        "high_fired_hightool_n": high_fired_hightool,
        # >>> multi kirilimi
        "multi_ok_pct": pct(ok["multi"], cats["multi"]),
        # bench3 politikasi (multi'de dogru = ABSTAIN/pas gec) ile kiyas icin:
        "multi_abstain_n": m_none + (1 if m01_abstain else 0),
        "multi_full_n": m_full,             # her iki niyet de yakalandi (m02-m06, n=5)
        "multi_partial_n": m_partial,       # yarim is
        "multi_none_n": m_none,             # hicbir sey
        "m01_abstain": m01_abstain,         # 2. niyet HIGH -> abstain etti mi
        "m01_half_job": m01_half,
        # >>> flag semasi
        "multi_flag_tp": fl_multi_tp, "multi_flag_fn": fl_multi_fn,
        "multi_flag_fp": fl_multi_fp, "multi_flag_tn": fl_multi_tn,
        "multi_flag_fp_pct": pct(fl_multi_fp, fl_multi_fp + fl_multi_tn),
        "parse_errors": errors,
        "by_cat": {k: {"n": cats[k], "ok": ok[k], "pct": pct(ok[k], cats[k])} for k in sorted(cats)},
        "splat_targets": dict(splat.most_common()),
        "old35_pct": pct(old_ok, old_tot),
        "old35": "%d/%d" % (old_ok, old_tot),
        "lat_p50_ms": round(statistics.median(lat)) if lat else None,
        "lat_mean_ms": round(statistics.mean(lat)) if lat else None,
        "lat_p95_ms": round(sorted(lat)[int(0.95 * len(lat))]) if len(lat) > 3 else None,
        "bad": bad,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen35-4b-q8", choices=list(MODELS))
    ap.add_argument("--schema", required=True, choices=["single", "list", "list_null", "list_guard", "flag"])
    ap.add_argument("--lang", default="en", choices=["en", "tr"])
    ap.add_argument("--tier", default="low", choices=["full", "low"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cfg = MODELS[a.model]
    catalog = catalog_for(a.tier)
    names = [t["function"]["name"] for t in catalog]
    schema = schema_for(a.schema, names)
    tmpl = load_tmpl(cfg["tmpl"])

    print(">>> %s [schema=%s/%s/tier=%s] (%d tool, %d vaka)" % (
        a.model, a.schema, a.lang, a.tier, len(catalog), len(CASES)), flush=True)

    vram_before = vram_used()
    for _ in range(3):
        try:
            call(cfg["mid"], build_prompt(tmpl, cfg["vars"], "what time is it right now",
                                          a.schema, catalog), cfg["stop"], schema, npred=32)
        except Exception as e:
            print("  warmup err:", e)
    time.sleep(2)
    vram_after = vram_used()

    per_case, lat, raws = [], [], []
    for c in CASES:
        text = c[a.lang]
        cc = dict(c); cc["_text"] = text
        cc["accept"] = c["accept"] if a.lang == "en" else c["accept_tr"]
        try:
            r, dt = call(cfg["mid"], build_prompt(tmpl, cfg["vars"], text, a.schema, catalog),
                         cfg["stop"], schema)
            if r.get("error"):
                print("  %s API ERR: %s" % (c["id"], str(r["error"])[:110]))
                pred, out = {"calls": [], "multi": None, "err": True}, ""
            else:
                out = r.get("response") or ""
                pred = parse(a.schema, out)
                lat.append(dt)
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], e))
            pred, out = {"calls": [], "multi": None, "err": True}, ""
        per_case.append((cc, pred))
        raws.append(out[:400])

    s = summarize(per_case, lat, a.schema)
    out = {"model": a.model, "model_id": cfg["mid"], "quant": cfg["quant"],
           "schema": a.schema, "lang": a.lang, "tier": a.tier,
           "catalog_size": len(catalog), "n_cases": len(CASES),
           "vram_model_mib": (vram_after - vram_before) if (vram_before and vram_after) else None,
           "summary": {k: v for k, v in s.items() if k != "bad"},
           "errors": [{"id": b[0], "cat": b[1], "text": b[2], "why": b[3],
                       "pred_tools": b[4], "pred_args": b[5]} for b in s["bad"]],
           "raw": [{"id": c["id"], "cat": c["cat"], "text": c["_text"],
                    "pred_tools": [x["tool"] for x in p["calls"]],
                    "pred_calls": p["calls"], "multi_flag": p["multi"],
                    "err": p["err"], "out": rw}
                   for (c, p), rw in zip(per_case, raws)],
           "latencies_ms": [round(x) for x in lat]}
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n===== %s [schema=%s / %s / tier=%s] =====" % (a.model, a.schema, a.lang, a.tier))
    for k in ("recall_pct", "arg_ok_pct", "extra_tool_pct", "extra_tool_n",
              "trap_abstain_pct", "trap_wrong_n", "high_abstain_pct", "high_fired_pct",
              "multi_ok_pct", "multi_full_n", "multi_partial_n", "multi_none_n",
              "m01_abstain", "m01_half_job",
              "multi_flag_tp", "multi_flag_fn", "multi_flag_fp", "multi_flag_fp_pct",
              "old35", "parse_errors", "lat_p50_ms"):
        print("  %-24s %s" % (k, s[k]))
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
