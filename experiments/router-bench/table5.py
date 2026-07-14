#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""unsupported_request deneyi — flag (baseline) vs flag2/flag2b (yeni alan) vs
flagp (yalniz METIN, alan yok) vs *neg (tool tarifine negatif kapsam).

Kullanim: python3 table5.py en    |    python3 table5.py tr
"""
import json, sys

LANG = sys.argv[1] if len(sys.argv) > 1 else "en"
VARIANTS = ["flag", "flagp", "flag2", "flag2b", "flagneg", "flag2neg"]
ROWS = [
    ("recall", "recall_pct"), ("arg", "arg_ok_pct"),
    ("trap_abstain", "trap_abstain_pct"), ("high_abstain", "high_abstain_pct"),
    ("multi_flag_tp", "multi_flag_tp"), ("multi_flag_fp", "multi_flag_fp"),
    ("unsup_escape%", "unsup_escape_pct"), ("p50_ms", "lat_p50_ms"),
    ("parse_err", "parse_errors"),
]
CATS = ["tool", "pair", "arg", "high", "trap_neigh", "trap_chat", "trap_ctx", "trap_know"]

D = {}
for v in VARIANTS:
    try:
        D[v] = json.load(open("results_qwen35_%s_%s.json" % (LANG, v)))
    except FileNotFoundError:
        pass
keys = [v for v in VARIANTS if v in D]
w = 10
print("=== %s ===" % LANG.upper())
print("metrik".ljust(15) + "".join(k.rjust(w) for k in keys))
for label, k in ROWS:
    print(label.ljust(15) + "".join(str(D[x]["summary"].get(k, "-")).rjust(w) for x in keys))
print("-- kategori ok/n --")
for c in CATS:
    cells = []
    for x in keys:
        v = D[x]["summary"]["by_cat"][c]
        cells.append(("%d/%d" % (v["ok"], v["n"])).rjust(w))
    print(("  " + c).ljust(15) + "".join(cells))
print("-- CANLI HATALAR: x01 kombi / x07+n14 perde / n13 kombi(cikplak) --")
for cid in ("x01", "n13", "x07", "n14"):
    row = []
    for x in keys:
        r = next(r for r in D[x]["raw"] if r["id"] == cid)
        t = (r["pred_tools"] or ["null"])[0]
        row.append(t[:9] + ("+U" if r.get("unsupported") else ""))
    print(("  " + cid).ljust(15) + "".join(s.rjust(w) for s in row))
print("-- gereksiz kacis --")
for x in keys:
    s = D[x]["summary"]
    if s.get("unsup_escape_n"):
        print("  %-9s %s" % (x, [(i, t) for i, _, t in s["unsup_escape_detail"]]))
print("-- kalan trap_neigh hatalari --")
for x in keys:
    print("  %-9s %s" % (x, [(e["id"], e["pred_tools"][0]) for e in D[x]["errors"]
                             if e["cat"] == "trap_neigh"]))
