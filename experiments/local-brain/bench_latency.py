#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GERCEKCI BEYIN PROMPT'u ile gecikme olcumu (llama-server, streaming).

Neden ayri bir olcum: router'in ~400ms'si KUCUK ve SABIT bir prompt'a (2.3k token
tool blogu) dayaniyor; prefix KV-cache'e tam oturuyor. BEYNIN prompt'u ise
persona + hafiza + KONUSMA GECMISI + 30 tool = cok daha buyuk ve her turda BUYUYOR.

Olculen:
  TTFT       : ilk token'a kadar gecen sure  <-- SESLI ASISTANDA EN ONEMLI METRIK
               (TTS ilk cumle hazir olunca konusmaya baslar)
  tok/s      : decode hizi
  toplam     : tool cagrisi (kisa JSON) ve sohbet cevabi (~50 token) icin uctan uca

Iki rejim:
  cold : KV-cache YOK (cache_prompt=false + rastgele tuz) -> oturumun ILK turu / slot devri
  warm : prefix cache ISABET (ayni sistem prompt'u, ardisik turlar) -> tipik orta-sohbet turu
"""
import argparse
import json
import random
import statistics
import string
import time
from pathlib import Path

import requests
import sys

sys.path.insert(0, "/root/router-bench")
from router_set import catalog_for  # noqa: E402

REPO = Path(__file__).resolve().parent

# --- GERCEK repo icerigi (worker/pi_brain.py'nin sistem prompt'una ekledikleri) -----
PERSONA = """# Persona: Candan

Adın **Candan**. Sıcak, güler yüzlü ve pratik bir arkadaş gibisin.

- Samimi ama saygılı konuş; kullanıcıya "sen" diye hitap et.
- Enerjik ve pozitifsin, ama abartılı değil.
- Gereksiz nezaket kalıplarıyla vakit kaybettirme; doğrudan yardım et.
- Konuşma dili kullan: kısa cümleler, doğal geçişler.
- Kullanıcı üzgün ya da sıkıntılıysa önce anlayışlı ol, sonra çözüme geç.
"""

AGENTS_MD = """# candan-lite — sesli ev asistanı

Sen bir evin ortak alanında çalışan, sesli konuşan bir asistansın. Cevapların
SESLİ OKUNUR: kısa, akıcı, noktalama açısından temiz olsun. Markdown, madde
işareti, kod bloğu, emoji KULLANMA — hepsi sesli okunduğunda gürültüdür.

- Cevap 1-3 cümle olsun; kullanıcı detay isterse uzat.
- Sayıları ve saatleri konuşma dilinde yaz ("dokuz buçuk", "yirmi derece").
- Emin değilsen sor; uydurma.
- Aynı odada birden fazla kişi olabilir; kime cevap verdiğini bil.
- Bir tool çağırman gerekiyorsa çağır; kullanıcıya "tool çağırıyorum" deme.
"""

SOUL = """- [2026-07-13] Ayhan eğlenceli bir şey söylediğinde, şaka yaptığında veya beklenmedik yeni bir konu açtığında uygun yerde kısa, nazik ve eğlenceli bir karşılık ver.
- [2026-07-14] Ayhan'a cevap verirken cümlelerin sonunda adını söyleme.
"""

FAMILY = """# Aile Ortak Hafızası

- Ev: Kadıköy, üç katlı; salon ve mutfak ortak alan, üst katta yatak odaları.
- Ortak takvim / alışkanlıklar: akşam yemeği 20:00 civarı; Pazar sabahı kahvaltı geç.
- Önemli tarihler: Neva'nın doğum günü 12 Mart, Ayşe'nin sınavı 24 Temmuz.
- Ev halkı: Ayhan (baba), Neva (kız, 14), Ayşe (kız, 17), İsmet (dede).
"""

NOTES = """# Ayhan — Temmuz 2026 notları

- [2026-07-02] Sabahları kahveyi sade içiyor, süt istemiyor.
- [2026-07-05] İşten genelde 18:30'da dönüyor; o saatten önce sesli hatırlatma isteme.
- [2026-07-08] Yeni bir proje başlattı: "candan-lite" — ev asistanı. Akşamları üzerinde çalışıyor.
- [2026-07-09] Neva'nın okul servisi sabah 7:40'ta geliyor.
- [2026-07-11] Spor salonuna Salı ve Perşembe gidiyor, akşam 19:00.
- [2026-07-13] Kombi arızalıydı, tamirci Pazartesi gelecek.
"""

# Birkac turluk konusma gecmisi (gercekci: sesli asistanda turlar kisa)
HISTORY = [
    ("user", "Günaydın Candan, bugün hava nasıl?"),
    ("assistant", "Günaydın! Bugün İstanbul'da hava açık, öğleden sonra yirmi sekiz dereceye kadar çıkıyor. Akşam serinliyor, ince bir şey al yanına."),
    ("user", "Neva'nın servisi kaçta geliyordu?"),
    ("assistant", "Sabah yedi kırkta geliyor. Yaklaşık on beş dakikan var."),
    ("user", "Tamam, akşam spor var mıydı bugün?"),
    ("assistant", "Bugün Salı, evet. Akşam yediye spor salonu var."),
    ("user", "Bir de tamirci ne zaman gelecekti, kombi için?"),
    ("assistant", "Pazartesi gelecek demiştin. Bir hatırlatma kurayım mı?"),
]


def system_prompt(catalog):
    parts = [AGENTS_MD, PERSONA, SOUL, FAMILY, NOTES]
    parts.append("# Tools\n\nYou have access to the following functions:\n\n<tools>")
    for t in catalog:
        parts.append("\n" + json.dumps({"type": "function", "function": t["function"]},
                                       ensure_ascii=False))
    parts.append("\n</tools>\n")
    return "\n\n".join(parts)


def messages(catalog, user_text, salt=None):
    sysmsg = system_prompt(catalog)
    if salt:  # cache'i BOZ (cold rejimi)
        sysmsg = "<!-- %s -->\n" % salt + sysmsg
    ms = [{"role": "system", "content": sysmsg}]
    for role, txt in HISTORY:
        ms.append({"role": role, "content": txt})
    ms.append({"role": "user", "content": user_text})
    return ms


def stream_once(url, ms, max_tokens, cache):
    payload = {"messages": ms, "temperature": 0.3, "max_tokens": max_tokens,
               "stream": True, "cache_prompt": cache,
               "stream_options": {"include_usage": True},
               "chat_template_kwargs": {"enable_thinking": False}}
    t0 = time.perf_counter()
    ttft = None
    ntok = 0
    usage = {}
    text = []
    with requests.post(url + "/v1/chat/completions", json=payload, stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line or not line.startswith(b"data: "):
                continue
            chunk = line[6:]
            if chunk == b"[DONE]":
                break
            d = json.loads(chunk)
            if d.get("usage"):
                usage = d["usage"]
            for ch in d.get("choices") or []:
                piece = (ch.get("delta") or {}).get("content") or ""
                if piece:
                    if ttft is None:
                        ttft = (time.perf_counter() - t0) * 1000
                    ntok += 1
                    text.append(piece)
    total = (time.perf_counter() - t0) * 1000
    dec_ms = total - (ttft or total)
    tps = (ntok - 1) / (dec_ms / 1000) if ntok > 1 and dec_ms > 0 else 0.0
    return {"ttft_ms": ttft, "total_ms": total, "n_tok": ntok, "tok_s": tps,
            "usage": usage, "text": "".join(text)}


def run(url, catalog, label, user_text, max_tokens, cold, reps):
    out = []
    for i in range(reps):
        salt = "".join(random.choices(string.ascii_lowercase, k=24)) if cold else None
        ms = messages(catalog, user_text, salt=salt)
        r = stream_once(url, ms, max_tokens, cache=not cold)
        out.append(r)
        time.sleep(0.3)
    def med(k):
        vals = [x[k] for x in out if x[k]]
        return round(statistics.median(vals), 1) if vals else None
    return {"label": label, "cold": cold, "reps": reps,
            "prompt_tokens": out[-1]["usage"].get("prompt_tokens"),
            "ttft_ms_p50": med("ttft_ms"), "total_ms_p50": med("total_ms"),
            "tok_s_p50": med("tok_s"),
            "n_tok_p50": med("n_tok"),
            "sample": out[-1]["text"][:200],
            "all_ttft": [round(x["ttft_ms"] or 0) for x in out],
            "all_total": [round(x["total_ms"]) for x in out]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8090")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=5)
    a = ap.parse_args()

    catalog = catalog_for("full")     # BEYIN 30 tool'un HEPSINI gorur (high dahil)
    sp = system_prompt(catalog)
    print(">>> %s | sistem prompt %d karakter | 30 tool | %d turluk gecmis"
          % (a.tag, len(sp), len(HISTORY) // 2), flush=True)

    res = []
    # 1) TOOL CAGRISI: kisa cikti (JSON/tool_call) — "isigi kapat" gibi
    res.append(run(a.url, catalog, "tool_cagrisi (cold)", "salondaki ışıkları kapat", 48, True, a.reps))
    res.append(run(a.url, catalog, "tool_cagrisi (warm)", "salondaki ışıkları kapat", 48, False, a.reps))
    # 2) SOHBET CEVABI: ~50 token
    res.append(run(a.url, catalog, "sohbet ~50tok (cold)", "Bugün biraz yorgunum, ne dersin akşam ne yapayım?", 64, True, a.reps))
    res.append(run(a.url, catalog, "sohbet ~50tok (warm)", "Bugün biraz yorgunum, ne dersin akşam ne yapayım?", 64, False, a.reps))
    # 3) UZUN cevap (tok/s icin temiz olcum)
    res.append(run(a.url, catalog, "uzun 200tok (warm)", "Bana kısaca bu haftanın planını özetler misin?", 200, False, max(3, a.reps // 2)))

    with open(a.out, "w") as f:
        json.dump({"tag": a.tag, "sys_prompt_chars": len(sp), "results": res}, f,
                  ensure_ascii=False, indent=2)

    print("\n%-24s %8s %8s %8s %8s %8s" % ("senaryo", "p_tok", "TTFT", "tok/s", "toplam", "n_tok"))
    for r in res:
        print("%-24s %8s %8s %8s %8s %8s" % (
            r["label"], r["prompt_tokens"], r["ttft_ms_p50"], r["tok_s_p50"],
            r["total_ms_p50"], r["n_tok_p50"]))
    print("\nornek cevap:", res[3]["sample"][:160])
    print("out:", a.out)


if __name__ == "__main__":
    main()
