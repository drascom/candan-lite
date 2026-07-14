#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bench4 sema deneyi -> kiyas tablosu. Baseline = results3_qwen35-4b-q8_{en,tr}_low.json"""
import glob, json
from collections import Counter

rows = []
# baseline (bench3, single sema)
for lg in ("en", "tr"):
    try:
        d = json.load(open("results3_qwen35-4b-q8_%s_low.json" % lg))
    except IOError:
        continue
    s = d["summary"]
    bc = s["by_cat"]
    rows.append(dict(schema="single (BASELINE)", lang=lg, s=s, bc=bc, d=d, base=True))

for f in sorted(glob.glob("results_qwen35_*_*.json")):
    d = json.load(open(f))
    rows.append(dict(schema=d["schema"], lang=d["lang"], s=d["summary"],
                     bc=d["summary"]["by_cat"], d=d, base=False))

ORD = {"single (BASELINE)": 0, "single": 1, "list": 2, "list_null": 3, "list_guard": 4, "flag": 5}
rows.sort(key=lambda r: (r["lang"], ORD.get(r["schema"], 9)))

H = ("sema", "lg", "recall", "arg", "FAZLA-tool", "TUZAK-abst", "komsu", "chat", "ctx", "know",
     "high-abst", "multi-TAM", "multi-abst", "old35", "p50ms")
print("| " + " | ".join(H) + " |")
print("|" + "|".join(["---"] * len(H)) + "|")
for r in rows:
    s, bc = r["s"], r["bc"]
    g = lambda k: ("%.0f" % bc[k]["pct"]) if k in bc else "-"
    ex = "%.0f%% (%d)" % (s["extra_tool_pct"], s["extra_tool_n"]) if "extra_tool_pct" in s else "n/a"
    mfull = ("%d/5" % s["multi_full_n"]) if "multi_full_n" in s else "0/5"
    mabst = ("%d/6" % s["multi_abstain_n"]) if "multi_abstain_n" in s else ("%d/6" % round(s.get("multi_abstain_pct", 0) * 6 / 100))
    print("| %s | %s | %.0f | %.0f | %s | **%.0f** | %s | %s | %s | %s | %.0f | **%s** | %s | %s | %d |" % (
        r["schema"], r["lang"], s["recall_pct"], s["arg_ok_pct"], ex, s["trap_abstain_pct"],
        g("trap_neigh"), g("trap_chat"), g("trap_ctx"), g("trap_know"),
        s["high_abstain_pct"], mfull, mabst, s["old35"], s["lat_p50_ms"] or 0))

print("\n### multi_intent bayragi (flag semasi)")
for r in rows:
    s = r["s"]
    if r["schema"] != "flag":
        continue
    print("- **%s**: multi yakalama %d/6 (kacan %d) | YANLIS ALARM %d/131 (%.1f%%) -> gereksiz escalate" % (
        r["lang"], s["multi_flag_tp"], s["multi_flag_fn"], s["multi_flag_fp"], s["multi_flag_fp_pct"]))

print("\n### Abstain gerekirken cagrilan tool'lar")
for r in rows:
    s = r["s"]
    if s["splat_targets"]:
        top = ", ".join("%s×%d" % (k, v) for k, v in list(s["splat_targets"].items())[:8])
        print("- **%s %s** (%d hata): %s" % (r["schema"], r["lang"], sum(s["splat_targets"].values()), top))
