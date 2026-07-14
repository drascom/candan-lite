#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TEK-MODEL (yerel beyin) adayları icin tool-karari benchmark'i.

router-bench/bench4.py ile AYNI vaka seti (router_set.CASES), AYNI katalog (23 low
tool), AYNI "flag" semasi ({tool,args,multi_intent}) ve AYNI skorlama (bench4.summarize).

TEK FARK: bench4 Ollama'ya /api/generate + model-ozel jinja sablonu ile gidiyordu.
Burada llama-server'in /v1/chat/completions'ina gidiyoruz (response_format=json_schema,
grammar). Neden: her aday icin ayri jinja sablonu bakimi gerekmesin; llama-server
GGUF'un KENDI sohbet sablonunu uygular. Kiyasin adil kalmasi icin BASELINE (Qwen3.5-4B
Q8, bugunun router'i) da AYNI harness'ta yeniden kosulur.

Kullanim (sunucuda):
  python3 bench_brain.py --url http://localhost:8090 --tag qwen3-14b-q6 --lang tr --out r.json
"""
import argparse
import json
import statistics
import sys
import time

import requests

sys.path.insert(0, "/root/router-bench")
from bench4 import INSTR, parse, schema_for, summarize  # noqa: E402
from router_set import CASES, catalog_for  # noqa: E402

TOOL_CALL_NOTE = ""  # Qwen'e ozel XML cagri formati YOK — cikti json_schema ile zorlanir


def build_messages(text, catalog, kind):
    parts = ["# Tools\n\nYou have access to the following functions:\n\n<tools>"]
    for t in catalog:
        parts.append("\n" + json.dumps({"type": "function", "function": t["function"]},
                                       ensure_ascii=False))
    parts.append("\n</tools>")
    return [{"role": "system", "content": "".join(parts)},
            {"role": "user", "content": text + INSTR[kind]}]


def call(url, messages, schema, npred=256):
    payload = {
        "messages": messages,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "route", "strict": True, "schema": schema}},
        "temperature": 0.0,
        "max_tokens": npred,
        "cache_prompt": True,
        # uretim router'iyla ayni (worker/router.py): multi_intent'i ayakta tutan ayar
        "repeat_penalty": 1.1,
        "repeat_last_n": 64,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    t0 = time.time()
    r = requests.post(url + "/v1/chat/completions", json=payload, timeout=600)
    dt = (time.time() - t0) * 1000
    r.raise_for_status()
    body = r.json()
    msg = body["choices"][0]["message"]
    out = msg.get("content") or ""
    usage = body.get("usage") or {}
    return out, dt, usage


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8090")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--lang", default="tr", choices=["en", "tr"])
    ap.add_argument("--tier", default="low", choices=["full", "low"])
    ap.add_argument("--schema", default="flag", choices=["flag", "single", "list"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    catalog = catalog_for(a.tier)
    names = [t["function"]["name"] for t in catalog]
    schema = schema_for(a.schema, names)

    print(">>> %s [%s/%s/tier=%s] %d tool, %d vaka" % (
        a.tag, a.schema, a.lang, a.tier, len(catalog), len(CASES)), flush=True)

    # isinma + statik onek KV-cache'i
    for _ in range(2):
        try:
            call(a.url, build_messages("what time is it", catalog, a.schema), schema, npred=32)
        except Exception as e:
            print("  warmup err:", e)

    per_case, lat, raws = [], [], []
    prompt_toks = None
    for c in CASES:
        text = c[a.lang]
        cc = dict(c)
        cc["_text"] = text
        cc["accept"] = c["accept"] if a.lang == "en" else c["accept_tr"]
        try:
            out, dt, usage = call(a.url, build_messages(text, catalog, a.schema), schema)
            pred = parse(a.schema, out)
            lat.append(dt)
            if prompt_toks is None:
                prompt_toks = usage.get("prompt_tokens")
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], str(e)[:120]))
            pred, out = {"calls": [], "multi": None, "unsup": None, "err": True}, ""
        per_case.append((cc, pred))
        raws.append(out[:400])

    s = summarize(per_case, lat, a.schema)

    # --- ozel ilgi: iki pahali tuzak cumlesi ("kombi ac" n13 / "perdeleri kapat" n14,
    #     ayrica orijinalleri x01 / x07) ---
    FAMOUS = {"x01", "n13", "x07", "n14"}
    famous = [{"id": c["id"], "text": c["_text"], "pred": [x["tool"] for x in p["calls"]],
               "ok": not [x["tool"] for x in p["calls"]]}
              for c, p in per_case if c["id"] in FAMOUS]

    neigh = [{"id": c["id"], "text": c["_text"], "pred": [x["tool"] for x in p["calls"]]}
             for c, p in per_case if c["cat"] == "trap_neigh"]
    neigh_ok = sum(1 for x in neigh if not x["pred"])

    out = {"tag": a.tag, "url": a.url, "schema": a.schema, "lang": a.lang, "tier": a.tier,
           "catalog_size": len(catalog), "n_cases": len(CASES),
           "prompt_tokens": prompt_toks,
           "summary": {k: v for k, v in s.items() if k != "bad"},
           "trap_neigh_ok": "%d/%d" % (neigh_ok, len(neigh)),
           "famous": famous,
           "trap_neigh_detail": neigh,
           "errors": [{"id": b[0], "cat": b[1], "text": b[2], "why": b[3],
                       "pred_tools": b[4], "pred_args": b[5]} for b in s["bad"]],
           "raw": [{"id": c["id"], "cat": c["cat"], "text": c["_text"],
                    "pred_tools": [x["tool"] for x in p["calls"]],
                    "pred_calls": p["calls"], "multi_flag": p["multi"], "err": p["err"],
                    "out": rw}
                   for (c, p), rw in zip(per_case, raws)],
           "latencies_ms": [round(x) for x in lat]}
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("\n===== %s (%s) =====" % (a.tag, a.lang))
    for k in ("recall_pct", "arg_ok_pct", "arg_ok_given_right_tool_pct", "extra_tool_pct",
              "trap_abstain_pct", "trap_wrong_n", "high_abstain_pct",
              "multi_ok_pct", "multi_full_n", "multi_partial_n",
              "multi_flag_tp", "multi_flag_fn", "multi_flag_fp", "multi_flag_fp_pct",
              "old35", "parse_errors", "lat_p50_ms", "lat_p95_ms"):
        print("  %-28s %s" % (k, s[k]))
    print("  %-28s %s" % ("trap_neigh_ok", "%d/%d" % (neigh_ok, len(neigh))))
    print("  %-28s %s" % ("prompt_tokens", prompt_toks))
    print("  --- kategori ---")
    for k, v in s["by_cat"].items():
        print("    %-11s %2d/%2d  %5.1f%%" % (k, v["ok"], v["n"], v["pct"]))
    print("  --- MESHUR IKILI ---")
    for f in famous:
        print("    %-4s %-24s -> %-20s %s" % (f["id"], f["text"], f["pred"] or "ABSTAIN",
                                              "OK" if f["ok"] else "YANLIS"))
    if s["splat_targets"]:
        print("  --- abstain gerekirken cagrilanlar ---")
        for t, n in s["splat_targets"].items():
            print("    %-24s %d" % (t, n))
    print("out:", a.out)


if __name__ == "__main__":
    main()
