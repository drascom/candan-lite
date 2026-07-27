"""higgs_tts — livekit-agents TTS plugin over Higgs TTS 3 (4B).

SİSTEMDEKİ TEK TTS MOTORU. 27 Tem'de OmniVoice'un (`omnivoice_tts.py`) yerine geçti,
28 Tem'de OmniVoice sunucudan ve koddan TAMAMEN kaldırıldı → motor seçimi (`TTS_ENGINE`)
ve geri dönüş kolu YOK. Kalıcı arıza hâlinde yol Piper'dır (ayrı iş, henüz yazılmadı).
Ayrıntı: handoff/2026-07-28-omnivoice-kaldir.md

İKİ YOL:
  • `POST /api/tts/stream` → chunked ham PCM. **VARSAYILAN.** Sunucu cümleyi
    üretirken 320 ms'lik bloklar hâlinde akıtır; ilk ses ~0.5 sn'de başlar.
  • `POST /api/tts` → tam WAV. GERİ DÖNÜŞ yolu (`HIGGS_STREAM=0`) ve bench.

⚡ NEDEN STREAMING (27 Tem, canlı şikâyet: "konuşmalara geç başlıyor"):
Ölçülen gecikme (kullanıcı sustu → Candan'ın sesi) kısa cevapta ~3 sn, uzun
cevapta 8-10 sn'ydi ve UZUNLUKLA ARTIYORDU. Beyin darboğaz DEĞİLDİ (llama-server
KV cache yeniden kullanımıyla ardışık çağrılar 0.67-0.79 sn). Darboğaz TTS'ti:
tam WAV ucu cümlenin TAMAMINI üretip öyle dönüyor, o süre kullanıcı için
sessizlik. Ölçüm (HTTP, 5 tekrar medyanı, ilk sese kadar):

    cümle    tam WAV      streaming    kazanç
    kısa      761 ms       467 ms      -0.29 sn
    orta     2343 ms       510 ms      -1.83 sn
    uzun     6207 ms       546 ms      -5.66 sn

Toplam RTF ikisinde de ~0.50 — yani ses aynı hızda üretiliyor, sadece BEKLETMİYORUZ.
Streaming'de ilk ses cümle uzunluğundan neredeyse BAĞIMSIZ (467-546 ms).

livekit zaten `streaming=False` ile CÜMLE BAŞINA `synthesize()` çağırıyor;
`streaming` bayrağı GİRDİ (metin) akışıyla ilgili, ÇIKTI akışıyla değil.
`AudioEmitter`'a parça parça `push()` etmek bu bayrakla ilgisiz — eski OmniVoice
yolu (`_run_ws`) da aynısını yapıyordu, pipeline bunu destekliyor.

OMNIVOICE'TAN AYNEN TAŞINAN KAZANIMLAR (hepsi ölçülmüş):
  • `normalize_tr()` (trnorm) — Higgs'te de WER 0.058 → 0.028 (29 cümle, ASR
    geri-dönüş). Higgs ham metinde zaten iyi; kalan gerçek zayıflık `1.250.000`
    tipi binlik+milyon ve trnorm onu düzeltiyor.
  • `tts_cache` — kalıp/kısa cümle cache'i.
  • Kısa metin guard'ı: cümle sonu noktası → tek retry → sessizlik.
  • TTS hatası TURU ÖLDÜRMEZ: her yolda en az bir parça (gerekirse sessizlik)
    push edilir. Hiç frame push edilmezse livekit "no audio frames were pushed"
    APIError'ı atıp turu öldürüyor.

⚠️ CACHE ANAHTARI — MOTOR KİMLİĞİ ŞART (ölçülmüş tuzak, 27 Tem). `tts_cache` anahtarı
referans parmak izini içeriyor; Higgs ve OmniVoice AYNI referans wav'ını ve aynı
`ref_text`'i kullandığı için sadece referansa bakan bir anahtar iki motorda da AYNI
çıkıyordu → Higgs'e geçince eski OmniVoice cache'i YANLIŞ SESLE çalardı. Bu yüzden
anahtara giren `ref` alanı `higgs-tts-3-4b:<parmak izi>` biçiminde, motor kimliğiyle
ÖNEKLİ (bkz. `ref_fingerprint`). OmniVoice gitti ama önek KALSIN: bir sonraki motor
(Piper) geldiğinde aynı tuzak aynen tekrar eder.

REFERANS SES (Candan'ın kimliği): `assets/voice/default-ref.wav` → sunucuda
`/opt/candan-lite/assets/voice/default-ref.wav`, `higgs.env`'deki `HIGGS_REF_AUDIO`
oraya bakar. Çalışma anında `HIGGS_REF_CODES` (önceden hesaplanmış kodlar) varsa wav
HİÇ okunmaz; kod dosyası silinirse wav'dan yeniden üretilir.

⚠️ ETİKET SÖZDİZİMİ — GEÇİŞİ BLOKE EDEN FARK. OmniVoice `[laughter]`, `[sigh]`,
`[surprise-oh]`, `[question-en]`, `[confirmation-en]` etiketlerini SESLENDİRİYORDU;
`pi/AGENTS.md` ve `pi/personas/candan.md` modele bunları hâlâ ürettiriyor. Higgs'in resmi
`PROMPTING.md`'si ise net: **tanınmayan etiket ya çıktıyı bozar ya da HARFİ HARFİNE
OKUNUR.** Yani müdahalesiz Higgs "laughter", "sigh", "mood excited" diye konuşurdu.
Üstelik `trnorm` köşeli parantez içini BİLEREK koruyor (OmniVoice için doğruydu),
yani etiketler bozulmadan buraya ulaşıyor.

Çözüm `_to_higgs_markup()`: trnorm'dan SONRA çalışır, `HIGGS_TAG_MAP`/`MOOD_PRESETS`
ile Higgs'in kendi sözdizimine (`<|kategori:etiket|>`) çevirir ve **eşlemesi olmayan
her `[...]` kalıbını SİLER**. Garanti: sunucuya giden metinde köşeli parantez KALMAZ.
Higgs'in yerleşim kuralı da korunur — emotion/style/prosody CÜMLE BAŞINA taşınır,
`sfx` yerinde kalır (etiketin hemen ardından ses taklidi, arada boşluk YOK).

SIRA (kritik): mood çıkarma → hız çıkarma → trnorm → etiket dönüşümü → gönderim.

🏃 KONUŞMA HIZI — `[speed:slow|normal|fast|very_fast]`, OTURUM ÖMÜRLÜ.
Canlı şikâyet (27 Tem 18:21 ve 18:33): kullanıcı üç kez "biraz daha hızlı konuş"
dedi, Candan üç kez "hızlandırıyorum" dedi, tempo DEĞİŞMEDİ — hız kolu yoktu.
Ölçüm (`experiments/konusma-hizi/`, koşul başına 12 örnek, canlı streaming ucu,
Whisper geri-dönüşü) üç adayı karşılaştırdı:

    yol                              kelime/s kazancı   WER
    <|prosody:speed_fast|> token'ı        +%5.9        0.030  ← REDDEDİLDİ
    <|prosody:speed_very_fast|>           +%7.2        0.023  ← REDDEDİLDİ
    motorun `speed` gövde parametresi     YOK (sunucu kodu onu hiç okumuyor)
    WSOLA tempo (worker/tempo.py) 1.15   +%14.8        0.004  ← SEÇİLDİ
    WSOLA tempo 1.30                     +%29.7        0.004  ← SEÇİLDİ

Taban WER'i de 0.004: WSOLA anlaşılırlığı HİÇ bozmuyor, token yolu 4-7 katına
çıkarıyor. Filtre streaming'in ÇIKIŞINDA duruyor (blok/lookahead/sol bağlam
mantığına DOKUNULMADI) ve ilk ses gecikmesi 517 ms → 517 ms.
Kademe `reset_mood()` ile SIFIRLANMAZ: mood cümlelik bir renk, hız kalıcı ayar.
Bayrak: `worker/.env` → `SPEECH_SPEED=0` (işaret yine silinir, tempo uygulanmaz).

⚠️ PROMPT MOTORA BAĞLI — BİR SONRAKİ MOTORDA BUNU HATIRLA. 27 Tem'de prompt'a Higgs'e
ÖZGÜ etiketler eklendi (`[pause]`, `[long_pause]`, `[whisper]`, `[mood:*]` ondu,
`[speed:X]`) — ölçüldüler, Higgs'te temizler. Bu etiketleri TANIMAYAN bir motor
`[...]`'yi HARFİ HARFİNE OKUR (OmniVoice öyle yapıyordu). Yani motor değişirse
`pi/AGENTS.md` + `pi/personas/candan.md` de o motora göre güncellenmeli; yoksa Candan
"pause", "mood warm" diye konuşur.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
import wave
from typing import Optional

import aiohttp

from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

import speech_speed
import tempo
import tts_cache
from log_utils import DedupeFilter
from trnorm import normalize_tr

logger = logging.getLogger("higgs_tts")
logger.addFilter(DedupeFilter())

# Cache anahtarına giren motor kimliği. DEĞİŞTİRME = tüm Higgs cache'ini geçersiz kılar.
ENGINE_ID = "higgs-tts-3-4b"

DEFAULT_SAMPLE_RATE = 24000
NUM_CHANNELS = 1

# Boş çıktı / hata durumunda emitter'a verilen sessizlik (omnivoice_tts ile aynı gerekçe:
# sıfır frame → livekit APIError → TUR ÖLÜR). Sessiz kalmak kabul, çökmek DEĞİL.
_SILENCE_MS = 120
_SHORT_TEXT_RETRIES = 1

# Sentez zaman aşımı. Ölçülen en yavaş cümle 4.1 s; 400 karakterlik emniyet supabı
# birkaç parçaya bölünebilir. 90 s cömert ama sonsuz değil — asılı kalan istek turu
# kilitlemesin.
_SYNTH_TIMEOUT_S = 90.0
# Streaming'de İKİ BLOK ARASI en uzun bekleme. Ölçülen en kötü ara 163 ms (blok=8);
# 20 s astronomik bir pay ama "sunucu bloke oldu" hâlini toplam 90 s beklemeden
# yakalar — tur erken kurtulsun.
_STREAM_STALL_S = 20.0
# `GET /api/default` (cache parmak izi) — kısa ve bağlayıcı olmayan.
_REF_TIMEOUT_S = 2.0
_REF_TTL_S = 300.0

_FINAL_PUNCT = ".!?…:;"

# ── OmniVoice etiketi → Higgs etiketi ────────────────────────────────────────
# TEK DÜZENLEME YERİ. Katalog burada genişler, `_to_higgs_markup()` değişmez.
#
# Higgs sözdizimi `<|kategori:etiket|>`, iki yerleşim sınıfı var:
#   • CÜMLE BAŞI  : emotion (21), style (3), prosody'nin speed_*/pitch_*/expressive_*
#   • SATIR İÇİ   : sfx (tam yerinde) ve prosody'nin pause / long_pause
# `sfx` tuzağı: etiketten hemen SONRA ses taklidi gelmeli, ARADA BOŞLUK YOK
# (`<|sfx:laughter|>Haha, ...`). Ölçüm `pause` için de AYNI kuralı gösterdi
# (aşağıya bak) — o yüzden `_HUG_INLINE_RE` iki yanındaki boşluğu yutuyor.
#
# Boş dize ("") = "karşılığı yok, SİL". Sözlükte HİÇ olmayan anahtar da silinir —
# fark yalnız niyet beyanında: burada olan "bilerek atıldı", olmayan "tanınmadı".
#
# ── 27 Tem ÖLÇÜMÜ (`experiments/higgs-tts3/token_probe.py` + `token_eval.py`) ──
# 43 etiketin TAMAMI canlı streaming ucundan, token başına 12 (sınırdakiler 24)
# örnek, Whisper geri-dönüşü. SONUÇ: 21 emotion + 3 style + 8 prosody-cümle-başı +
# 2 sfx + 2 prosody-satır-içi — hepsi 12/12 anlaşıldı, boş çıktı YOK.
# Buradaki eşleme o havuzdan SEÇİLMİŞTİR; ölçülmemiş token hâlâ girmez.
#
# ⚠️ `elation` DÜZELTMESİ: bu dosyada "5-7/12, bozuk" yazıyordu. Aynı cümleyle
# 24 örnekte 24/24 TEMİZ çıktı — eski ölçüm canlı yoldan (referans klonu +
# streaming) DEĞİL, deney koşumundan alınmıştı. `excited` yine de `enthusiasm`'da
# kalıyor: ikisi de temiz, `enthusiasm` canlıda kullanıcı tarafından onaylandı,
# ölçülmüş bir kazanç olmadan canlı davranış değiştirilmiyor.
#
# ⚠️ `speed_very_slow` ŞÜPHELİ: 24'te 23 anlaşıldı, WER 0.075 (diğerlerinde 0.000)
# ve cümlenin ilk hecesini kırpabiliyor ("Bugün" → "Gün"). Eşlemeye ALINMADI.
#
# ── SESİN KİMLİĞİNİ BOZAN TOKEN'LAR — ÖLÇÜMDE TEMİZ, KULAKTA YASAK ───────────
# Kalıcı ders (27 Tem, kullanıcı kulaklıkla dinledi): "temiz" yalnızca ANLAŞILIR
# demek; Candan'ın SESİ olarak kabul edilebilir demek DEĞİL.
#   • `prosody:pitch_low`  → "taban kadın sesi, etiketli erkek ses". Konuşanın
#     KİMLİĞİNİ değiştiriyor. ASLA kullanılmasın (`affection+pitch_low`nun "şuh"
#     bulunmasının sebebi de buydu).
#   • `style:shouting`     → "ses başkasına ait gibi, ses rengi değişmiş". Aynı sebep.
#   • `emotion:longing`    → KÖTÜ: "her kelimeyi uzatması fazla".
#   • `prosody:speed_*`    → ÖLÜ YOL. Kulakla da doğrulandı ("taban ile aynı",
#     `speed_fast` için "taban olan daha iyi"). Hız artık WSOLA ile yapılıyor
#     (`worker/tempo.py`, +%14.8/+%29.7 ve WER tabanla aynı).
#   • `emotion:arousal` / `fear` / `style:singing` → idare/uygunsuz.
#
# ── KOMBO (`<|emotion:X|><|prosody:Y|>`) ─────────────────────────────────────
# 27 Tem kombo ölçümü: 14 koşul / 168 wav, HEPSİ TEMİZ (12/12, WER 0.000, cümle
# başı yeme YOK). Token sırası ölçüldü, fark standart sapmanın altında; sıra
# tutarlılık için `emotion` önce (resmi PROMPTING.md örnekleriyle aynı).
# Tablo: `handoff/2026-07-27-kombo-olcumu.md`. Kombolar kulakla SEÇİLDİ.
MOOD_PRESETS: dict[str, str] = {
    "excited": "<|emotion:enthusiasm|>",   # 12/12 · canlıda onaylı
    "sad": "<|emotion:sadness|>",          # 12/12 · canlıda onaylı
    # 27 Tem'de ÖLÇÜLÜP eklenenler (hepsi 12/12, boş yok, baş yeme yok). Seçim
    # ölçütü: Türkçe sohbette SIK geçen ve birbirinden AYIRT EDİLEBİLİR duygular.
    # Katalogda temiz çıkan ama eklenMEyenler (anger/disgust/fear/shame/
    # bitterness/helplessness) bir ev asistanının ağzına uymuyor — temiz olması
    # gerekli, yeterli değil.
    "warm": "<|emotion:affection|>",       # 12/12 · şefkat/destek — Candan'ın tonu
    "confused": "<|emotion:confusion|>",   # 12/12 · "tam anlamadım"
    # Kulakla onaylanıp 27 Tem akşamı eklenenler.
    "amused": "<|emotion:amusement|>",         # 12/12 · şakalaşma, hafif alay
    "thinking": "<|emotion:contemplation|>",   # 12/12 · "bir düşüneyim"
    "determined": "<|emotion:determination|>", # 12/12 · söz verme, kararlılık
    "relieved": "<|emotion:relief|>",          # 12/12 · "çözüldü, geçmiş olsun"
    # KOMBO — kullanıcı tekiliyle yan yana dinledi, ikisinde de komboyu seçti.
    "proud": "<|emotion:pride|><|prosody:expressive_high|>",       # "kesinlikle kombo daha güzel"
    "calm": "<|emotion:contentment|><|prosody:expressive_low|>",   # tekili "yoga hocası" gibi düz kalıyordu
}

# `[mood:X]` KONTROL işareti — seslendirilmez, metinden SİLİNİR. Desen tek kaynaktan
# (MOOD_PRESETS) üretilir ki yeni bir duygu eklenince regex'i güncellemek unutulmasın.
KNOWN_MOODS = tuple(MOOD_PRESETS)
_MOOD_RE = re.compile(
    r"\s*\[mood:(" + "|".join(KNOWN_MOODS) + r")\]\s*", re.IGNORECASE
)

HIGGS_TAG_MAP: dict[str, str] = {
    # sesli taklit gerektirenler — SATIR İÇİ, taklit etikete bitişik
    "laughter": "<|sfx:laughter|>Haha, ",
    "sigh": "<|sfx:sigh|>Haah, ",
    # sessiz ortam biçemi — CÜMLE BAŞINA taşınır. `[mood:]` altına SOKULMADI:
    # mood'ların hepsi `<|emotion:…|>`, bu ise `style` — duygu değil biçem, ve
    # `[laughter]`/`[pause]` gibi çıplak etiket deseni zaten yerleşik.
    # ⚠️ KAPSAMI CÜMLE: livekit cümle cümle sentezliyor, yani token yalnız
    # konduğu cümleye etki eder (mood gibi TUR boyu yaşamaz). Prompt bu yüzden
    # "fısıltıyla söylenecek her cümlenin başına" diyor.
    "whisper": "<|style:whispering|>",     # 12/12 · kullanıcı: "harika"
    # şaşkınlık aileleri → KOMBO (CÜMLE BAŞINA taşınır)
    # ⚠️ `awe` DENENDİ, KULAKLA ELENDİ — geri getirmeyin. Ölçümü temizdi (12/12,
    # WER 0.000, Δsüre +0.35 s) ve 15:0x'te bir süre canlıdaydı; kullanıcı
    # kulaklıkla dinleyince tekil `awe`'yi KÖTÜ buldu ("şuh kalmış, heyecan yok").
    # Tekil `surprise` de yetmiyordu (en düşük Δsüre, -0.02 s, tabandan farksız).
    # Kombo ölçüldü (ünlemli+ünlemsiz cümle, iki sıra, hepsi 12/12 TEMİZ) ve
    # kullanıcı kulakla SEÇTİ: "kombo her ikisinin tam bir karışımı olmuş".
    # NOT: `awe+expressive_high` de temiz çıktı — yedek aday, ama seçilen bu değil.
    "surprise-ah": "<|emotion:surprise|><|prosody:expressive_high|>",
    "surprise-oh": "<|emotion:surprise|><|prosody:expressive_high|>",
    "surprise-wa": "<|emotion:surprise|><|prosody:expressive_high|>",
    "surprise-yo": "<|emotion:surprise|><|prosody:expressive_high|>",
    # SATIR İÇİ duraklama — duygu gerektirmez, konuşmanın RİTMİNİ düzeltir.
    # Ölçüm (24 örnek, cümle ortasında, bitişik): ikisi de 24/24 anlaşıldı,
    # `pause` +0.32 s, `long_pause` +0.51 s sessizlik ekliyor. Gerçekten duruyor.
    "pause": "<|prosody:pause|>",
    "long_pause": "<|prosody:long_pause|>",
    # karşılığı NET DEĞİL → sil (okunmaması yeter; uydurma eşleme YAPMIYORUZ).
    # NOT: `[question-*]` için `<|prosody:pitch_high|>` ölçüldü ve TEMİZ çıktı ama
    # "soru tonu" demek DEĞİL — uydurma eşleme olurdu, kulakla doğrulanmadan girmez.
    "question-en": "",
    "question-ah": "",
    "question-oh": "",
    "question-ei": "",
    "question-yi": "",
    "confirmation-en": "",
    "dissatisfaction-hnn": "",
}

# ── Etiketi KULLANMAK ile ANLATMAK'ı ayırt et ────────────────────────────────
# CANLI HATA (27 Tem 13:09:46): model etiketi kullanmıyor, kullanıcıya ANLATIYORDU —
#     model yazdı : "...şaşırdığımda [surprise-oh] gibi efektlerle tepki verebilirim..."
#     kullanıcı duydu: "...şaşırdığımda ___ gibi efektlerle..."
# Temizleyici etiketi sildiği için cümlede DELİK kaldı. Silmek burada yanlış: etiket
# cümlenin ÖZNESİ, atılınca cümle bozuluyor. Doğrusu okunabilir karşılığına çevirmek.
#
# Ayırt etme SAĞ BAĞLAMLA yapılıyor (dar ve kanıtlanabilir): etiketten hemen sonra
# "gibi / diye / etiketi / efektini / yazarak..." geliyorsa etiket ANLATILIYOR. Bu kalıp
# gerçek kullanımda görünmez — `[laughter] Bunu gerçekten yaptın mı?` sağında normal
# cümle var, eşleşmez. Tırnak içindeki etiket de anlatımdır.
_MENTION_AFTER_RE = re.compile(
    r"^\s*(?:gibi|diye|şeklinde|biçiminde|"
    r"etiket\w*|işaret\w*|efekt\w*|komut\w*|"
    r"yaz\w*|koy\w*|kullan\w*|ekle\w*)\b",
    re.IGNORECASE,
)
_MENTION_QUOTES = "\"'«»“”‘’"

# Anlatılan etiketin SESLİ karşılığı. "gibi/etiketi" ile devam eden cümleye
# oturacak biçimde seçildi: "…şaşırdığımda ŞAŞIRMA gibi efektlerle…".
_READABLE: dict[str, str] = {
    "laughter": "kahkaha",
    "sigh": "iç çekme",
    "surprise-ah": "şaşırma",
    "surprise-oh": "şaşırma",
    "surprise-wa": "şaşırma",
    "surprise-yo": "şaşırma",
    "question-en": "soru tonu",
    "question-ah": "soru tonu",
    "question-oh": "soru tonu",
    "question-ei": "soru tonu",
    "question-yi": "soru tonu",
    "confirmation-en": "onaylama",
    "dissatisfaction-hnn": "hoşnutsuzluk",
    "pause": "duraklama",
    "long_pause": "uzun duraklama",
    "whisper": "fısıltı",
    "emphasis": "vurgu",
    "mood:excited": "heyecanlı ton",
    "mood:sad": "üzgün ton",
    "speed:slow": "yavaş tempo",
    "speed:normal": "normal tempo",
    "speed:fast": "hızlı tempo",
    "speed:very_fast": "çok hızlı tempo",
}


def _is_mention(text: str, start: int, end: int) -> bool:
    """`text[start:end]` etiketi KULLANILIYOR mu, ANLATILIYOR mu?

    Sınırlar etiketin ETRAFINDAKİ boşluğu içerebilir (`_MOOD_RE` öyle yakalıyor),
    o yüzden iki yanda da boşluk kırpılır.
    """
    left = text[:start].rstrip()
    if left and left[-1] in _MENTION_QUOTES:
        return True
    return bool(_MENTION_AFTER_RE.match(text[end:end + 40].lstrip(_MENTION_QUOTES)))


# Metindeki HER `[...]` kalıbı. Eşlemesi olmayan da bu regex'e takılır ve SİLİNİR —
# "tanınmayan etiket asla seslendirilmez" garantisi buradan geliyor.
_BRACKET_RE = re.compile(r"\[[^\[\]]{0,60}\]")
# Yerleşimi CÜMLE BAŞI olan token sınıfı (kalanı satır içi kalır).
_PREFIX_TOKEN_RE = re.compile(
    r"^<\|(?:emotion|style):|^<\|prosody:(?:speed|pitch|expressive)_"
)
# Higgs kontrol token'ı — "söylenecek bir şey kaldı mı" sayımında sayılmaz.
_HIGGS_TOKEN_RE = re.compile(r"<\|[a-z_]+:[a-z_]+\|>")

# ── Duraklama token'ının YERLEŞİM KURALLARI (ikisi de ÖLÇÜLDÜ) ───────────────
# 1) BOŞLUKSUZ. `"Bir saniye <|prosody:pause|> düşüneyim"` (boşluklu) 12 örnekte 3
#    kez cümlenin ilk kelimesini yedi; boşluksuzu 0/12 → aradaki boşluk zararlı.
# 2) CÜMLE BAŞINA ÇOK YAKIN OLMASIN. Boşluksuz hâli bile etiket 2. kelimeden
#    hemen sonraysa 24'te 2 kez "Bir"i yuttu; AYNI etiket cümlenin ortasında
#    (4 kelime sonra) 24/24 temiz ve +0.32 s sessizlik ekliyor. Yani sorun
#    token'da değil, token'ın cümle başına yakınlığında.
_HUG_INLINE_RE = re.compile(r"\s*(<\|prosody:(?:pause|long_pause)\|>)\s*")
_MIN_WORDS_BEFORE_PAUSE = 3

# ── VURGU (`[emphasis]`) — kelimeyi öne çıkarma ──────────────────────────────
# 28 Tem, `experiments/vurgu/` (4 tur ölçüm + 3 tur kulak testi).
#
# Higgs kataloğunda kelime düzeyinde vurgu YOK: uydurma `<|prosody:emphasis|>`,
# `<|emphasis:strong|>` ve SSML `<emphasis>` HARFİ HARFİNE OKUNUYOR (WER 0.22-0.56,
# `katalog_yoklama.py`). O yüzden vurgu bir TOKEN'a değil, ÖLÇÜLMÜŞ bir NOKTALAMA
# yerleşimine çevrilir — kullanıcının kulakla seçtiği yol (`tire-on`):
#
#     Bugün kendine iyi bakmayı unutma — olur mu, ben hep buradayım.
#                                      ↑ sınır YALNIZ ÖNDE, arkada HİÇBİR ŞEY
#
# Dizge `experiments/vurgu/vurgu_set.py::_on_isaretli(c, " — ")` ile BİREBİR:
# soldaki boşluk ve VİRGÜL atılır, yerine " — " (boşluk + U+2014 + boşluk) gelir,
# hedeften SONRASI olduğu gibi kalır.
#
# ⚠️ ARKAYA İŞARET KOYMAK YASAK. 2. turda tire hedefin İKİ yanındaydı; kullanıcı
# altı ayrı notta "kelime sonrası uzun bekleme" dedi, ölçüm de doğruladı (hedeften
# sonra +0.31 s fazladan sessizlik; `confusion` +0.56, `arousal` +0.50). Arkadaki
# işaret atılınca bekleme taban seviyesine düştü (-0.02 s).
#
# ⚠️ SINIR HEDEFİN TAM ÖNÜNE. Deneyde "bağlı öbeği de kapsa" (işaretin "hem de"nin
# önüne alınması) denendi: ÖLÇÜM onu üstün gördü (Δ perde +2.17 vs -0.72), KULAK
# çürüttü (0/3 vs 3/3). Geriye doğru öbek kapsama mantığı canlıya ALINMADI.
#
# ⚠️ HER CÜMLEDE TUTMUYOR — yaklaşık YARISINDA tutuyor (kulak: 2/4 cümlede 3/3,
# 2/4'ünde 0/3). Kullanıcı bunu bilerek seçti çünkü başarısızlık ZARARSIZ: vurgu
# gelmez ama ses bozulmaz (dört turda da WER 0.000, baş yeme 0, bekleme yok).
EMPHASIS_TAG = "emphasis"
_EMPHASIS_SEP = " — "        # ölçülen dizge — DEĞİŞTİRME (bkz. vurgu_set._on_isaretli)
# Dönüşüm iki adımlı: `_replace` yerine bir işaretçi bırakır, `_apply_emphasis`
# sınırı kurar. Sebep: kararlar (kaçıncı vurgu, solunda kaç kelime var, sağında
# kelime kaldı mı) ancak etiketler temizlendikten SONRA verilebilir.
_EMPHASIS_MARK = "\x01"
_EMPHASIS_RE = re.compile(r"[ \t]*(,*)[ \t]*" + _EMPHASIS_MARK + r"\s*")
# Cümle başındaki vurgu ATILIR. `pause` için ölçülmüş `_MIN_WORDS_BEFORE_PAUSE = 3`
# kuralının vurguya AYNEN uygulanması gerekmiyor: ölçüm tam da bu yerleşimi kapsıyor —
# `confusion` cümlesinde ("Tam — anlamadım şimdi…") sınır 1. kelimeden sonra ve
# `tire-on` 5/5, `tire` 8/8 örnekte baş yeme 0, WER 0.000. Yani tire duraklama
# token'ı gibi ilk kelimeyi yemiyor. Ölçülmemiş tek yerleşim SIFIR kelimeli hâl
# (cümlenin en başı), o da atılır: vurgulanacak bir bağlam yok, tire orada diyalog
# çizgisine benzer. Şüphede kalırsak vurguyu kaybederiz, ilk kelimeyi ASLA.
_MIN_WORDS_BEFORE_EMPHASIS = 1


def _extract_mood(text: str) -> tuple[Optional[str], str]:
    """Metinden `[mood:X]` KONTROL işaretini çıkar → (mood | None, temiz metin).

    ANLATILAN mood işareti (`"[mood:sad] etiketini kullanırım"`) çıkarılmaz ve
    silinmez — metinde bırakılır, `_to_higgs_markup` onu okunabilir karşılığına
    çevirir. Aksi hâlde cümlede delik kalırdı (bkz. `_MENTION_AFTER_RE`).
    """
    if not text or "[" not in text:
        return None, text
    mood: Optional[str] = None

    def _drop(m: re.Match) -> str:
        nonlocal mood
        if _is_mention(text, m.start(), m.end()):
            return m.group(0)          # anlatılıyor → DOKUNMA
        if mood is None:
            mood = m.group(1).lower()
        return " "

    cleaned = _MOOD_RE.sub(_drop, text)
    if mood is None:
        return None, text
    return mood, re.sub(r"\s{2,}", " ", cleaned).strip()


def _extract_speed(text: str) -> tuple[Optional[str], str]:
    """Metinden `[speed:X]` KONTROL işaretini çıkar → (kademe | None, temiz metin).

    `_extract_mood`'un ikizi ve AYNI mention korumasını kullanır: model etiketi
    KULLANMIYOR da ANLATIYORSA ("[speed:fast] gibi bir işaretle hızlanabilirim")
    işaret metinde BIRAKILIR, `_to_higgs_markup` onu okunur karşılığına çevirir —
    aksi hâlde cümlede delik kalırdı (bkz. `_MENTION_AFTER_RE`).

    Mood'dan TEK FARKI ömrü: mood tur sonunda sıfırlanır (`reset_mood`), hız
    OTURUM BOYUNCA yaşar. Canlı şikâyet buydu: "sonraki cevapta eski tempoya döndü".
    """
    if not text or "[" not in text:
        return None, text
    level: Optional[str] = None

    def _drop(m: re.Match) -> str:
        nonlocal level
        if _is_mention(text, m.start(), m.end()):
            return m.group(0)          # anlatılıyor → DOKUNMA
        if level is None:
            level = m.group(1).lower()
        return " "

    cleaned = speech_speed.TAG_RE.sub(_drop, text)
    if level is None:
        return None, text
    return level, re.sub(r"\s{2,}", " ", cleaned).strip()


def _has_speakable(text: str) -> bool:
    """Seslendirilecek harf/rakam var mı? "...", "?!", " - " → yok."""
    return any(ch.isalnum() for ch in text)


def _apply_emphasis(body: str) -> str:
    """`_EMPHASIS_MARK` işaretçilerini ölçülen vurgu sınırına (` — `) çevir.

    Cümlede EN FAZLA BİR vurgu kalır — çok vurgu vurgusuzluktur. İlki kazanır
    (mood'da olduğu gibi), gerisi silinir. İşaretin solunda kelime yoksa ya da
    sağında söylenecek bir şey kalmadıysa işaret DÜŞER: sınır bir kelimeyi öne
    çıkarmak içindir, cümlenin ucunu süslemek için değil.

    Soldaki virgül, ölçülen metinle birebir kalmak için atılır
    (`vurgu_set._on_isaretli` → `on.rstrip().rstrip(",")`); işaret düşerse virgül
    yerinde kalır, cümlenin noktalaması bozulmaz.
    """
    if _EMPHASIS_MARK not in body:
        return body
    used = False

    def _place(m: re.Match) -> str:
        nonlocal used
        comma = m.group(1)
        before = len(_speakable_body(body[:m.start()]).split())
        after = _speakable_body(body[m.end():])
        if used:
            logger.info("TTS: fazladan vurgu işareti atıldı (cümlede en fazla bir vurgu)")
        elif before < _MIN_WORDS_BEFORE_EMPHASIS:
            logger.info("TTS: cümle başındaki vurgu işareti atıldı")
        elif not _has_speakable(after):
            logger.info("TTS: vurgulanacak kelime yok → vurgu işareti atıldı")
        else:
            used = True
            return _EMPHASIS_SEP
        return f"{comma} " if comma else " "

    return _EMPHASIS_RE.sub(_place, body)


def _to_higgs_markup(text: str, mood: Optional[str]) -> str:
    """OmniVoice `[etiket]`lerini Higgs sözdizimine çevir; TANINMAYANI SİL.

    GARANTİ: dönen metinde köşeli parantezli hiçbir kalıp KALMAZ. Higgs
    tanımadığı etiketi harfi harfine okuduğu için bu bir konfor değil, koruma.

    Yerleşim: emotion/style/prosody-cümle-başı token'ları metnin BAŞINA taşınır
    (kategori başına ilk gelen kazanır — `[mood:X]` her zaman ilk sırada, çünkü
    tur-kapsamlı ve daha belirleyici). `sfx` bulunduğu yerde kalır.

    trnorm'dan SONRA çağrılmalı: trnorm köşeli parantez içini bilerek koruyor
    (OmniVoice etiketleri seslendiriyordu), yani dönüşüm ondan önce yapılırsa
    ürettiğimiz `<|...|>` token'ları normalizasyona yakalanır.
    """
    # İç işaretçi model metninden GELEMEZ (uzunluğu koruyarak temizlenir ki
    # `_is_mention`'ın kullandığı konumlar kaymasın).
    text = text.replace(_EMPHASIS_MARK, " ")
    prefixes: list[str] = []
    seen_categories: set[str] = set()

    def _add_prefix(preset: str) -> None:
        """Ön eki yerleştir. Preset KOMBO olabilir (`<|emotion:X|><|prosody:Y|>`).

        Kombo ATOMİK: parçalarından biri dolu bir kategoriye düşüyorsa TAMAMI
        atılır. Yarısını almak eşlemeyi bozardı — `[mood:sad] … [surprise-oh]`
        cümlesinde `surprise` düşüp `expressive_high` kalsaydı üzgün cümle
        sebepsiz yere canlanırdı. İstifleme yine serbest (farklı kategoriler).
        """
        tokens = _HIGGS_TOKEN_RE.findall(preset)
        categories = {t[2:t.index(":")] for t in tokens}
        if not tokens or categories & seen_categories:
            return
        seen_categories.update(categories)
        prefixes.extend(tokens)

    if mood in MOOD_PRESETS:
        _add_prefix(MOOD_PRESETS[mood])

    def _replace(match: re.Match) -> str:
        key = match.group(0)[1:-1].strip().lower()
        if _is_mention(text, match.start(), match.end()):
            # Etiket KULLANILMIYOR, ANLATILIYOR: silmek cümlede delik bırakır.
            readable = _READABLE.get(key)
            if readable:
                logger.info("TTS: anlatılan etiket okunur hâle çevrildi: %r → %r",
                            match.group(0), readable)
                return readable
            # Tanınmayan etiket anlatılıyor → uydurma karşılık YOK, silinir.
        if key == EMPHASIS_TAG:
            # Vurgu bir Higgs TOKEN'ı değil, ölçülmüş bir NOKTALAMA yerleşimi →
            # `HIGGS_TAG_MAP`'e girmez. Kararı `_apply_emphasis` verir.
            return _EMPHASIS_MARK
        repl = HIGGS_TAG_MAP.get(key)
        if repl is None:
            # TANINMAYAN: Higgs bunu SESLİ OKUR → tek güvenli davranış silmek.
            logger.info("TTS: Higgs'in tanımadığı etiket silindi: %r", match.group(0))
            return " "
        if not repl:
            return " "
        if _PREFIX_TOKEN_RE.match(repl):
            _add_prefix(repl)
            return " "
        if repl.startswith("<|prosody:") and (
            len(text[:match.start()].split()) < _MIN_WORDS_BEFORE_PAUSE
        ):
            # Cümle başına çok yakın duraklama İLK KELİMEYİ yiyor (ölçüldü).
            # Duraklama süs; ilk kelime değil. Şüphede kalırsak duraklamayı atarız.
            logger.info("TTS: cümle başına çok yakın duraklama atıldı: %r",
                        match.group(0))
            return " "
        return repl        # satır içi (sfx / pause) — yerinde kalır

    body = _BRACKET_RE.sub(_replace, text)
    body = _apply_emphasis(body)
    body = re.sub(r"\s{2,}", " ", body).strip()
    # Duraklama token'ı İKİ YANINDAKİ boşluğu yutar (ölçüldü: boşluklu hâli
    # cümlenin ilk kelimesini yiyor). `sfx`'in resmi "boşluk yok" kuralının aynısı.
    body = _HUG_INLINE_RE.sub(r"\1", body)
    body = re.sub(r"\s+([,.!?;:…])", r"\1", body)   # silinen etiketin bıraktığı boşluk
    if body.endswith(","):                          # "…Haha," gibi asılı kalan taklit
        body = body[:-1] + "."
    return "".join(prefixes) + body


def _speakable_body(markup: str) -> str:
    """Higgs kontrol token'ları çıkarılmış hâl — "söylenecek bir şey var mı" için."""
    return _HIGGS_TOKEN_RE.sub(" ", markup)


def _ensure_final_punct(text: str) -> str:
    """Kısa metnin sonuna nokta ekle (omnivoice_tts'teki ölçülmüş EN AZ müdahale).

    Higgs'in boş çıktı davranışı BİLİNMİYOR (116 wav'ın hiçbiri boş çıkmamıştı,
    ama kısa tek kelimeler bench'te yoktu). Guard yine de duruyor: zararı yok,
    faydası kanıtlı bir sınıfta var.
    """
    stripped = text.rstrip()
    if not stripped or stripped[-1] in _FINAL_PUNCT:
        return text
    return stripped + "."


def _header_int(raw: Optional[str], default: int) -> int:
    """Sunucu başlığından tam sayı; bozuk/eksikse VARSAYILAN (başlık yüzünden tur ölmez)."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _silence_pcm() -> bytes:
    return b"\x00\x00" * int(DEFAULT_SAMPLE_RATE * NUM_CHANNELS * _SILENCE_MS / 1000)


# ── Referans parmak izi (cache anahtarı) ─────────────────────────────────────
_ref_cache: dict[str, tuple[float, Optional[str]]] = {}


def reset_ref_cache() -> None:
    """Parmak izi belleğini boşalt (test için)."""
    _ref_cache.clear()


def _fingerprint_from_payload(payload: object) -> str:
    """`GET /api/default` gövdesinden MOTOR KİMLİĞİ ÖNEKLİ parmak izi.

    Sunucu `ref_fingerprint` veriyorsa (referans KODLARININ sha256'sı) onu kullanırız —
    aynı yola başka bir wav yazılsa bile değişir. Yoksa gövdenin tamamı özetlenir.
    Önek `ENGINE_ID`: OmniVoice ile aynı referans wav'ını paylaştığımız için anahtarın
    motoru ayırt etmesi ŞART (bkz. modül başındaki uyarı).
    """
    fp: Optional[str] = None
    if isinstance(payload, dict):
        raw = payload.get("ref_fingerprint")
        if isinstance(raw, str) and raw:
            fp = raw
    if fp is None:
        material = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fp = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{ENGINE_ID}:{fp}"


async def ref_fingerprint(host: str, port: int) -> Optional[str]:
    """Higgs sunucusundaki referansın parmak izi. SALT-OKUMA (`GET /api/default`).

    None dönerse cache DEVRE DIŞI bırakılmalı: hangi referansla üretildiğini
    bilmediğimiz sesi çalmak, yeniden üretmekten daha kötü. Sonuç (başarısızlık dahil)
    TTL boyunca bellekte tutulur — her tur timeout beklemeyelim.
    """
    key = f"{host}:{port}"
    now = time.monotonic()
    hit = _ref_cache.get(key)
    if hit and now - hit[0] < _REF_TTL_S:
        return hit[1]

    try:
        timeout = aiohttp.ClientTimeout(total=_REF_TIMEOUT_S)
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.get(f"http://{host}:{port}/api/default") as resp,
        ):
            resp.raise_for_status()
            payload = json.loads(await resp.text())
    except Exception as exc:  # noqa: BLE001 — cache bir optimizasyon, tur BOZULMAZ
        logger.warning("Higgs referansı okunamadı (%s) → ses cache'i devre dışı", exc)
        _ref_cache[key] = (now - _REF_TTL_S * 0.8, None)
        return None

    fp = _fingerprint_from_payload(payload)
    _ref_cache[key] = (now, fp)
    return fp


class HiggsTTS(tts.TTS):
    """Higgs TTS 3 plugin — sistemdeki TEK TTS motoru (synthesize + reset_mood)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        voice: Optional[str] = None,
        token: Optional[str] = None,
        stream: bool = True,
        speed_control: bool = True,
    ):
        super().__init__(
            # `streaming=False` GİRDİ akışı içindir (SynthesizeStream yok, livekit
            # cümle cümle çağırır). ÇIKTIYI parça parça push etmeye engel DEĞİL.
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=DEFAULT_SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._host = host
        self._port = port
        self._voice = voice
        self._token = token
        self._current_mood: Optional[str] = None
        # Chunked PCM ucu. Kapatınca tam-WAV yoluna dönülür (TEK SATIRLIK geri dönüş:
        # worker/.env'e `HIGGS_STREAM=0`). Sunucu ikisini de sunmaya devam ediyor.
        self._stream = stream
        # ── Konuşma hızı: OTURUM ÖMÜRLÜ ayar (mood gibi TUR ömürlü DEĞİL) ────
        # Bayrak kapalıyken davranış BUGÜNKÜYLE bire bir aynı: `[speed:X]` işareti
        # yine metinden silinir (asla seslendirilmez) ama tempo dönüşümü YAPILMAZ.
        self._speed_control = speed_control
        self._speed = speech_speed.DEFAULT

    def reset_mood(self) -> None:
        """Yeni tur başında nötr'e dön (agent.py agent_state 'thinking' hook'undan).

        ⚠️ HIZA DOKUNMAZ. Mood cümlelik bir renk, hız KALICI bir ayar; kullanıcının
        canlı şikâyeti tam da hızın bir sonraki turda eski hâline dönmesiydi.
        """
        self._current_mood = None

    # ── Konuşma hızı ─────────────────────────────────────────────────────────
    @property
    def speed(self) -> str:
        """Oturumun geçerli hız kademesi (`speech_speed.LEVELS`)."""
        return self._speed

    def set_speed(self, level: Optional[str]) -> bool:
        """Kademeyi ayarla. DEĞİŞTİYSE True. Bilinmeyen kademe YOK SAYILIR.

        Bayrak kapalıyken de kademe TUTULUR (yalnız tempo uygulanmaz): denetim
        katmanı "istek karşılandı mı" sorusuna aynı cevabı versin, davranış
        bayrağa göre iki türlü olmasın.
        """
        if not speech_speed.is_level(level) or level == self._speed:
            return False
        logger.info("TTS: konuşma hızı %s → %s (oran %.2f)",
                    speech_speed.TR.get(self._speed), speech_speed.TR.get(level or ""),
                    speech_speed.rate(level))
        self._speed = level or speech_speed.DEFAULT
        return True

    def tempo_rate(self) -> float:
        """Bu anda uygulanacak tempo oranı. Bayrak kapalı → 1.0 (hiç işlem yok)."""
        return speech_speed.rate(self._speed) if self._speed_control else 1.0

    def _http_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/tts"

    def _stream_url(self) -> str:
        return f"http://{self._host}:{self._port}/api/tts/stream"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "HiggsChunkedStream":
        return HiggsChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class HiggsChunkedStream(tts.ChunkedStream):
    """Bir synth turu. Higgs'te tek yol var: HTTP POST → tam WAV."""

    def __init__(
        self,
        *,
        tts: HiggsTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._higgs = tts
        # Emitter `initialize()` edildi mi? KENDİMİZ takip ediyoruz: `pushed_duration()`
        # asenkron güncellenen bir sayaç, hemen ardından okumak güvenilmez.
        self._emitter_ready = False
        # Bu turun tempo filtresi (WSOLA). `normal` hızda None → TEK BİR EK İŞLEM YOK.
        self._tempo: Optional[tempo.TempoStream] = None

    # ── Emitter yardımcıları ─────────────────────────────────────────────────
    def _ensure_emitter(
        self,
        output_emitter: tts.AudioEmitter,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        """Emitter'ı bir kez başlat (ikinci `initialize()` RuntimeError verir)."""
        if self._emitter_ready:
            return
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=sample_rate,
            num_channels=num_channels,
            mime_type="audio/pcm",
        )
        self._emitter_ready = True

    # ── Tempo (konuşma hızı) ─────────────────────────────────────────────────
    # ⚠️ FİLTRE STREAMING'İN ÇIKIŞINDA. Blok (8 kare) / lookahead (8 kare) / sol
    # bağlam (16 kare) mantığına DOKUNULMAZ — kodek ne çözdüyse o çözülür, biz
    # yalnız çıkan PCM'in temposunu değiştiririz. Ölçüldü (`experiments/konusma-hizi`):
    # ilk ses gecikmesi 517 ms → 517 ms (+1 ms), çünkü filtrenin istediği ~55 ms
    # ilk bloğun 320 ms'i İÇİNDE soğuruluyor.
    def _begin_tempo(self, sample_rate: int = DEFAULT_SAMPLE_RATE) -> None:
        rate = self._higgs.tempo_rate()
        self._tempo = tempo.TempoStream(rate, sample_rate) if rate != 1.0 else None

    def _shape(self, pcm: bytes) -> bytes:
        return self._tempo.feed(pcm) if self._tempo is not None else pcm

    def _finish(self, output_emitter: tts.AudioEmitter) -> None:
        """Tempo kuyruğunu boşalt ve segmenti kapat. Her başarı/yarım yolda ÇAĞRILIR.

        Kuyruk atlanırsa cümlenin son ~50 ms'i düşerdi (son hece kırpılması).
        """
        if self._tempo is not None:
            tail = self._tempo.flush()
            if tail:
                self._ensure_emitter(output_emitter)
                output_emitter.push(tail)
        output_emitter.flush()

    def _emit_pcm(
        self,
        output_emitter: tts.AudioEmitter,
        pcm: bytes,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        shaped = self._shape(pcm)
        if shaped:
            self._ensure_emitter(
                output_emitter, sample_rate=sample_rate, num_channels=num_channels
            )
            output_emitter.push(shaped)
        self._finish(output_emitter)

    def _push_pcm(
        self,
        output_emitter: tts.AudioEmitter,
        pcm: bytes,
        *,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        num_channels: int = NUM_CHANNELS,
    ) -> None:
        """Parça push et, flush ETME (streaming: flush cümlenin SONUNDA bir kez)."""
        shaped = self._shape(pcm)
        if not shaped:
            return                     # filtre henüz kare doldurmadı — kayıp YOK
        self._ensure_emitter(
            output_emitter, sample_rate=sample_rate, num_channels=num_channels
        )
        output_emitter.push(shaped)

    def _emit_silence(self, output_emitter: tts.AudioEmitter) -> None:
        """Turu KURTARAN sessizlik — sessiz kalmak kabul, çökmek DEĞİL."""
        try:
            self._ensure_emitter(output_emitter)
            output_emitter.push(_silence_pcm())
            output_emitter.flush()
        except Exception:  # noqa: BLE001 — guard'ın kendisi ASLA patlamasın
            logger.warning("sessizlik guard'ı emitter'a yazamadı", exc_info=True)

    # ── Ana akış ─────────────────────────────────────────────────────────────
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        # SIFIRLA: livekit `_main_task` her deneme için YENİ bir AudioEmitter kurup
        # `_run()`'ı tekrar çağırıyor. Bayrak devreden kalırsa ikinci denemede
        # `initialize()` atlanır ve `push()` "AudioEmitter isn't started" ile patlar.
        self._emitter_ready = False

        mood, text = _extract_mood(self._input_text)
        if mood is not None:
            self._higgs._current_mood = mood
        effective_mood = self._higgs._current_mood

        # Konuşma hızı: `[speed:X]` OTURUM ayarını değiştirir ve BU cümleden itibaren
        # geçerlidir. Sıra kritik — filtre metinden değil, kademeden kuruluyor.
        level, text = _extract_speed(text)
        if level is not None:
            self._higgs.set_speed(level)
        self._begin_tempo()

        # Türkçe normalizasyon — SIRA KRİTİK: mood İŞARETİ ÇIKARILDIKTAN SONRA,
        # gönderimden ÖNCE. Ölçüm (29 cümle, ASR geri-dönüş): WER 0.058 → 0.028.
        text = normalize_tr(text)

        # Guard 1: söylenecek bir şey kalmadı ([mood:X]-only, "..." vb.).
        if not _has_speakable(text):
            logger.info("TTS: seslendirilecek metin yok (%r) → sessiz geçildi", text[:20])
            self._emit_silence(output_emitter)
            return

        mood_key = effective_mood if effective_mood in KNOWN_MOODS else None

        if tts_cache.is_cacheable(text):
            await self._run_short(output_emitter, text, mood_key)
            return

        sent = self._prepare(text, mood_key)
        if sent is None:
            logger.info("TTS: etiket dönüşümünden sonra metin kalmadı → sessiz geçildi")
            self._emit_silence(output_emitter)
            return

        pushed = 0
        try:
            async for pcm, sample_rate, num_channels in self._iter_pcm(sent, mood_key):
                if not pcm:
                    continue
                self._push_pcm(
                    output_emitter, pcm,
                    sample_rate=sample_rate, num_channels=num_channels,
                )
                pushed += len(pcm)
            if not pushed:
                raise RuntimeError("Higgs: ses üretilmedi (boş yanıt)")
            self._finish(output_emitter)
        except Exception as exc:  # noqa: BLE001 — TTS hatası turu ÖLDÜRMESİN
            logger.warning(
                "TTS başarısız (%s: %s) → cümlenin kalanı sessiz geçildi "
                "[%d karakter, %d bayt çalındı]",
                type(exc).__name__, exc, len(text), pushed,
            )
            if pushed:
                # YARIM ses zaten çıktı: sessizlik EKLEME (tur zaten kurtuldu),
                # sadece parçayı kapat ki livekit segmenti bitmiş saysın.
                try:
                    self._finish(output_emitter)
                except Exception:  # noqa: BLE001
                    logger.warning("yarım akış flush edilemedi", exc_info=True)
            else:
                self._emit_silence(output_emitter)

    async def _run_short(
        self, output_emitter: tts.AudioEmitter, text: str, mood: Optional[str]
    ) -> None:
        """Kısa/kalıp metin: cache'ten çal, yoksa üret + boşsa tek retry + cache'e yaz.

        Sıra: cache → sentez → (boş/hata) tek retry → sessizlik. Hiçbir adım turu öldürmez.
        """
        key: Optional[str] = None
        ref = await ref_fingerprint(self._higgs._host, self._higgs._port)
        if ref is not None:
            key = tts_cache.make_key(
                text, ref=ref, voice=self._higgs._voice, mood=mood
            )
            cached = tts_cache.load(key)
            if cached:
                logger.info("TTS cache HIT (%d bayt): %.40r", len(cached), text)
                self._emit_pcm(output_emitter, cached)
                return

        sent = self._prepare(text, mood)
        if sent is None:
            logger.info("TTS: etiket dönüşümünden sonra metin kalmadı → sessiz geçildi")
            self._emit_silence(output_emitter)
            return

        # RETRY'İN ŞARTI: HİÇ ses push edilmemiş olmak. Streaming'de parçalar
        # çıktıkça çalınıyor; yarısı çalınmış bir cümleyi baştan sentezlemek
        # kullanıcıya cümleyi İKİ KEZ dinletir. O yüzden ses başladıktan sonra
        # gelen hata retry ETMEZ, elde ne varsa onunla kapatılır.
        pushed = 0
        for attempt in range(_SHORT_TEXT_RETRIES + 1):
            parts: list[bytes] = []
            sample_rate, num_channels = DEFAULT_SAMPLE_RATE, NUM_CHANNELS
            try:
                async for pcm, sr, ch in self._iter_pcm(sent, mood):
                    if not pcm:
                        continue
                    sample_rate, num_channels = sr, ch
                    parts.append(pcm)
                    self._push_pcm(
                        output_emitter, pcm, sample_rate=sr, num_channels=ch
                    )
                    pushed += len(pcm)
            except Exception as exc:  # noqa: BLE001 — hata da retry'a değer
                logger.warning(
                    "TTS kısa metin hatası (%s: %s) deneme %d/%d: %.40r",
                    type(exc).__name__, exc, attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                if pushed:
                    break              # yarım ses çıktı → tekrarlama
                continue

            if not parts:
                logger.warning(
                    "TTS boş çıktı, deneme %d/%d: %.40r",
                    attempt + 1, _SHORT_TEXT_RETRIES + 1, text,
                )
                continue

            # CACHE streaming'de de YAZILIR: parçalar birleştirilince tam PCM.
            # Bir sonraki turda cache HIT olur ve sentez HİÇ yapılmaz (streaming'e
            # de gerek kalmaz — anında çalar).
            if key is not None:
                tts_cache.store(
                    key, b"".join(parts),
                    sample_rate=sample_rate, channels=num_channels,
                )
            self._finish(output_emitter)
            return

        if pushed:
            # Yarım ses çalındı: tur kurtuldu, sessizlik EKLEME — sadece kapat.
            try:
                self._finish(output_emitter)
            except Exception:  # noqa: BLE001
                logger.warning("yarım akış flush edilemedi", exc_info=True)
            return

        logger.warning("TTS kısa metin üretilemedi → sessiz geçildi: %.40r", text)
        self._emit_silence(output_emitter)

    def _prepare(self, text: str, mood: Optional[str]) -> Optional[str]:
        """Gönderime hazır Higgs metni; söylenecek bir şey kalmadıysa None.

        `_to_higgs_markup` tanınmayan `[...]` kalıplarını sildiği için sonuç BOŞ
        kalabilir (örn. yalnız `[question-en]` içeren cümle). O durumda sunucuya
        boş metin gönderip 400 yemek yerine doğrudan sessizliğe düşülür.
        """
        markup = _to_higgs_markup(text, mood)
        if not _has_speakable(_speakable_body(markup)):
            return None
        return _ensure_final_punct(markup)

    async def _iter_pcm(self, text: str, mood: Optional[str]):
        """PCM parçalarını (pcm, sample_rate, kanal) olarak akıtır — TEK GİRİŞ NOKTASI.

        Streaming açıksa `/api/tts/stream`'i dinler; kapalıysa (`HIGGS_STREAM=0`)
        tam WAV'ı çekip TEK parça verir. Çağıranlar (`_run`, `_run_short`) iki
        durumu da aynı döngüyle işliyor, yani geri dönüş yolu ayrı kod değil.
        """
        if getattr(self._higgs, "_stream", True):
            async for part in self._stream_pcm(text, mood):
                yield part
            return
        yield await self._collect(text, mood)

    async def _stream_pcm(self, text: str, mood: Optional[str]):
        """`POST /api/tts/stream` → chunked ham PCM parçaları.

        SES BAŞLAMADAN gelen HTTP hatası (400/500/502/503) exception'a döner;
        çağıran sessizliğe düşer, tur ölmez. Ses BAŞLADIKTAN sonra sunucu
        yarıda kalırsa aiohttp gövdeyi eksik görüp hata atar — o da exception'a
        döner ama çağıran o ana kadarki sesi zaten çalmıştır.

        ⚠️ TEK BAYT HİZALAMA: s16le'de bir örnek 2 bayt, ama TCP parçaları
        keyfî sınırdan gelebilir. Tek sayılı kuyruk bir SONRAKİ parçaya devredilir;
        aksi hâlde emitter'a yarım örnek gider ve ses kayar (çıtırtı).
        """
        payload = {"text": text}
        if mood:
            payload["mood"] = mood
        if self._higgs._voice:
            payload["voice"] = self._higgs._voice

        timeout = aiohttp.ClientTimeout(
            total=_SYNTH_TIMEOUT_S, sock_read=_STREAM_STALL_S
        )
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.post(self._higgs._stream_url(), json=payload) as resp,
        ):
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise RuntimeError(f"Higgs HTTP {resp.status}: {detail}")

            sample_rate = _header_int(
                resp.headers.get("X-Higgs-Sample-Rate"), DEFAULT_SAMPLE_RATE
            )
            num_channels = _header_int(
                resp.headers.get("X-Higgs-Channels"), NUM_CHANNELS
            )

            tail = b""
            async for raw in resp.content.iter_any():
                if not raw:
                    continue
                buf = tail + raw
                cut = len(buf) - (len(buf) % 2)
                tail = buf[cut:]
                if cut:
                    yield buf[:cut], sample_rate, num_channels
            if tail:
                # Yarım örnekle bitmek sunucu hatası olurdu; sessizce yutma.
                logger.warning("Higgs akışı tek bayt artıkla bitti (%d) → atıldı", len(tail))

    async def _collect(self, text: str, mood: Optional[str]) -> tuple[bytes, int, int]:
        """`POST /api/tts` → (s16le PCM, sample_rate, kanal). WAV zaten s16le."""
        wav_bytes = await self._post_tts(text, mood)
        with wave.open(io.BytesIO(wav_bytes), "rb") as wav:
            sample_rate = wav.getframerate() or DEFAULT_SAMPLE_RATE
            num_channels = wav.getnchannels() or NUM_CHANNELS
            pcm = wav.readframes(wav.getnframes())
        return pcm, sample_rate, num_channels

    async def _post_tts(self, text: str, mood: Optional[str]) -> bytes:
        """Ham WAV baytları. HTTP hatası (400/500/502/503) exception'a döner —
        sunucu sessizce boş ses dönmüyor, biz de sessizce yutmuyoruz."""
        payload = {"text": text, "format": "wav"}
        if mood:
            payload["mood"] = mood
        if self._higgs._voice:
            payload["voice"] = self._higgs._voice

        timeout = aiohttp.ClientTimeout(total=_SYNTH_TIMEOUT_S)
        async with (
            aiohttp.ClientSession(timeout=timeout) as sess,
            sess.post(self._higgs._http_url(), json=payload) as resp,
        ):
            if resp.status != 200:
                detail = (await resp.text())[:200]
                raise RuntimeError(f"Higgs HTTP {resp.status}: {detail}")
            return await resp.read()
