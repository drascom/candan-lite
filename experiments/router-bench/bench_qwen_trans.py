#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Qwen3.5-4B'yi CEVIRILMIS (opus-mt tr->en) cumlelerde olc — CEVIRI KAYBI olcumu.

Soru: TR cumleyi kucuk bir ceviri modeliyle (Helsinki-NLP/opus-mt-tc-big-tr-en, ~20ms)
İngilizce'ye cevirip router'a EN vermek, dogrudan TR vermekten IYI mi KOTU mu?
Ozellikle ARGUMAN dogrulugu (ozel isim + goreli zaman) ceviride bozuluyor mu?

bench4.py'nin prompt/cagri/skorlama fonksiyonlarini AYNEN kullanir (tek fark: metin kaynagi).
Kiyas: results_qwen35_tr_single.json (dogrudan TR) vs results_qwen35_en_single.json (native EN).
"""
import argparse, json, time

from router_set import CASES, catalog_for
from bench4 import MODELS, INSTR, schema_for, load_tmpl, build_prompt, call, parse, summarize, vram_used


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen35-4b-q8", choices=list(MODELS))
    ap.add_argument("--schema", default="single")
    ap.add_argument("--tier", default="low", choices=["full", "low"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cfg = MODELS[a.model]
    catalog = catalog_for(a.tier)
    names = [t["function"]["name"] for t in catalog]
    schema = schema_for(a.schema, names)
    tmpl = load_tmpl(cfg["tmpl"])
    trans = json.load(open("translated_tr2en.json"))

    print(">>> %s [schema=%s/lang=TRANS(tr->en)/tier=%s] (%d tool, %d vaka)" % (
        a.model, a.schema, a.tier, len(catalog), len(CASES)), flush=True)

    for _ in range(3):
        try:
            call(cfg["mid"], build_prompt(tmpl, cfg["vars"], "what time is it right now",
                                          a.schema, catalog), cfg["stop"], schema, npred=32)
        except Exception as e:
            print("  warmup err:", e)

    per_case, lat, raws = [], [], []
    for c in CASES:
        text = trans[c["id"]]                 # CEVIRILMIS ingilizce metin
        cc = dict(c); cc["_text"] = text
        cc["accept"] = c["accept"]            # cikti ingilizce -> EN accept dizeleri
        try:
            r, dt = call(cfg["mid"], build_prompt(tmpl, cfg["vars"], text, a.schema, catalog),
                         cfg["stop"], schema)
            if r.get("error"):
                pred, out = {"calls": [], "multi": None, "unsup": None, "err": True}, ""
            else:
                out = r.get("response") or ""
                pred = parse(a.schema, out)
                lat.append(dt)
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], e))
            pred, out = {"calls": [], "multi": None, "unsup": None, "err": True}, ""
        per_case.append((cc, pred))
        raws.append(out[:400])

    s = summarize(per_case, lat, a.schema)
    out = {"model": a.model, "schema": a.schema, "lang": "trans_tr2en", "tier": a.tier,
           "catalog_size": len(catalog), "n_cases": len(CASES),
           "summary": {k: v for k, v in s.items() if k != "bad"},
           "errors": [{"id": b[0], "cat": b[1], "text": b[2], "why": b[3],
                       "pred_tools": b[4], "pred_args": b[5]} for b in s["bad"]],
           "raw": [{"id": c["id"], "cat": c["cat"], "text": c["_text"],
                    "pred_tools": [x["tool"] for x in p["calls"]],
                    "pred_calls": p["calls"], "err": p["err"], "out": rw}
                   for (c, p), rw in zip(per_case, raws)],
           "latencies_ms": [round(x) for x in lat]}
    with open(a.out, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    S = out["summary"]
    print("  recall %.1f | arg(dogru tool) %.1f | trap-abstain %.1f | high-abstain %.1f | p50 %sms" % (
        S["recall_pct"], S["arg_ok_given_right_tool_pct"], S["trap_abstain_pct"],
        S["high_abstain_pct"], S["lat_p50_ms"]))
    print("  by_cat:", json.dumps(S["by_cat"], ensure_ascii=False))


if __name__ == "__main__":
    main()
