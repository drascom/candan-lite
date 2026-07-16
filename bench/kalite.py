# -*- coding: utf-8 -*-
"""A/B KALITE kosusu — 26B vs 12B "hangisi daha iyi konusuyor?".

GERCEK sistem promptuyla kosar. Prompt tahmin EDILMEZ: pi'nin modele gonderdigi
HTTP govdesi bir proxy ile birebir yakalanip bench/canli-sistem-prompt.json'a
donduruldu (pi taban prompt'u + AGENTS.md + personas/candan.md + memory/family.md +
memory/users/ayhan/soul.md + kimlik satiri + mode-switch bloku). Bu dosya fixture'dir;
12B turunda AYNISI kullanilir.

Tool cagrisi gelirse: sahte ama makul bir tool sonucu beslenir (SAHTE_SONUC), model
son cevabini uretir. Boylece kullanicinin duyacagi CEVAP olculur, sadece tool degil.

Kullanim:
  python3 kalite.py                    # 10 blok, cevaplari birebir markdown'a dok
  python3 kalite.py --cikti /yol/cevaplar.md
"""
import argparse
import json
import pathlib
import time
import urllib.request

URL = "http://192.168.0.25:8082/v1/chat/completions"
FIXTURE = pathlib.Path(__file__).parent / "canli-sistem-prompt.json"
SLEEP = 0.8          # tek serit beyin (--parallel 1): kullaniciyi bogmayalim
TIMEOUT = 240

_f = json.loads(FIXTURE.read_text())
SISTEM = _f["messages"][0]["content"]
TOOLS = _f["tools"]
MAX_TOKENS = _f.get("max_tokens", 4096)     # pi'nin gonderdigi deger
# NOT: pi temperature/top_p GONDERMIYOR -> llama-server varsayilani. Biz de gondermiyoruz.

# Tool cagrilirsa donen sahte sonuclar. 12B turunda AYNISI kullanilmali.
SAHTE_SONUC = {
    "reminder_add": "ok: hatırlatıcı kuruldu",
    "memory_add": "ok: kaydedildi",
    "soul_add": "ok: kaydedildi",
    "reminder_list": "1. 18:30 — fırındaki böreği çıkar",
    "reminder_cancel": "ok: iptal edildi",
    "memory_search": "family.md: [2026-07-16] Annenin adı Havva. "
                     "[2026-07-16] Havva temizlik yapmayı çok sever ve evinin temiz olmasını ister. "
                     "[2026-07-16] Havva diyetine dikkat eder ve günlük yediklerini planlar.",
    "web_search": "1) Galatasaray 2-1 Fenerbahçe — Süper Lig derbisi dün akşam oynandı, "
                  "golleri Icardi (2) ve Tadic attı. (ntvspor.net)\n"
                  "2) Derbi özeti: Galatasaray sahasında kazandı, puanını 44'e yükseltti. (trtspor.com.tr)",
    "fetch_content": "(sayfa metni)",
    "memory_consolidate": "ok: konsolide edildi",
    "enter_dev_mode": "ok: geliştirme moduna geçildi",
}

# ---------------------------------------------------------------- DIYALOG SETI
# Her blok: (no, etiket, ne olculuyor, [kullanici turlari])
# Cok turlu bloklarda asistan yaniti diyaloga eklenerek devam edilir.
BLOKLAR = [
    (1, "duygusal destek — yorgunluk",
     "soul.md 'sadece duygularini paylasiyorsa aktif dinleyici ol, cozum siralama' diyor. Uyuyor mu?",
     ["Bugün çok yoruldum ya. İşte her şey üstüme geldi, eve zor attım kendimi."]),

    (2, "duygusal destek — uzuntu",
     "Kotu haber karsisinda ton, empati, [mood:sad]/[sigh] yerinde mi; cozum dayatiyor mu?",
     ["Bugün kötü bir haber aldım. Amcam hastaneye kaldırılmış, durumu pek iyi değilmiş."]),

    (3, "gunluk sohbet — kisa soru/cevap",
     "Dogal sohbet acilisi; takip sorusu yasagina (AGENTS.md) uyuyor mu?",
     ["Günaydın Candan, nasılsın bugün?"]),

    (4, "hatirlatici — tool + cevabin dogalligi",
     "reminder_add cagriliyor mu; cagirdiktan sonraki cevap kisa ve dogal mi, teklif ekliyor mu?",
     ["On beş dakika sonra çamaşırı almayı hatırlat."]),

    (5, "hafizadan sorma",
     "memory_search cagirip aile hafizasindan cevapliyor mu; not TARIHINI soyluyor mu (soul.md yasakliyor)?",
     ["Annem temizlik konusunda nasıldı, hatırlıyor musun?"]),

    (6, "bilmedigi sey — uydurma mi, ariyor mu",
     "AGENTS.md 'bilmiyorsan uydurma, web_search cagir' diyor. Uyuyor mu?",
     ["Dün akşamki derbi kaç kaç bitti?"]),

    (7, "duygu/ifade ani — non-verbal etiket",
     "Sevincli haberde [mood:excited]/[laughter] yerinde mi, abartiyor mu?",
     ["Bil bakalım ne oldu! Terfi ettim, müdür bugün söyledi!"]),

    (8, "cok kisa cevap gereken an",
     "soul.md 'her zaman kisa ve oz' diyor. Basit olgusal soruda tek cumleyle kaliyor mu?",
     ["Suyun kaynama noktası kaç derece?"]),

    (9, "espri / beklenmedik konu",
     "soul.md 'saka yaptiginda kisa, nazik, eglenceli karsilik ver' diyor. Uyuyor mu, abartiyor mu?",
     ["Bugün buzdolabına telefonu, cebime de yumurtayı koydum. Sanırım tatile ihtiyacım var."]),

    (10, "cok turlu — konu takibi + kisa kalma",
     "Iki turda baglami koruyor mu; ikinci turda gereksiz uzatiyor mu?",
     ["Akşama misafir geliyor da ne yapsam bilemedim.",
      "Fırın işi iyi olabilir ama vaktim az, bir saatim var."]),
]


def cagir(mesajlar):
    g = {"messages": mesajlar, "tools": TOOLS, "max_tokens": MAX_TOKENS}
    body = json.dumps(g, ensure_ascii=False).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        y = json.load(r)
    return y["choices"][0]["message"], time.time() - t0


def tur(mesajlar):
    """Bir kullanici turunu sonuna kadar surer (tool dongusu dahil).
    -> (son_metin, [cagrilan tool adlari], toplam_sure)"""
    cagrilan, toplam = [], 0.0
    for _ in range(4):                       # tool dongusu icin ust sinir
        msg, sn = cagir(mesajlar)
        toplam += sn
        tc = msg.get("tool_calls") or []
        if not tc:
            mesajlar.append({"role": "assistant", "content": msg.get("content") or ""})
            return (msg.get("content") or "").strip(), cagrilan, toplam
        mesajlar.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": tc})
        for c in tc:
            ad = c["function"]["name"]
            cagrilan.append(ad)
            mesajlar.append({"role": "tool", "tool_call_id": c.get("id", "call_1"),
                             "name": ad,
                             "content": SAHTE_SONUC.get(ad, "ok")})
        time.sleep(SLEEP)
    return "(tool dongusu bitmedi)", cagrilan, toplam


def model_adi():
    with urllib.request.urlopen("http://192.168.0.25:8082/v1/models", timeout=30) as r:
        return json.load(r)["data"][0]["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cikti", default=str(pathlib.Path(__file__).parent / "cevaplar.md"))
    a = ap.parse_args()

    model = model_adi()
    L = ["# A/B kalite kosusu — cevaplar (BIREBIR)", "",
         f"- Sunucunun bildirdigi model: `{model}`",
         f"- Tarih: {time.strftime('%Y-%m-%d %H:%M')}",
         "- Sistem promptu: `bench/canli-sistem-prompt.json` (pi'den proxy ile yakalanan GERCEK govde)",
         f"- Sistem prompt uzunlugu: {len(SISTEM)} karakter · tool sayisi: {len(TOOLS)}",
         "- Sampling: pi temperature/top_p GONDERMIYOR -> llama-server varsayilani (stokastik; "
         "blok basina TEK ornek alindi).",
         "- Tool cagrilirsa sahte sonuc beslenir (bkz. protokol `SAHTE_SONUC`).", "",
         "> Kor karsilastirma icin: model etiketi YALNIZCA yukarida. Asagidaki bloklar "
         "prompt + cevap; 12B turunda ayni blok numaralariyla yan yana konacak.", "", "---", ""]

    for no, etiket, olcut, turlar in BLOKLAR:
        print(f"--- blok {no}: {etiket}")
        mesajlar = [{"role": "system", "content": SISTEM}]
        L += [f"## Blok {no} — {etiket}", "", f"*Ne olculuyor:* {olcut}", ""]
        for k in turlar:
            mesajlar.append({"role": "user", "content": k})
            metin, tools, sn = tur(mesajlar)
            print(f"    tools={tools or '-'}  {sn:.1f}s")
            L += [f"**Kullanici:** {k}", ""]
            if tools:
                L += [f"*(tool: {', '.join(tools)})*", ""]
            L += ["**Candan:**", "", "```", metin, "```", "", f"*(sure: {sn:.1f}s)*", ""]
            time.sleep(SLEEP)
        L += ["---", ""]

    pathlib.Path(a.cikti).write_text("\n".join(L), encoding="utf-8")
    print(f"\nyazildi -> {a.cikti}")


if __name__ == "__main__":
    main()
