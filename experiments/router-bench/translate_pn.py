#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PN vakalarini opus-mt-tc-big-tr-en ile cevir + gecikme olc. SUNUCUDA kosar (GPU)."""
import json, sys, time

import torch
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

from pn_set import PN_CASES

M = "Helsinki-NLP/opus-mt-tc-big-tr-en"
dev = "cuda" if torch.cuda.is_available() else "cpu"
tok = AutoTokenizer.from_pretrained(M)
mdl = AutoModelForSeq2SeqLM.from_pretrained(M).to(dev).eval()


def tr2en(s):
    b = tok([s], return_tensors="pt", padding=True).to(dev)
    with torch.no_grad():
        o = mdl.generate(**b, max_new_tokens=64, num_beams=1)
    return tok.decode(o[0], skip_special_tokens=True)


for _ in range(3):
    tr2en("merhaba dunya")

out, lat = {}, []
for c in PN_CASES:
    t0 = time.perf_counter()
    en = tr2en(c["tr"])
    lat.append((time.perf_counter() - t0) * 1000)
    out[c["id"]] = en
    print("%s  %-45s -> %s" % (c["id"], c["tr"], en), flush=True)

lat.sort()
print("\n%s ceviri gecikme: p50=%.0fms p95=%.0fms (dev=%s)" % (
    len(lat), lat[len(lat) // 2], lat[int(0.95 * len(lat))], dev))
json.dump(out, open("pn_translated.json", "w"), ensure_ascii=False, indent=1)
