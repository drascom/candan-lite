"""VURGU — cümleler, hedef kelimeler ve denenen yollar (tek kaynak).

`vurgu_probe.py` (üretim), `vurgu_eval.py` (ölçüm) ve kulak seti sayfası aynı
tanımı buradan okur.

NEDEN: kullanıcı duygu atlasını kulakla dinledi ve beş satırda duygu değil
**VURGU** eksikliğinden şikâyet etti ("olur mu kısmında vurgu eksik",
"on dakikaya derken vurgu lazım", "tek başına kısmına da vurgu lazım",
"anlamadım kelimesinde vurgu yok"). Higgs kataloğunda (43 etiket) kelime
düzeyinde vurgu YOK — `katalog_yoklama.py` bunu ölçtü: uydurma
`<|prosody:emphasis|>` / SSML `<emphasis>` HARFİ HARFİNE OKUNUYOR. Bu yüzden
dolaylı yollar denenir.

TABAN = kullanıcının DİNLEDİĞİ hâl, yani duygu etiketiyle birlikte. Böylece
"eksik" dediği vurgu geldi mi doğrudan kıyaslanır; fark yalnız vurgu yolundan
gelir.

Cümleler `../duygu-atlasi/atlas_set.py`'den birebir alındı.

⚠️ `emotion:contemplation` satırı ("sadece **da** vurgusu eksik") ölçüme
ALINMADI: hedef iki harflik bir bağlaç, Whisper kelime sınırı o uzunlukta
güvenilir değil (10 ms çözünürlükte 60-80 ms'lik kelime). Kulak setinde
kazanan yolla yine sunulabilir, ama sayıyla eleme ona dayandırılamaz.
"""
from __future__ import annotations

# (id, duygu öneki, ön, [bagli], HEDEF, arka, kullanıcının notu)
#
# `bagli` — 2. TURDAN SONRA EKLENDİ, `awe` için. Kullanıcı o cümlede "vurgu HEM DE
# kelimesinde" dedi: işaret hedefin önüne konuyor, ama önündeki kelime "hem de"ydi
# ve Türkçede **sınırdan önceki** kelime öbek-sonu belirginliği alır. "hem de"
# ayrıca hedefe bağlı bir pekiştirici; ondan koparılırsa vurguyu kendine çeker.
# `bagli`, işaretin ÖNÜNE değil ARKASINA düşmesi gereken kısımdır: sınır
# "hem de"nin önüne alınır, "hem de tek başına" tek öbek kalır.
#
# ESKİ YOLLAR DEĞİŞMEZ: `taban`/`pause`/`tire`/`kombo`… `on + bagli`yi tek parça
# görür, yani 2. turun metinleri ve wav'ları HARFİ HARFİNE aynı kalır (kıyas
# zemini bozulmasın). `bagli` yalnız yeni `*-on` yollarında ayrışır.
CUMLELER: list[dict] = [
    {
        "id": "affection",
        "onek": "<|emotion:affection|>",
        "on": "Bugün kendine iyi bakmayı unutma ",
        "hedef": "olur mu",
        "arka": ", ben hep buradayım.",
        "sikayet": "olur mu kısmında vurgu eksik",
    },
    {
        "id": "arousal",
        "onek": "<|emotion:arousal|>",
        "on": "Hadi kalk bakalım, ",
        "hedef": "on dakikaya",
        "arka": " çıkmamız gerekiyor!",
        "sikayet": "on dakikaya derken vurgu lazım",
    },
    {
        "id": "awe",
        "onek": "<|emotion:awe|>",
        "on": "Sınavdan tam not almışsın, ",
        "bagli": "hem de ",   # hedeften koparılamaz — bkz. yukarıdaki not
        "hedef": "tek başına",
        "arka": " çalışarak.",
        "sikayet": "tam not vurgusu güzel ama tek başına kısmına da vurgu lazım",
    },
    {
        "id": "confusion",
        "onek": "<|emotion:confusion|>",
        "on": "Tam ",
        "hedef": "anlamadım",
        "arka": " şimdi, biraz daha açar mısın?",
        "sikayet": "anlamadım kelimesinde vurgu yok",
    },
]

# Yollar. `kombo` ölçümün İLK turundan SONRA doldurulur (en umut veren ikisi).
YOLLAR: list[dict] = [
    {"ad": "taban", "aciklama": "kullanıcının dinlediği hâl — yalnız duygu etiketi"},
    {"ad": "expressive_high",
     "aciklama": "<|prosody:expressive_high|> cümle başında (tüm cümleye etki eder)"},
    {"ad": "pause",
     "aciklama": "<|prosody:pause|> hedef kelimeden hemen önce, bitişik"},
    {"ad": "buyuk", "aciklama": "hedef kelime BÜYÜK HARF"},
    {"ad": "virgul", "aciklama": "hedef kelimenin iki yanına virgül"},
    {"ad": "tire", "aciklama": "hedef kelime uzun tire ile ayrılmış"},
    {"ad": "kombo", "aciklama": "ölçümde en iyi iki yol birlikte"},
    # ── 3. TUR: kelime SONRASI beklemeyi kesen yollar ───────────────────────
    # Kullanıcı komboyu 4 cümlenin 3'ünde "vurgu geldi" dedi ama altı ayrı
    # notta "kelime sonrası uzun bekleme" diye şikâyet etti. Kod bakıldı:
    # `_tireli` tireyi hedefin İKİ YANINA koyuyor; ARKADAKİ tire vurguya
    # katkı vermeden yalnız bekleme üretiyor. Bu iki yol arkadaki işareti atar.
    {"ad": "kombo-on",
     "aciklama": "kombonun yalnız ÖNÜ: tire + pause hedeften önce, arkada işaret YOK"},
    {"ad": "tire-on",
     "aciklama": "yalnız öndeki tire — pause yok, arkada işaret yok"},
    # YALNIZ `awe` için üretildi (bağlı kısmı olan tek cümle). `bagli`si olmayan
    # cümlelerde `kombo-on` ile HARFİ HARFİNE aynı olur, o yüzden koşulmaz.
    # İşi: A kusurunu (arkadaki bekleme) B kusurundan (sınırın yanlış yerde
    # olması) ayırmak. Burada sınır 2. turdaki yerinde — "hem de"nin ARKASINDA.
    {"ad": "kombo-on-dar",
     "aciklama": "sınır 2. turdaki yerinde (hem de'den SONRA), arkada işaret yok"},
]

# `kombo` hangi iki yolun birleşimi — İLK TUR ÖLÇÜLDÜKTEN SONRA seçildi.
# Ölçümde iki AYRI sinyal çıktı, ikisi de tek başına eksik:
#   • `pause`  → tek tutarlı etki: hedefin perde tepesi +1.2 yarım ton (4/4 cümlede
#     artı) ve önüne ~150 ms sessizlik. Ama kelimeyi UZATMIYOR.
#   • `tire`   → en güçlü uzatma (süre oranı +0.31) ama perdeyi ve enerjiyi
#     DÜŞÜRÜYOR (sıralama/ara cümle tonu).
# Kombo bu ikisinin toplanıp toplanmadığını sorar: hem uzun hem tiz bir hedef.
KOMBO = ("pause", "tire")


def buyuk_tr(s: str) -> str:
    """Türkçe büyük harf: Python'un .upper()'ı 'i'yi 'I' yapar, doğrusu 'İ'."""
    return s.replace("i", "İ").replace("ı", "I").upper()


def _on_tam(c: dict) -> str:
    """Hedeften önceki metnin TAMAMI (bağlı kısım dahil).

    2. tur yolları `bagli`yi bilmez; onlar için ön parça hep bu. Böylece
    `bagli` eklenmesi eski metinleri HİÇ değiştirmez.
    """
    return c["on"] + c.get("bagli", "")


def _virgullu(c: dict, hedef: str) -> str:
    on = _on_tam(c).rstrip()
    if not on.endswith(","):
        on += ","
    arka = c["arka"].lstrip()
    if not arka.startswith(","):
        arka = "," + ("" if arka.startswith(" ") else " ") + arka
    return f"{on} {hedef}{arka}"


def _tireli(c: dict, hedef: str) -> str:
    on = _on_tam(c).rstrip().rstrip(",")
    arka = c["arka"].lstrip().lstrip(",").lstrip()
    return f"{on} — {hedef} — {arka}"


def _on_isaretli(c: dict, once: str, bagli_ayir: bool = True) -> str:
    """İşaret YALNIZ hedefin önünde; arkasına hiçbir şey eklenmez.

    `once` sınırın kendisi (" — ", " —<|prosody:pause|>" gibi). Hedeften sonra
    metin OLDUĞU GİBİ devam eder — arkadaki tire/virgül eklenmez, `arka`nın
    kendi noktalaması korunur. Şikâyet edilen "kelime sonrası uzun bekleme"nin
    kaynağı tam olarak arkaya eklenen işaretti.

    `bagli_ayir=False` → bağlı kısım sınırın ÖNÜNDE kalır, yani 2. turdaki
    yerleşim. Yalnız B kusurunu (sınırın yeri) ayrı ölçmek için.
    """
    bagli = c.get("bagli", "")
    on = (c["on"] if bagli_ayir else c["on"] + bagli).rstrip().rstrip(",")
    arda = bagli if bagli_ayir else ""
    return f"{on}{once}{arda}{c['hedef']}{c['arka']}"


def duz(c: dict) -> str:
    """Etiketsiz, sade cümle — WER'in beklediği söylenmiş metin."""
    return _on_tam(c) + c["hedef"] + c["arka"]


def metin(c: dict, yol: str) -> str:
    """Bir cümle + bir yol → sunucuya gönderilecek metin."""
    onek, hedef, arka = c["onek"], c["hedef"], c["arka"]
    on = _on_tam(c)
    if yol == "kombo-on":
        # Kombonun ÖN yarısı: tire + bitişik pause. Arkada hiçbir işaret yok.
        return onek + _on_isaretli(c, " —<|prosody:pause|>")
    if yol == "tire-on":
        return onek + _on_isaretli(c, " — ")
    if yol == "kombo-on-dar":
        return onek + _on_isaretli(c, " —<|prosody:pause|>", bagli_ayir=False)
    if yol == "taban":
        return onek + on + hedef + arka
    if yol == "expressive_high":
        return onek + "<|prosody:expressive_high|>" + on + hedef + arka
    if yol == "pause":
        # Ölçülmüş yerleşim kuralı: iki yanı BOŞLUKSUZ (bkz. duygu-katmanı §4).
        return onek + on.rstrip() + "<|prosody:pause|>" + hedef + arka
    if yol == "buyuk":
        return onek + on + buyuk_tr(hedef) + arka
    if yol == "virgul":
        return onek + _virgullu(c, hedef)
    if yol == "tire":
        return onek + _tireli(c, hedef)
    if yol == "kombo":
        m = c
        a, b = KOMBO
        # İkinci yolu birinciye uygula: metni değil, hedefi/yerleşimi birleştir.
        hed = buyuk_tr(hedef) if "buyuk" in KOMBO else hedef
        if set(KOMBO) == {"pause", "buyuk"}:
            return onek + on.rstrip() + "<|prosody:pause|>" + hed + arka
        if set(KOMBO) == {"pause", "virgul"}:
            g = _virgullu(m, hedef)
            return onek + g.replace(f" {hedef}", f"<|prosody:pause|>{hedef}", 1)
        if set(KOMBO) == {"buyuk", "virgul"}:
            return onek + _virgullu(m, buyuk_tr(hedef))
        if set(KOMBO) == {"expressive_high", "pause"}:
            return (onek + "<|prosody:expressive_high|>" + on.rstrip()
                    + "<|prosody:pause|>" + hedef + arka)
        if set(KOMBO) == {"expressive_high", "buyuk"}:
            return onek + "<|prosody:expressive_high|>" + on + buyuk_tr(hedef) + arka
        if set(KOMBO) == {"buyuk", "tire"}:
            return onek + _tireli(m, buyuk_tr(hedef))
        if set(KOMBO) == {"pause", "tire"}:
            g = _tireli(m, hedef)
            return onek + g.replace(f" {hedef}", f"<|prosody:pause|>{hedef}", 1)
        raise SystemExit(f"kombo için birleştirme kuralı yok: {a}+{b}")
    raise SystemExit(f"bilinmeyen yol: {yol}")


def satirlar(yollar: list[str] | None = None) -> list[dict]:
    """(cümle × yol) düz liste — probe ve sayfa aynı sırayı görsün."""
    out = []
    for c in CUMLELER:
        for y in YOLLAR:
            if yollar and y["ad"] not in yollar:
                continue
            out.append({
                "ad": f"{c['id']}/{y['ad']}", "cumle_id": c["id"], "yol": y["ad"],
                "metin": metin(c, y["ad"]), "beklenen": duz(c),
                "hedef": c["hedef"], "sikayet": c["sikayet"],
                "yol_aciklama": y["aciklama"],
            })
    return out


if __name__ == "__main__":  # gönderilen metinleri gözle doğrulamak için
    for r in satirlar():
        print(f"{r['ad']:26s} {r['metin']}")
