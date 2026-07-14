"""Tool kataloğu — ROUTER'IN TEK KAYNAĞI (single source of truth).

Router'ın gördüğü tool listesi, JSON şeması ve prompt'u BURADAN türetilir. Tool
eklemek/çıkarmak için YALNIZCA bu dosya değişir; router.py'de hardcode liste YOKTUR.

## Katalog neden burada, pi extension'larında değil?
Tool'ların ÇALIŞAN kodu TypeScript'te (pi/extensions/family-memory/index.ts,
pi/extensions/websearch/index.ts) ve veriyi ORASI sahiplenir. Ama router (Python) o
tool'ları LLM'e tanıtmak için ad+açıklama+parametre şemasına ihtiyaç duyar. pi bu
şemayı dışarı veren bir arayüz sunmuyor (RPC komut yüzeyi: prompt/abort/steer/
follow_up/state/model/... — "tool listesini ver" YOK). Dolayısıyla router-tarafı
katalog burada, TEK yerde tutulur.

## tier — YETKİ KATALOGLA VERİLİR, PROMPTLA DEĞİL
  low  : geri alınabilir / küçük sonuçlu → router'a GÖSTERİLİR (23 tane)
  high : geri alınamaz / dışarı çıkan (para, mesaj, mail, silme) → router'a HİÇ
         GÖSTERİLMEZ; ana modelde kalır (7 tane)
Benchmark kanıtı (experiments/router-bench): high tool katalogda görünürse model
%92-100 oranında tereddütsüz çağırıyor; prompt'ta "çağırma" demek İŞE YARAMIYOR.
Tek etkili kontrol: katalogdan çıkarmak. router_catalog() bunu uygular.

## origin — bir tool GERÇEKTEN var mı?
  real     : repoda çalışan kodu VAR (family-memory / websearch extension'ları)
  planned  : docs/MULTI-CLIENT-PLAN.md §7 — yazılmasına karar verildi, KOD YOK
  invented : benchmark için uydurulmuş, makul ama KOD YOK. (Semantik-komşu
             tuzakları bunların etrafında kurgulanmıştı; katalogda kalmaları
             router'ın "yok olanı uydurma" davranışını ölçtüğümüz hâli korur.)
`origin != "real"` olan bir tool router tarafından SEÇİLEBİLİR ama ÇALIŞTIRILAMAZ
(çalışan kodu yok) → dispatch onu executor'suz sayıp ana modele düşer. Bkz. router.py.

Ad/açıklama/parametreler experiments/router-bench/router_set.py ile BİREBİR AYNIDIR.
Ölçülen doğruluk (TR flag şeması: recall %94.1, multi_intent 6/6) bu metne bağlıdır —
açıklamaları değiştirmek benchmark'ı geçersiz kılar.
"""
from typing import Any, Optional


def _T(name: str, desc: str, props: Optional[dict] = None, req: Optional[list] = None,
       tier: str = "low", origin: str = "invented") -> dict:
    return {
        "type": "function",
        "tier": tier,
        "origin": origin,
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": req or []},
        },
    }


def _S(d: str) -> dict:
    return {"type": "string", "description": d}


def _N(d: str) -> dict:
    return {"type": "number", "description": d}


# ---------------------------------------------------------------------------
# KATALOG (30 tool = 23 low + 7 high)
# ---------------------------------------------------------------------------
CATALOG: list[dict] = [
    # ---------- GERÇEK — pi/extensions/family-memory/index.ts ----------
    _T("memory_add",
       "Store a durable fact/note in persistent memory (a name, a date, a preference, a habit). "
       "This is a FACT that has no time trigger. scope: 'private' (default) | 'family' | 'project:<name>'.",
       {"text": _S("the durable note, one line"),
        "scope": _S("'private' (default) | 'family' | 'project:<name>'")},
       ["text"], tier="low", origin="real"),

    _T("memory_search",
       "Search persistent memory for something that was saved earlier (facts, dates, names, preferences).",
       {"query": _S("search keywords"), "limit": _N("max results (default 5)")},
       ["query"], tier="low", origin="real"),

    _T("soul_add",
       "Store a behaviour instruction / personal preference about HOW the assistant should act "
       "('call me by my name', 'do not talk so much', 'be gentler in the morning'). "
       "Not a fact, not a timed reminder.",
       {"text": _S("the behaviour instruction, one line"), "scope": _S("'self' (default) | 'family'")},
       ["text"], tier="low", origin="real"),

    _T("memory_consolidate",
       "Rewrite and shrink a memory file (profile or family) when it has grown too large. "
       "Destructive rewrite — only when explicitly asked.",
       {"file": _S("'profile' | 'family'"), "text": _S("new shortened content")},
       ["file", "text"], tier="high", origin="real"),

    _T("reminder_add",
       "Set a reminder/alarm that FIRES at a time and is spoken out loud to the user. "
       "Use when the user wants to BE REMINDED of something later.",
       {"text": _S("what to remind about (short)"),
        "at": _S("absolute time, e.g. '09:00', 'tomorrow 20:00', '2026-07-20 14:30'"),
        "in_minutes": _N("relative: minutes from now")},
       ["text"], tier="low", origin="real"),

    _T("reminder_list", "List the user's pending reminders.",
       {"limit": _N("max rows (default 10)")}, [], tier="low", origin="real"),

    _T("reminder_cancel", "Cancel/delete a pending reminder.",
       {"id": _N("reminder id"), "text": _S("approximate text of the reminder")},
       [], tier="high", origin="real"),

    _T("web_search", "Search the internet for up-to-date information.",
       {"query": _S("search query, short keywords"), "max_results": _N("max results (default 3)")},
       ["query"], tier="low", origin="real"),

    # ---------- PLANLI — docs/MULTI-CLIENT-PLAN.md §7 (kod YOK) ----------
    _T("message_leave",
       "Send/leave a spoken message for ANOTHER person in the house; it is played from the "
       "speaker in their room (or queued until they are seen).",
       {"target": _S("who the message is for, e.g. 'Neva'"), "text": _S("the message")},
       ["target", "text"], tier="high", origin="planned"),

    _T("intercom_open",
       "Open a live two-way intercom call to another person's room speaker.",
       {"target": _S("who to call")}, ["target"], tier="high", origin="planned"),

    # ---------- UYDURMA ama makul (kod YOK) ----------
    _T("clock_now", "Tell the current time or today's date.",
       {"kind": _S("'time' or 'date'")}, [], tier="low"),

    _T("timer_set", "Start a countdown timer (kitchen timer). It beeps when it runs out.",
       {"minutes": _N("how many minutes"), "label": _S("what the timer is for, e.g. 'pasta'")},
       ["minutes"], tier="low"),

    _T("weather", "Get the weather forecast for a city.",
       {"city": _S("city name"), "day": _S("which day, e.g. 'today', 'tomorrow', 'weekend'")},
       ["city"], tier="low"),

    _T("shopping_add", "Add items to the household shopping list.",
       {"items": _S("items to add")}, ["items"], tier="low"),

    _T("shopping_list", "Read out the current shopping list.", {}, [], tier="low"),

    _T("light_control", "Turn the smart LIGHTS in a room of the house on or off.",
       {"room": _S("which room"), "state": _S("'on' or 'off'")}, ["state"], tier="low"),

    _T("media_play", "Play music or a video (Spotify/YouTube) on the house speakers.",
       {"artist": _S("artist"), "track": _S("song or video name"),
        "platform": _S("'spotify' or 'youtube'"), "for_whom": _S("who it is played for, e.g. 'dad'")},
       [], tier="low"),

    _T("media_stop", "Stop the music/video that is currently playing.", {}, [], tier="low"),

    _T("volume_set", "Set the volume of the assistant's own speaker.",
       {"level": _N("0-100"), "direction": _S("'up' or 'down'")}, [], tier="low"),

    _T("diet_log", "Log a meal that was eaten into the diet diary.",
       {"food": _S("the food eaten"), "meal": _S("breakfast/lunch/dinner/snack"),
        "amount": _S("quantity or portion")}, ["food"], tier="low"),

    _T("diet_summary", "Give the diet / calorie summary for a day.",
       {"day": _S("which day")}, [], tier="low"),

    _T("school_exam_schedule", "Get a child's school exam schedule.",
       {"person": _S("student name"), "subject": _S("subject name")}, ["person"], tier="low"),

    _T("match_result", "Get a football team's latest match result / score.",
       {"team": _S("team name")}, ["team"], tier="low"),

    _T("mail_check", "Check the inbox, summarise urgent mails and meeting requests.",
       {"filter": _S("e.g. 'meeting', 'urgent'")}, [], tier="low"),

    _T("mail_send", "Compose and SEND an e-mail to someone.",
       {"to": _S("recipient"), "subject": _S("subject"), "body": _S("mail body")},
       ["to", "body"], tier="high"),

    _T("med_reminder", "Set a recurring medication reminder.",
       {"medicine": _S("medication name"), "at": _S("when"), "person": _S("for whom")},
       ["at"], tier="low"),

    _T("translate", "Translate a piece of text into another language.",
       {"text": _S("text to translate"), "target_lang": _S("target language")},
       ["text", "target_lang"], tier="low"),

    _T("calendar_add", "Add an appointment/event to the family calendar.",
       {"title": _S("event title"), "at": _S("when"), "with_whom": _S("with whom")},
       ["at"], tier="low"),

    _T("calendar_delete", "Delete/cancel an event from the family calendar.",
       {"target": _S("which event")}, ["target"], tier="high"),

    _T("money_send", "Send money from the user's bank account to someone. Irreversible.",
       {"recipient": _S("who to send to"), "amount": _N("amount"), "currency": _S("currency")},
       ["recipient", "amount"], tier="high"),
]

TOOL_TIER: dict[str, str] = {t["function"]["name"]: t["tier"] for t in CATALOG}
TOOL_ORIGIN: dict[str, str] = {t["function"]["name"]: t["origin"] for t in CATALOG}
HIGH_TOOLS: list[str] = [n for n, v in TOOL_TIER.items() if v == "high"]
LOW_TOOLS: list[str] = [n for n, v in TOOL_TIER.items() if v == "low"]
# Çalışan kodu OLAN tool'lar (pi extension'larında). origin != real → kod yok.
REAL_TOOLS: list[str] = [n for n, v in TOOL_ORIGIN.items() if v == "real"]


def router_catalog() -> list[dict]:
    """Router'a GÖSTERİLEN katalog: yalnızca low-tier (23 tool).

    high-tier'ı burada eliyoruz — prompt'ta değil. Yetki katalogla verilir."""
    return [t for t in CATALOG if t["tier"] == "low"]


def router_tool_names() -> list[str]:
    """json_schema enum'u — geçersiz tool adı üretmek İMKÂNSIZ olsun."""
    return [t["function"]["name"] for t in router_catalog()]


# ---------------------------------------------------------------------------
# ŞEMA — benchmark'ta kazanan "flag" şeması
#   {"tool": <enum|null>, "args": object, "multi_intent": bool}
# multi_intent=true → tool çağrısı ATILIR, ana modele düşülür (router.py).
# ---------------------------------------------------------------------------
def router_json_schema() -> dict:
    names = router_tool_names()
    return {
        "type": "object",
        "properties": {
            "tool": {"anyOf": [{"type": "string", "enum": names}, {"type": "null"}]},
            "args": {"type": "object"},
            "multi_intent": {"type": "boolean"},
        },
        "required": ["tool", "args", "multi_intent"],
    }


# ---------------------------------------------------------------------------
# PROMPT — Qwen3.5 chat template'inin (tmpl/qwen35.jinja) tool bloğu, elle
# açılmış hâli. enable_thinking=false, add_generation_prompt=true.
# Benchmark ile BİREBİR aynı metin: ölçülen doğruluk buna bağlı.
#
# STATİK ÖNEK (prefix) = system bloğu + tool listesi → HER ÇAĞRIDA AYNI.
# llama-server `cache_prompt` ile bunun KV-cache'i yeniden kullanılır
# (prefill ~210ms → ~6ms). Değişken kısım (kullanıcı cümlesi) SONA gelir —
# önek sabit kalsın diye sıralama böyle. Bozarsan cache'i kaybedersin.
# ---------------------------------------------------------------------------
_TOOL_CALL_FORMAT = (
    "\n\nIf you choose to call a function ONLY reply in the following format with NO suffix:"
    "\n\n<tool_call>\n<function=example_function_name>\n<parameter=example_parameter_1>\nvalue_1\n"
    "</parameter>\n<parameter=example_parameter_2>\nThis is the value for the second parameter\n"
    "that can span\nmultiple lines\n</parameter>\n</function>\n</tool_call>"
    "\n\n<IMPORTANT>\nReminder:\n"
    "- Function calls MUST follow the specified format: an inner <function=...></function> block "
    "must be nested within <tool_call></tool_call> XML tags\n"
    "- Required parameters MUST be specified\n"
    "- You may provide optional reasoning for your function call in natural language BEFORE the "
    "function call, but NOT after\n"
    "- If there is no function call available, answer the question like normal with your current "
    "knowledge and do not tell the user about function calls\n</IMPORTANT>"
)

# Çıktı talimatı — kullanıcı cümlesinin ARDINA eklenir (benchmark: INSTR["flag"]).
INSTRUCTION = (
    "\n\nAnswer ONLY with a JSON object of the form "
    '{"tool": "<one of the tool names above, or null>", "args": {<arguments>}, '
    '"multi_intent": <true|false>}. '
    'Set "multi_intent" to true if the user asked for MORE THAN ONE separate thing in this '
    "sentence, false otherwise. "
    "If none of the tools fit, or the user is just chatting / venting / asking your opinion, "
    "or the request is something you cannot do with these tools, answer "
    '{"tool": null, "args": {}, "multi_intent": false}.'
)

STOP = ["<|im_end|>"]

# ---------------------------------------------------------------------------
# ÇEVİRİ SATIRI — TR+EN (worker/translate.py, ROUTER_TRANSLATE)
#
# Türkçe cümlenin İngilizce çevirisi, cümlenin ARDINA PARANTEZ İÇİNDE eklenir.
# Çeviri VERİDİR, EMİR DEĞİL. Bu ayrım hayati:
#
#   ÖLÇÜLDÜ (139+20 vaka, Qwen3.5-4B Q8; experiments/router-bench/res_v_*.json):
#     kalıp                                    recall  arg   trap_neigh  trap_all
#     TR-direkt (baseline)                      94.1   87.5     72.7       80.8
#     "argümanları TR'den al, tool'u EN'den"    98.5   92.5      9.1       34.6  ← ÇÖKTÜ
#     "tool'u EN ile seç, args'ı TR'den doldur" 95.6   95.4      4.5        1.9  ← ÇÖKTÜ
#     BU KALIP (yalnızca parantez içinde çeviri) 97.1   90.9     81.8       84.6  ← KAZANAN
#
#   "Tool'u seç / argümanları doldur" gibi bir EMİR, modeli cümlenin bir TOOL ÇAĞRISI
#   olduğuna ikna ediyor ve ABSTAIN'i (sohbet/tuzak/desteklenmeyen cihaz) yok ediyor.
#   Cümleyi yalnızca İKİ DİLDE göstermek ise abstain'i BOZMADAN tuzak direncini
#   artırıyor ("perdeleri kapat", "kombi aç" → light_control hatası düzeliyor).
#
# Argümanlar Türkçe orijinalden çıkmaya devam eder (özel isim korunur: "Kuzu Kuzu"
# çeviride "Lamb Lamb" olur; bu kalıpta argümana yine "Kuzu Kuzu" yazılır).
# METNİ DEĞİŞTİRME — ölçülen davranış bu metne bağlı.
# ---------------------------------------------------------------------------
TRANSLATION_SUFFIX = "\n\n(English translation of the sentence above: {en})"


def _static_prefix() -> str:
    import json

    parts = ["<|im_start|>system\n# Tools\n\nYou have access to the following functions:\n\n<tools>"]
    for t in router_catalog():
        spec = {"type": "function", "function": t["function"]}
        parts.append("\n" + json.dumps(spec, ensure_ascii=False))
    parts.append("\n</tools>")
    parts.append(_TOOL_CALL_FORMAT)
    parts.append("<|im_end|>\n")
    return "".join(parts)


# Süreç ömrü boyunca bir kez kurulur; her turda yeniden birleştirmeye gerek yok
# (ve byte-aynı kalması KV-cache isabeti için ŞART).
STATIC_PREFIX: str = _static_prefix()


def build_prompt(text: str, text_en: Optional[str] = None) -> str:
    """Ham Qwen prompt'u: statik önek + kullanıcı cümlesi (+ çeviri) + çıktı talimatı.

    `text_en` verilirse (ROUTER_TRANSLATE açık ve çeviri servisi cevap verdiyse) cümlenin
    İngilizce çevirisi parantez içinde eklenir — bkz. TRANSLATION_SUFFIX. Çeviri YOKSA
    (servis kapalı/timeout) prompt bugünküyle BİREBİR aynıdır."""
    user = text
    if text_en:
        user += TRANSLATION_SUFFIX.format(en=text_en)
    return (
        STATIC_PREFIX
        + "<|im_start|>user\n" + user + INSTRUCTION + "<|im_end|>\n"
        + "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def describe() -> dict[str, Any]:
    """Kendini kontrol (python -m tool_catalog)."""
    return {
        "tools_total": len(CATALOG),
        "router_sees_low": len(router_tool_names()),
        "high_hidden": sorted(HIGH_TOOLS),
        "real_tools": sorted(REAL_TOOLS),
        "prefix_chars": len(STATIC_PREFIX),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(describe(), ensure_ascii=False, indent=2))
    assert len(router_tool_names()) == 23, "low-tier tool sayısı 23 olmalı"
    assert not (set(HIGH_TOOLS) & set(router_tool_names())), "high tool router kataloğuna sızdı!"
    print("\nOK: router 23 low tool görüyor, 7 high tool GİZLİ.")
