#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FunctionGemma-270M router benchmark — NATIVE format, zero-shot.

Ayni 137+ vaka, ayni 23 low-tier katalog, ayni metrikler (bench4.summarize yeniden
kullaniliyor -> skorlama BIREBIR ayni). TEK fark: cikti sozlesmesi.

FunctionGemma'nin KENDI formati (grammar/json_schema ZORLANMIYOR — test edilen sey bu):

  prompt  : <start_of_turn>developer
            <start_function_declaration>declaration:NAME{description:<escape>..<escape>,
              parameters:{...}}<end_function_declaration> ...<end_of_turn>
            <start_of_turn>user\n TEXT <end_of_turn>\n<start_of_turn>model
            (llama-server --jinja + /v1/chat/completions "tools" ile OTOMATIK uretiliyor)

  cikti   : <start_function_call>call:NAME{arg:<escape>val<escape>,...}<end_function_call>
  ABSTAIN : fonksiyon cagrisi YOK -> duz metin (ör. "I cannot assist with...").
            Ayri bir "no_tool" TOKEN'i YOK; native abstain sinyali = cagri yoklugu.

NOT: llama.cpp bu formati tool_calls'a PARSE ETMIYOR (message.tool_calls = null),
     bu yuzden ham content'i kendimiz ayristiriyoruz.
NOT: model <end_function_call>'dan sonra durmuyor (bitmeyen uretim) -> stop dizisi
     sart. Uretim sozlesmesi = ILK cagri.
"""
import argparse, json, re, subprocess, time

import requests

from router_set import CASES, catalog_for
from bench4 import summarize

FG = "http://127.0.0.1:8081"

CALL_RE = re.compile(r"<start_function_call>\s*call:([A-Za-z0-9_]+)\s*\{(.*?)\}\s*(?:<end_function_call>|$)", re.S)
ARG_RE = re.compile(r"([A-Za-z0-9_]+)\s*:\s*<escape>(.*?)<escape>", re.S)


def parse_fg(out, valid):
    """FunctionGemma native cikti -> {"calls":[...], "multi":None, "unsup":None, "err":bool}

    Cagri yok  -> calls=[] (ABSTAIN — modelin native sinyali).
    err        : cagri VAR ama tool adi katalogda yok (halusinasyon) veya bicim bozuk.
    """
    calls, err = [], False
    for m in CALL_RE.finditer(out):
        name, body = m.group(1), m.group(2)
        if name not in valid:
            err = True                       # katalog disi tool -> gecersiz
            continue
        calls.append({"tool": name, "args": {k: v for k, v in ARG_RE.findall(body)}})
    # cagri isareti var ama hicbiri ayristirilamadi -> bicim hatasi
    if not calls and "<start_function_call>" in out:
        err = True
    return {"calls": calls, "multi": None, "unsup": None, "err": err}


def vram_used():
    try:
        o = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
        return int(o.strip().splitlines()[0])
    except Exception:
        return None


def call(tools, text, npred=256):
    p = {"model": "fg270", "messages": [{"role": "user", "content": text}],
         "tools": tools, "temperature": 0, "max_tokens": npred,
         "stop": ["<end_function_call>", "<end_of_turn>"]}
    t0 = time.time()
    r = requests.post(FG + "/v1/chat/completions", json=p, timeout=300).json()
    return r, (time.time() - t0) * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "tr", "trans"],
                    help="trans = TR cumlelerin opus-mt-tr-en ile CEVIRILMISI (ceviri katmani senaryosu)")
    ap.add_argument("--tier", default="low", choices=["full", "low"])
    ap.add_argument("--first-only", action="store_true", default=True,
                    help="uretim sozlesmesi: yalnizca ILK cagri (model durmuyor)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    catalog = catalog_for(a.tier)
    names = {t["function"]["name"] for t in catalog}
    tools = [{"type": "function", "function": t["function"]} for t in catalog]

    print(">>> functiongemma-270m-it Q8 [native/%s/tier=%s] (%d tool, %d vaka)" % (
        a.lang, a.tier, len(catalog), len(CASES)), flush=True)

    vram_before = vram_used()
    for _ in range(3):
        try:
            call(tools, "what time is it right now", npred=32)
        except Exception as e:
            print("  warmup err:", e)
    time.sleep(2)
    vram_after = vram_used()

    trans = json.load(open("translated_tr2en.json")) if a.lang == "trans" else {}

    per_case, lat, raws = [], [], []
    for c in CASES:
        text = trans[c["id"]] if a.lang == "trans" else c[a.lang]
        cc = dict(c); cc["_text"] = text
        # ceviri ciktisi INGILIZCE -> EN accept dizeleri gecerli
        cc["accept"] = c["accept_tr"] if a.lang == "tr" else c["accept"]
        try:
            r, dt = call(tools, text)
            if r.get("error"):
                print("  %s API ERR: %s" % (c["id"], str(r["error"])[:110]))
                pred, out = {"calls": [], "multi": None, "unsup": None, "err": True}, ""
            else:
                out = (r["choices"][0]["message"].get("content") or "")
                pred = parse_fg(out, names)
                if a.first_only and len(pred["calls"]) > 1:
                    pred["calls"] = pred["calls"][:1]
                lat.append(dt)
        except Exception as e:
            print("  %s EXC: %s" % (c["id"], e))
            pred, out = {"calls": [], "multi": None, "unsup": None, "err": True}, ""
        per_case.append((cc, pred))
        raws.append(out[:400])

    s = summarize(per_case, lat, "single")
    out = {"model": "functiongemma-270m-it", "quant": "Q8_0", "schema": "native",
           "lang": a.lang, "tier": a.tier,
           "catalog_size": len(catalog), "n_cases": len(CASES),
           "vram_model_mib": (vram_after - vram_before) if (vram_before and vram_after) else None,
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
    print("  recall %.1f | arg %.1f | trap-abstain %.1f | high-abstain %.1f | p50 %sms | parse-err %d" % (
        S["recall_pct"], S["arg_ok_given_right_tool_pct"], S["trap_abstain_pct"],
        S["high_abstain_pct"], S["lat_p50_ms"], S["parse_errors"]))
    print("  by_cat:", json.dumps(S["by_cat"], ensure_ascii=False))


if __name__ == "__main__":
    main()
