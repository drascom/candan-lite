#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TR-direkt vs EN-only(ceviri) vs TR+EN(iki metin birlikte) — AYNI set, AYNI metrikler.

  --variant tr    : router'a TURKCE cumle (bugunku uretim / baseline)
  --variant en    : router'a yalnizca CEVIRI (opus-mt tr->en)
  --variant tren  : router'a IKI METIN birlikte — arguman TURKCE orijinalden,
                    tool secimi ingilizce ceviriden.
  --variant tren2 : tren'in daha kisa/sert varyanti (prompt duyarliligi kontrolu)

Vaka seti = router_set.CASES (139) + pn_set.PN_CASES (20, ozel-isim agirlikli).
PN vakalari AYRI skorlanir: olcut "ozel ismin TURKCE hali argumanda duruyor mu".
bench4.py'nin prompt/cagri/skorlama fonksiyonlarini AYNEN kullanir.
"""
import argparse, json, statistics

from router_set import CASES
from pn_set import PN_CASES
from bench4 import (MODELS, schema_for, load_tmpl, build_prompt, call, parse, summarize,
                    score_args, catalog_for)

# ── TR+EN prompt kalibi ─────────────────────────────────────────────────────
# Sira ONEMLI: TURKCE once (arguman kaynagi), ingilizce sonra (tool secimi).
TREN = (
    "User said (Turkish — ORIGINAL). Take ALL argument values from THIS text and copy names, "
    "song titles, places and brands EXACTLY as written here; never translate them:\n"
    "{tr}\n\n"
    "English translation (use this ONLY to understand WHICH tool is needed):\n"
    "{en}"
)
TREN2 = (
    "Turkish (original): {tr}\n"
    "English (machine translation): {en}\n\n"
    "Pick the tool using the English translation. Fill the arguments from the Turkish original — "
    "names, song titles, places and brands must stay in Turkish, exactly as the user said them."
)
# tren/tren2 ABSTAIN'i COKERTTI (trap_neigh 72.7 -> 9.1): "tool'u sec / argumanlari doldur"
# emri, cumleyi bir TOOL CAGRISI olduguna ikna ediyor. tren3/tren4 EMIR VERMEZ — ceviriyi
# yalnizca VERI olarak koyar; tool/abstain karari INSTR'e (flag semasi) birakilir.
TREN3 = "{tr}\n\n(English translation of the sentence above: {en})"
TREN4 = (
    "{tr}\n\n(English translation of the sentence above, to help you understand it: {en})\n"
    "The translation is only an aid; it may mangle names. IF you call a tool, copy the argument "
    "values from the Turkish sentence, not from the translation."
)
TPL = {"tren": TREN, "tren2": TREN2, "tren3": TREN3, "tren4": TREN4}


def text_for(variant, c, trans):
    if variant == "tr":
        return c["tr"]
    if variant == "en":
        return trans[c["id"]]
    return TPL[variant].format(tr=c["tr"], en=trans[c["id"]])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen35-4b-q8", choices=list(MODELS))
    ap.add_argument("--variant", required=True, choices=["tr", "en", "tren", "tren2", "tren3", "tren4"])
    ap.add_argument("--schema", default="flag")
    ap.add_argument("--tier", default="low")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    cfg = MODELS[a.model]
    catalog = catalog_for(a.tier)
    names = [t["function"]["name"] for t in catalog]
    schema = schema_for(a.schema, names)
    tmpl = load_tmpl(cfg["tmpl"])
    trans = json.load(open("translated_tr2en.json"))
    trans.update(json.load(open("pn_translated.json")))

    print(">>> %s [variant=%s schema=%s tier=%s] %d+%d vaka" % (
        a.model, a.variant, a.schema, a.tier, len(CASES), len(PN_CASES)), flush=True)

    for _ in range(3):
        try:
            call(cfg["mid"], build_prompt(tmpl, cfg["vars"], "what time is it right now",
                                          a.schema, catalog), cfg["stop"], schema, npred=32)
        except Exception as e:
            print("  warmup err:", e)

    def run(c):
        text = text_for(a.variant, c, trans)
        try:
            r, dt = call(cfg["mid"], build_prompt(tmpl, cfg["vars"], text, a.schema, catalog),
                         cfg["stop"], schema)
            if r.get("error"):
                return text, {"calls": [], "multi": None, "unsup": None, "err": True}, None
            return text, parse(a.schema, r.get("response") or ""), dt
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], e))
            return text, {"calls": [], "multi": None, "unsup": None, "err": True}, None

    # ── ana set (139) ──
    per_case, lat = [], []
    for c in CASES:
        text, pred, dt = run(c)
        cc = dict(c)
        cc["_text"] = text
        # arguman kabul dizeleri: EN-only'de cikti ingilizce -> EN accept; TR ve TR+EN'de TR accept
        cc["accept"] = c["accept"] if a.variant == "en" else c["accept_tr"]
        if dt:
            lat.append(dt)
        per_case.append((cc, pred))
    s = summarize(per_case, lat, a.schema)

    # ── PN seti (20) — accept HER VARYANTTA TURKCE ORIJINAL ──
    pn_rows, pn_tool_ok, pn_arg_ok = [], 0, 0
    for c in PN_CASES:
        text, pred, dt = run(c)
        tools = [x["tool"] for x in pred["calls"]]
        hit = [x for x in pred["calls"] if x["tool"] in c["gold"]]
        t_ok = bool(hit) and len(tools) == 1
        a_ok = bool(hit) and score_args(hit[0]["args"], c["accept"])
        pn_tool_ok += t_ok
        pn_arg_ok += a_ok
        pn_rows.append({"id": c["id"], "tr": c["tr"], "en": trans[c["id"]],
                        "gold": sorted(c["gold"]), "pred_tools": tools,
                        "args": hit[0]["args"] if hit else (pred["calls"][0]["args"] if pred["calls"] else {}),
                        "tool_ok": t_ok, "name_ok": a_ok})
        if dt:
            lat.append(dt)

    n = len(PN_CASES)
    res = {"variant": a.variant, "schema": a.schema, "model": a.model,
           "summary": {k: v for k, v in s.items() if k != "bad"},
           "errors": [{"id": b[0], "cat": b[1], "text": b[2], "why": b[3], "pred_tools": b[4],
                       "pred_args": b[5]} for b in s["bad"]],
           "pn": {"n": n, "tool_ok": pn_tool_ok, "tool_pct": round(100.0 * pn_tool_ok / n, 1),
                  "name_ok": pn_arg_ok, "name_pct": round(100.0 * pn_arg_ok / n, 1),
                  "rows": pn_rows},
           "lat_p50_all_ms": round(statistics.median(lat)) if lat else None,
           "raw": [{"id": c["id"], "cat": c["cat"], "text": c["_text"],
                    "pred_tools": [x["tool"] for x in p["calls"]],
                    "pred_calls": p["calls"], "multi_flag": p["multi"]} for c, p in per_case]}
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    S = res["summary"]
    print("\n===== variant=%s =====" % a.variant)
    print("  recall            %.1f" % S["recall_pct"])
    print("  arg(dogru tool)   %.1f" % S["arg_ok_given_right_tool_pct"])
    print("  trap_neigh        %.1f  (%s)" % (S["by_cat"]["trap_neigh"]["pct"],
                                              S["by_cat"]["trap_neigh"]["ok"]))
    print("  trap_all          %.1f" % S["trap_abstain_pct"])
    print("  high_abstain      %.1f" % S["high_abstain_pct"])
    print("  multi_flag tp/fn/fp %s/%s/%s (fp%% %.1f)" % (
        S["multi_flag_tp"], S["multi_flag_fn"], S["multi_flag_fp"], S["multi_flag_fp_pct"]))
    print("  PN tool %.1f%% | PN OZEL ISIM KORUNDU %.1f%% (%d/%d)" % (
        res["pn"]["tool_pct"], res["pn"]["name_pct"], pn_arg_ok, n))
    print("  lat p50 %sms (router cagrisi, ceviri HARIC)" % res["lat_p50_all_ms"])
    for r in pn_rows:
        if not r["name_ok"]:
            print("    PN-KAYIP %s %-38s tools=%s args=%s" % (
                r["id"], r["tr"], r["pred_tools"], json.dumps(r["args"], ensure_ascii=False)))
    print("out:", a.out)


if __name__ == "__main__":
    main()
