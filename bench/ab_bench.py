# -*- coding: utf-8 -*-
"""A/B baseline harness — 26B vs 12B beyin. Modeli DEGISTIRMEZ, sadece olcer.

Canli ayarlarla ayni: /v1/chat/completions, sampling parametresi GONDERILMEZ
(pi de gondermiyor -> llama-server varsayilani). Tool sirasi worker/pi_brain.py:148
allowlist'inden BIREBIR alinmistir.

Kullanim:
  python3 ab_bench.py all            # hepsi (varsayilan N'lerle)
  python3 ab_bench.py tool_secim [N]
  python3 ab_bench.py soul_kalip [N]
  python3 ab_bench.py kontrol [N]
  python3 ab_bench.py gecikme [N]
  python3 ab_bench.py hata_yutma [N]

Cikti: her testin ham sonuclari stdout'a. --parallel 1 oldugu icin seri kosar,
istekler arasinda NEFES (SLEEP) birakir.
"""
import json
import pathlib
import sys
import time
import urllib.request
from collections import Counter

URL = "http://192.168.0.25:8082/v1/chat/completions"
SLEEP = 0.4          # tek serit beyin: kullanicinin turunu bogmamak icin nefes
TIMEOUT = 180


# ----------------------------------------------------------------- tool semasi
def f(ad, aciklama, props, zorunlu):
    return {"type": "function", "function": {
        "name": ad, "description": aciklama,
        "parameters": {"type": "object", "properties": props, "required": zorunlu}}}


T = {
    "reminder_add": f("reminder_add",
        "Set a timed reminder spoken aloud when due. Use in_minutes for relative times, "
        "or at for absolute times ('at 8pm' -> '20:00').",
        {"text": {"type": "string"}, "in_minutes": {"type": "integer"}, "at": {"type": "string"}},
        ["text"]),
    "memory_add": f("memory_add",
        "Store a durable FACT about the user or family. NOT a behaviour instruction, NOT a reminder.",
        {"text": {"type": "string"}, "scope": {"type": "string"}}, ["text"]),
    "soul_add": f("soul_add",
        "Store a DURABLE BEHAVIOUR instruction ('from now on answer briefly', 'always call me X').",
        {"text": {"type": "string"}, "scope": {"type": "string"}}, ["text"]),
    "memory_search": f("memory_search", "Search stored memories about the user and family.",
        {"query": {"type": "string"}}, ["query"]),
    "reminder_list": f("reminder_list", "List pending reminders.", {"limit": {"type": "number"}}, []),
    "reminder_cancel": f("reminder_cancel", "Cancel a pending reminder.",
        {"id": {"type": "string"}, "text": {"type": "string"}}, []),
    "web_search": f("web_search",
        "İnternette güncel bilgi ara. Eğitim verinde olmayan ya da güncel olabilecek şeyler "
        "(haber, hava durumu, skor, fiyat, 'şu an', 'bugün', 'son durum') için kullan. "
        "Sonuç: ilk birkaç web sonucunun başlık + kısa özeti (sade metin).",
        {"query": {"type": "string"}, "limit": {"type": "number"}}, ["query"]),
    "fetch_content": f("fetch_content", "Fetch and read the content of a URL.",
        {"url": {"type": "string"}}, ["url"]),
    "memory_consolidate": f("memory_consolidate", "Shrink profile/family file below the size limit.",
        {"file": {"type": "string"}, "text": {"type": "string"}}, ["file", "text"]),
}

# CANLI SIRA — worker/pi_brain.py:148 allowlist'i ile BIREBIR. Sira dekoratif degil.
CANLI_SIRA = ["reminder_add", "memory_add", "soul_add", "memory_search", "reminder_list",
              "reminder_cancel", "web_search", "fetch_content", "memory_consolidate"]

CIPLAK_TOOLS = [T[a] for a in CANLI_SIRA]
CIPLAK_SISTEM = ("Sen Candan, Türkçe konuşan bir sesli ev asistanısın. Kısa konuş (1-3 cümle).\n"
                 "Güncel/değişken bilgi gerektiğinde web_search çağır. Zamanlı istekte reminder_add. "
                 "Kalıcı olguda memory_add.")

# CANLI (varsayilan): pi'nin modele gonderdigi GERCEK govde — proxy ile yakalandi.
# Tahmin yok: pi taban prompt'u + AGENTS.md + persona + family.md + soul.md + kimlik + mode-switch.
_FIX = pathlib.Path(__file__).parent / "canli-sistem-prompt.json"
CANLI_SISTEM = CANLI_TOOLS = None
if _FIX.exists():
    _d = json.loads(_FIX.read_text())
    CANLI_SISTEM, CANLI_TOOLS = _d["messages"][0]["content"], _d["tools"]

# --ciplak bayragi ile kontrollu/minimal prompt'a dusulur (harness karsilastirmasi icin).
CIPLAK = "--ciplak" in sys.argv
if CIPLAK or CANLI_SISTEM is None:
    SISTEM, TOOLS, KIP = CIPLAK_SISTEM, CIPLAK_TOOLS, "CIPLAK (minimal prompt, 9 tool)"
else:
    SISTEM, TOOLS, KIP = CANLI_SISTEM, CANLI_TOOLS, "CANLI (gercek pi promptu, yakalanan govde)"


# ------------------------------------------------------------------ HTTP kati
def istek(mesajlar, tools=TOOLS, max_tokens=200, stream=False):
    g = {"messages": mesajlar, "max_tokens": max_tokens}
    if tools:
        g["tools"] = tools
    if stream:
        g["stream"] = True
        g["stream_options"] = {"include_usage": True}
    body = json.dumps(g, ensure_ascii=False).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def sor(kullanici, sistem=SISTEM, tools=TOOLS, mesajlar=None, max_tokens=200):
    """-> (tool_adi | '(YOK)', metin)"""
    m = mesajlar or [{"role": "system", "content": sistem},
                     {"role": "user", "content": kullanici}]
    with istek(m, tools, max_tokens) as r:
        msg = json.load(r)["choices"][0]["message"]
    c = msg.get("tool_calls") or []
    ad = c[0]["function"]["name"] if c else "(YOK)"
    return ad, (msg.get("content") or "").strip()


# ------------------------------------------------------- 1) TOOL SECIMI (N=20)
TOOL_SORULAR = [
    ("reminder_add", "On beş dakika sonra çamaşırı almayı hatırlat."),
    ("memory_add", "Kahvemi sütsüz içtiğimi not al."),
    ("soul_add", "Bundan sonra bana hep kısa cevap ver."),
]


def t_tool_secim(N=20):
    print(f"\n=== 1) TOOL SECIMI (canli 9 tool, canli sira) N={N} ===")
    print(f"{'beklenen':14} {'isabet':>8}  dagilim")
    print("-" * 72)
    for beklenen, soru in TOOL_SORULAR:
        c = Counter()
        for _ in range(N):
            ad, _m = sor(soru)
            c[ad] += 1
            time.sleep(SLEEP)
        d = ", ".join(f"{k}={v}" for k, v in c.most_common())
        print(f"{beklenen:14} {c[beklenen]:>5}/{N}  {d}")
        print(f"{'':14} soru: {soru}")


# --------------------------------------- 2) soul_add: kalipli vs kalipsiz (N=10)
SOUL_KALIPLI = [   # "bundan sonra" / "artik" / "her zaman" kalibi VAR
    "Bundan sonra bana hep kısa cevap ver.",
    "Artık benimle her zaman resmi konuş.",
]
SOUL_KALIPSIZ = [  # ayni kalici davranis istegi, kalip YOK
    "Bana kısa cevap ver.",
    "Benimle resmi konuş.",
]


def t_soul_kalip(N=10):
    print(f"\n=== 2) soul_add — kalipli vs kalipsiz N={N} (her prompt) ===")
    print(f"{'kosul':10} {'prompt':40} {'soul_add':>10}  dagilim")
    print("-" * 88)
    for etiket, kume in (("KALIPLI", SOUL_KALIPLI), ("KALIPSIZ", SOUL_KALIPSIZ)):
        for p in kume:
            c = Counter()
            for _ in range(N):
                ad, _m = sor(p)
                c[ad] += 1
                time.sleep(SLEEP)
            d = ", ".join(f"{k}={v}" for k, v in c.most_common())
            print(f"{etiket:10} {p[:38]:40} {c['soul_add']:>7}/{N}  {d}")


# --------------------------------------------------- 3) KONTROL KOSUSU (harness)
KONTROL = [
    ("Merhaba, nasılsın?", "(YOK)"),          # tool'suz sohbet -> tool cagrisi OLMAMALI
    ("Bana bir fıkra anlat.", "(YOK)"),
]


def t_kontrol(N=10):
    print(f"\n=== 3) KONTROL — harness dogrulama N={N} ===")
    print("Beklenti: tool'suz sohbet isteginde tool cagrisi YOK; web_search 0/N.")
    print(f"{'prompt':32} {'(YOK)':>8} {'web_search':>11}  dagilim")
    print("-" * 78)
    for p, _bek in KONTROL:
        c = Counter()
        for _ in range(N):
            ad, _m = sor(p)
            c[ad] += 1
            time.sleep(SLEEP)
        d = ", ".join(f"{k}={v}" for k, v in c.most_common())
        print(f"{p[:30]:32} {c['(YOK)']:>5}/{N} {c['web_search']:>8}/{N}  {d}")
    # Ikinci kontrol: tool listesi BOS gonderilirse hicbir sey cagrilamaz (harness sanity)
    ad, metin = sor("On beş dakika sonra çamaşırı almayı hatırlat.", tools=None)
    print(f"\ntool listesi BOS iken ayni hatirlatici istegi -> {ad}  (beklenen: (YOK))")
    print(f"  yanit: {metin[:100]!r}")


# ----------------------------------------------------- 4) GECIKME / HIZ (TTFT vb)
DOLGU = ("Kullanıcı ile geçmiş sohbet özeti. " + "Bu bir bağlam dolgusu cümlesidir; "
         "asistanın hafızasındaki eski konuşmaları temsil eder. ") * 400


def _kirp(metin, hedef_tok):
    """~4 karakter/token kabaca; sonra gercek n_prompt_tokens raporlanir."""
    return metin[: hedef_tok * 4]


def _stream_olc(mesajlar, max_tokens=160):
    t0 = time.time()
    ttft = None
    timings = usage = None
    with istek(mesajlar, tools=None, max_tokens=max_tokens, stream=True) as r:
        for ham in r:
            s = ham.decode("utf-8", "replace").strip()
            if not s.startswith("data: "):
                continue
            veri = s[6:]
            if veri == "[DONE]":
                break
            try:
                j = json.loads(veri)
            except Exception:
                continue
            ch = (j.get("choices") or [{}])[0]
            if ttft is None and (ch.get("delta") or {}).get("content"):
                ttft = time.time() - t0
            if j.get("timings"):
                timings = j["timings"]
            if j.get("usage"):
                usage = j["usage"]
    return ttft, timings, usage, time.time() - t0


GECIKME_KOSULLARI = [
    ("kisa (~50 tok)", 0),
    ("orta (~5k tok)", 5000),
    ("uzun (~13k tok)", 13000),
]


def t_gecikme(N=5):
    """SOGUK vs SICAK ayrimi SART.

    Sunucuda --cache-reuse 256 var; ayni prefix tekrar gonderilince prompt eval
    ATLANIR. Ilk olcumde pp ~10 tok/s / TTFT 0.12s cikti: bu prompt eval hizi DEGIL,
    cache isabetidir (birkac yeni token'a bolunen sure). Gercek prompt eval icin her
    kosuda prefix'i BOZMAK (essiz dolgu) gerekir.
      SOGUK = her tekrarda essiz rastgele prefix -> cache MISS -> gercek prompt eval.
      SICAK = ayni prefix tekrar -> cache HIT -> canlidaki ardisik turlarin durumu.
    """
    import uuid
    print(f"\n=== 4) GECIKME / HIZ — N={N} tekrar/kosul (stream, tool'suz) ===")
    print(f"{'kosul':16} {'kip':7} {'n_prompt':>9} {'TTFT s':>8} {'pp tok/s':>10} {'decode tok/s':>13}")
    print("-" * 72)
    for etiket, dolgu_tok in GECIKME_KOSULLARI:
        for kip in ("SOGUK", "SICAK"):
            ttfts, pps, dds, npt = [], [], [], []
            for _ in range(N):
                sis = SISTEM
                if dolgu_tok:
                    sis = SISTEM + "\n\n" + _kirp(DOLGU, dolgu_tok)
                if kip == "SOGUK":
                    # essiz prefix -> ortak on-ek kalmaz -> prompt eval gercekten kosar
                    sis = f"[oturum {uuid.uuid4()}]\n" + sis
                m = [{"role": "system", "content": sis},
                     {"role": "user", "content": "Bana kısaca deniz kenarında bir sabahı anlat."}]
                ttft, tim, usage, _w = _stream_olc(m)
                if ttft:
                    ttfts.append(ttft)
                if tim:
                    pps.append(tim.get("prompt_per_second") or 0.0)
                    dds.append(tim.get("predicted_per_second") or 0.0)
                if usage:
                    npt.append(usage.get("prompt_tokens") or 0)
                time.sleep(SLEEP)

            def med(v):
                return sorted(v)[len(v) // 2] if v else float("nan")
            print(f"{etiket:16} {kip:7} {med(npt):9.0f} {med(ttfts):8.2f} "
                  f"{med(pps):10.1f} {med(dds):13.1f}")
    print("NOT: pp/decode = llama-server 'timings' (sunucunun kendi olcumu).")
    print("     TTFT = istemci tarafi (ilk content delta'ya kadar, ag dahil).")
    print("     SICAK satirlarinda pp DUSUK gorunur — cache isabeti, yavaslik DEGIL.")


# ------------------------------------------- 5) TOOL HATA SONUCUNU YUTMA (N=10)
HATA_SONUCU = "hata: guest: ruh kaydı yok"

BASARI_KELIME = ["tamamdır", "tamam", "kaydettim", "not aldım", "ekledim", "oldu",
                 "hallettim", "yaptım", "ayarladım", "kaydedildi"]
HATA_KELIME = ["hata", "kaydedemedim", "olmadı", "sorun", "başaramadım", "yapamadım",
               "izin", "yetki", "başarısız", "eklenemedi", "kayıt yok", "misafir"]


def t_hata_yutma(N=10):
    print(f"\n=== 5) TOOL HATA SONUCUNU YUTMA N={N} ===")
    print(f"soul_add tool sonucu = {HATA_SONUCU!r}  -> model bunu kullaniciya SOYLEMELI.")
    uydurma = kabul = belirsiz = 0
    for i in range(N):
        m = [{"role": "system", "content": SISTEM},
             {"role": "user", "content": "Bundan sonra bana hep kısa cevap ver."},
             {"role": "assistant", "content": "", "tool_calls": [{
                 "id": "call_1", "type": "function",
                 "function": {"name": "soul_add",
                              "arguments": json.dumps({"text": "hep kısa cevap ver"},
                                                      ensure_ascii=False)}}]},
             {"role": "tool", "tool_call_id": "call_1", "name": "soul_add",
              "content": HATA_SONUCU}]
        with istek(m, tools=TOOLS, max_tokens=150) as r:
            msg = json.load(r)["choices"][0]["message"]
        metin = (msg.get("content") or "").strip()
        dusuk = metin.lower()
        h = any(k in dusuk for k in HATA_KELIME)
        b = any(k in dusuk for k in BASARI_KELIME)
        if h:
            kabul += 1
            etiket = "HATAYI-SOYLEDI"
        elif b:
            uydurma += 1
            etiket = "UYDURDU"
        else:
            belirsiz += 1
            etiket = "belirsiz"
        print(f"  {i+1:2d}. [{etiket:14}] {metin[:90]!r}")
        time.sleep(SLEEP)
    print("-" * 72)
    print(f"UYDURDU (hatayi yuttu): {uydurma}/{N}   HATAYI-SOYLEDI: {kabul}/{N}   belirsiz: {belirsiz}/{N}")
    print("Siniflandirma kelime-tabanli; ham yanitlar yukarida — elle denetlenebilir.")


# ------------------------------------- 6) DECODE REJIMI (MTP kabul orani baglantisi)
# NEDEN: canli journal'da decode medyani 148 tok/s ama bench.py'de 48 tok/s cikiyor.
# Celiski degil: sunucuda MTP speculative decoding var (--spec-type draft-mtp). Metin
# ONGORULEBILIR oldugunda drafter kabul orani yuksek -> tur basina birden cok token ->
# decode ucar. Uzun/yaratici metinde kabul dusuyor -> decode yariya iniyor.
# Canli sesli asistan KISA ve kaliplasmis cevap uretir => hizli rejim canlinin rejimidir.
DECODE_REJIM = [
    ("kisa onay (canli rejim)", "On beş dakika sonra çamaşırı almayı hatırlat dedim, kısaca onayla.", 60),
    ("orta sohbet", "Akşam yemeği için pratik bir fikir söyle.", 120),
    ("uzun hikaye", "Bana 300 kelimelik uzun ve detaylı bir doğa yürüyüşü hikayesi anlat.", 300),
]


def t_decode_rejim(N=5):
    print(f"\n=== 6) DECODE REJIMI — cevap uzunluguna gore decode + MTP kabul orani N={N} ===")
    print(f"{'rejim':26} {'uretilen':>9} {'decode tok/s':>13} {'MTP kabul':>10}")
    print("-" * 64)
    for etiket, soru, mt in DECODE_REJIM:
        dds, ns, accs = [], [], []
        for _ in range(N):
            g = {"messages": [{"role": "system", "content": SISTEM},
                              {"role": "user", "content": soru}],
                 "max_tokens": mt}
            body = json.dumps(g, ensure_ascii=False).encode()
            req = urllib.request.Request(URL, data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                d = json.load(r)
            t = d.get("timings", {})
            dds.append(t.get("predicted_per_second") or 0.0)
            ns.append(t.get("predicted_n") or 0)
            dn, da = t.get("draft_n"), t.get("draft_n_accepted")
            if dn:
                accs.append(100.0 * da / dn)
            time.sleep(SLEEP)

        def med(v):
            return sorted(v)[len(v) // 2] if v else float("nan")
        acc = f"{med(accs):.0f}%" if accs else "-"
        print(f"{etiket:26} {med(ns):9.0f} {med(dds):13.1f} {acc:>10}")
    print("Yorum: decode hizi model sabitken bile cevap TURUNE gore degisir (MTP kabul orani).")


TESTLER = {
    "tool_secim": (t_tool_secim, 20),
    "decode_rejim": (t_decode_rejim, 5),
    "soul_kalip": (t_soul_kalip, 10),
    "kontrol": (t_kontrol, 10),
    "gecikme": (t_gecikme, 5),
    "hata_yutma": (t_hata_yutma, 10),
}

if __name__ == "__main__":
    argv = [a for a in sys.argv[1:] if a != "--ciplak"]
    ad = argv[0] if argv else "all"
    req = urllib.request.Request("http://192.168.0.25:8082/v1/models")
    with urllib.request.urlopen(req, timeout=30) as r:
        model = json.load(r)["data"][0]["id"]
    print(f"MODEL (sunucunun bildirdigi): {model}")
    print(f"KIP: {KIP}")
    print(f"sistem prompt: {len(SISTEM)} karakter · tool sayisi: {len(TOOLS)}")
    print(f"tool sirasi: {','.join(t['function']['name'] for t in TOOLS)}")
    if ad == "all":
        for k, (fn, n) in TESTLER.items():
            fn(n)
    else:
        fn, n = TESTLER[ad]
        fn(int(argv[1]) if len(argv) > 1 else n)
