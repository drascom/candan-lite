#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""results3_*.json -> ana tablo + kategori kirilimi + semantik-komsu analizi."""
import glob, json, sys
from collections import Counter

rows = []
for f in sorted(glob.glob("results3_*.json")):
    d = json.load(open(f))
    s = d["summary"]
    rows.append((d["model"], d["quant"], d["lang"], d["tier"], s, d))

ORDER = {"qwen35-4b-q8": 0, "qwen35-4b-q6": 1, "qwen35-4b-q5": 2, "qwen35-4b-q4": 3,
         "xlam2-3b-q8": 4, "xlam2-3b-q6": 5, "xlam2-3b-q5": 6, "nemotron3-nano-4b": 7}
rows.sort(key=lambda r: (ORDER.get(r[0], 9), r[2], r[3]))

H = ("model", "quant", "lg", "tier", "recall", "arg", "TUZAK-abst", "komsu", "chat", "ctx",
     "know", "multi", "high-abst", "high-FIRE", "old35", "p50ms", "VRAM")
print("| " + " | ".join(H) + " |")
print("|" + "|".join(["---"] * len(H)) + "|")
for m, q, lg, tier, s, d in rows:
    bc = s["by_cat"]
    g = lambda k: ("%.0f" % bc[k]["pct"]) if k in bc else "-"
    print("| %s | %s | %s | %s | %.0f | %.0f | **%.0f** | %s | %s | %s | %s | %s | %.0f | **%.0f** | %s | %d | %.1fGB |" % (
        m, q, lg, tier, s["recall_pct"], s["arg_ok_pct"], s["trap_abstain_pct"],
        g("trap_neigh"), g("trap_chat"), g("trap_ctx"), g("trap_know"), g("multi"),
        s["high_abstain_pct"], s["high_fired_pct"], s["old35"],
        s["lat_p50_ms"] or 0, (d["vram_model_mib"] or 0) / 1024.0))

print("\n\n### Abstain gerekirken cagrilan tool'lar (semantik komsu yapistirma)")
for m, q, lg, tier, s, d in rows:
    if s["splat_targets"]:
        top = ", ".join("%s×%d" % (k, v) for k, v in list(s["splat_targets"].items())[:6])
        print("- **%s %s %s/%s** (%d hata): %s" % (m, q, lg, tier, sum(s["splat_targets"].values()), top))

print("\n\n### Hata ornekleri (tier=low, EN — uretim kosulu)")
for m, q, lg, tier, s, d in rows:
    if tier != "low" or lg != "en":
        continue
    print("\n**%s %s** — %d hata" % (m, q, len(d["errors"])))
    for e in d["errors"][:14]:
        print("  - [%s/%s] \"%s\" -> %s %s" % (e["id"], e["cat"], e["text"], e["pred_tool"],
                                              json.dumps(e["pred_args"], ensure_ascii=False)[:70]))
