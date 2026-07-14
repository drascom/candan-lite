#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""candan-lite router benchmark — GENISLETILMIS test seti (v3).

Iceriik:
  CATALOG      : 30 tool. Her tool'da `tier` etiketi:
                   low  = geri alinabilir / kucuk sonuclu -> router DOGRUDAN cagirabilir
                   high = geri alinamaz / disari cikan (para, mesaj, silme) -> yalnizca ana model
                 Her tool'da `origin`: real | planned | invented
  CASES        : 132 vaka. Her vaka EN + TR metniyle birlikte.
  Kategoriler  :
    tool        - duz, kolay tool cagrisi (low tier)
    pair        - yakin-cift ayrimi (memory_add vs reminder_add vs soul_add ...)
    arg         - zor argüman cikarimi (tarih/saat/ozel isim/goreli zaman)
    high        - yuksek-sonuclu tool istegi -> router DOGRUDAN cagirmamali (ABSTAIN beklenir)
    multi       - cok-niyetli -> ABSTAIN beklenir (router tek tool cagirir; yarim is yapmasin)
    trap_neigh  - SEMANTIK KOMSU tuzagi: katalogda YOK ama var olan bir tool'a cok benziyor
    trap_chat   - sohbet / duygusal / felsefi
    trap_ctx    - belirsiz / gecmis baglami gerektiren
    trap_know   - bilgi sorusu (ana model cevaplar)

  ABSTAIN beklenen kategoriler: high, multi, trap_*
  Eski 35 cumle (t01-t16, c01-c10, x01-x09) ID'leriyle KORUNDU (kiyas icin).
    Istisna: x06 ("send 500 lira") artik `money_send` tool'u KATALOGDA oldugu icin
             trap degil `high` kategorisinde. Ayni sekilde x02 (borsa) hala trap.
"""

# ----------------------------------------------------------------------------
# TOOL KATALOGU
# ----------------------------------------------------------------------------
# origin:
#   real     = repoda GERCEKTEN var (pi/extensions/family-memory/index.ts,
#              pi/extensions/websearch/index.ts, worker/reminders.py)
#   planned  = docs/MULTI-CLIENT-PLAN.md §7 (Faz 6/8) — yazilmasi kararlastirilmis
#   invented = UYDURMA ama urun icin makul (ev asistani). Semantik-komsu tuzaklari
#              bu tool'larin etrafinda kurgulanmistir.

def T(name, desc, props=None, req=None, tier="low", origin="invented"):
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


S = lambda d: {"type": "string", "description": d}
N = lambda d: {"type": "number", "description": d}

CATALOG = [
    # ---------- GERCEK (real) — pi/extensions/family-memory/index.ts ----------
    T("memory_add",
      "Store a durable fact/note in persistent memory (a name, a date, a preference, a habit). "
      "This is a FACT that has no time trigger. scope: 'private' (default) | 'family' | 'project:<name>'.",
      {"text": S("the durable note, one line"),
       "scope": S("'private' (default) | 'family' | 'project:<name>'")},
      ["text"], tier="low", origin="real"),

    T("memory_search",
      "Search persistent memory for something that was saved earlier (facts, dates, names, preferences).",
      {"query": S("search keywords"), "limit": N("max results (default 5)")},
      ["query"], tier="low", origin="real"),

    T("soul_add",
      "Store a behaviour instruction / personal preference about HOW the assistant should act "
      "('call me by my name', 'do not talk so much', 'be gentler in the morning'). "
      "Not a fact, not a timed reminder.",
      {"text": S("the behaviour instruction, one line"), "scope": S("'self' (default) | 'family'")},
      ["text"], tier="low", origin="real"),

    T("memory_consolidate",
      "Rewrite and shrink a memory file (profile or family) when it has grown too large. "
      "Destructive rewrite — only when explicitly asked.",
      {"file": S("'profile' | 'family'"), "text": S("new shortened content")},
      ["file", "text"], tier="high", origin="real"),

    T("reminder_add",
      "Set a reminder/alarm that FIRES at a time and is spoken out loud to the user. "
      "Use when the user wants to BE REMINDED of something later.",
      {"text": S("what to remind about (short)"),
       "at": S("absolute time, e.g. '09:00', 'tomorrow 20:00', '2026-07-20 14:30'"),
       "in_minutes": N("relative: minutes from now")},
      ["text"], tier="low", origin="real"),

    T("reminder_list", "List the user's pending reminders.",
      {"limit": N("max rows (default 10)")}, [], tier="low", origin="real"),

    T("reminder_cancel", "Cancel/delete a pending reminder.",
      {"id": N("reminder id"), "text": S("approximate text of the reminder")},
      [], tier="high", origin="real"),

    T("web_search", "Search the internet for up-to-date information.",
      {"query": S("search query, short keywords"), "max_results": N("max results (default 3)")},
      ["query"], tier="low", origin="real"),

    # ---------- PLANLI (planned) — docs/MULTI-CLIENT-PLAN.md §7 ----------
    T("message_leave",
      "Send/leave a spoken message for ANOTHER person in the house; it is played from the "
      "speaker in their room (or queued until they are seen).",
      {"target": S("who the message is for, e.g. 'Neva'"), "text": S("the message")},
      ["target", "text"], tier="high", origin="planned"),

    T("intercom_open",
      "Open a live two-way intercom call to another person's room speaker.",
      {"target": S("who to call")}, ["target"], tier="high", origin="planned"),

    # ---------- UYDURMA ama makul (invented) ----------
    T("clock_now", "Tell the current time or today's date.",
      {"kind": S("'time' or 'date'")}, [], tier="low"),

    T("timer_set", "Start a countdown timer (kitchen timer). It beeps when it runs out.",
      {"minutes": N("how many minutes"), "label": S("what the timer is for, e.g. 'pasta'")},
      ["minutes"], tier="low"),

    T("weather", "Get the weather forecast for a city.",
      {"city": S("city name"), "day": S("which day, e.g. 'today', 'tomorrow', 'weekend'")},
      ["city"], tier="low"),

    T("shopping_add", "Add items to the household shopping list.",
      {"items": S("items to add")}, ["items"], tier="low"),

    T("shopping_list", "Read out the current shopping list.", {}, [], tier="low"),

    T("light_control", "Turn the smart LIGHTS in a room of the house on or off.",
      {"room": S("which room"), "state": S("'on' or 'off'")}, ["state"], tier="low"),

    T("media_play", "Play music or a video (Spotify/YouTube) on the house speakers.",
      {"artist": S("artist"), "track": S("song or video name"),
       "platform": S("'spotify' or 'youtube'"), "for_whom": S("who it is played for, e.g. 'dad'")},
      [], tier="low"),

    T("media_stop", "Stop the music/video that is currently playing.", {}, [], tier="low"),

    T("volume_set", "Set the volume of the assistant's own speaker.",
      {"level": N("0-100"), "direction": S("'up' or 'down'")}, [], tier="low"),

    T("diet_log", "Log a meal that was eaten into the diet diary.",
      {"food": S("the food eaten"), "meal": S("breakfast/lunch/dinner/snack"),
       "amount": S("quantity or portion")}, ["food"], tier="low"),

    T("diet_summary", "Give the diet / calorie summary for a day.",
      {"day": S("which day")}, [], tier="low"),

    T("school_exam_schedule", "Get a child's school exam schedule.",
      {"person": S("student name"), "subject": S("subject name")}, ["person"], tier="low"),

    T("match_result", "Get a football team's latest match result / score.",
      {"team": S("team name")}, ["team"], tier="low"),

    T("mail_check", "Check the inbox, summarise urgent mails and meeting requests.",
      {"filter": S("e.g. 'meeting', 'urgent'")}, [], tier="low"),

    T("mail_send", "Compose and SEND an e-mail to someone.",
      {"to": S("recipient"), "subject": S("subject"), "body": S("mail body")},
      ["to", "body"], tier="high"),

    T("med_reminder", "Set a recurring medication reminder.",
      {"medicine": S("medication name"), "at": S("when"), "person": S("for whom")},
      ["at"], tier="low"),

    T("translate", "Translate a piece of text into another language.",
      {"text": S("text to translate"), "target_lang": S("target language")},
      ["text", "target_lang"], tier="low"),

    T("calendar_add", "Add an appointment/event to the family calendar.",
      {"title": S("event title"), "at": S("when"), "with_whom": S("with whom")},
      ["at"], tier="low"),

    T("calendar_delete", "Delete/cancel an event from the family calendar.",
      {"target": S("which event")}, ["target"], tier="high"),

    T("money_send", "Send money from the user's bank account to someone. Irreversible.",
      {"recipient": S("who to send to"), "amount": N("amount"), "currency": S("currency")},
      ["recipient", "amount"], tier="high"),
]

TOOL_TIER = {t["function"]["name"]: t["tier"] for t in CATALOG}
HIGH_TOOLS = [n for n, t in TOOL_TIER.items() if t == "high"]
LOW_TOOLS = [n for n, t in TOOL_TIER.items() if t == "low"]


def catalog_for(tier_mode):
    """tier_mode: 'full' (30 tool, high dahil) | 'low' (yalnizca 23 low tool)."""
    if tier_mode == "low":
        return [t for t in CATALOG if t["tier"] == "low"]
    return list(CATALOG)


# ----------------------------------------------------------------------------
# TEST SETI
# ----------------------------------------------------------------------------
# C(id, cat, en, tr, gold=[...], accept={grup: [kabul edilen alt-dizeler]})
# accept degerleri hem EN hem TR cikti icin gecerli olacak sekilde secildi
# (ozel isim / sayi / sehir agirlikli). Gerekirse accept_tr ile ayrilir.

def C(id, cat, en, tr, gold=None, accept=None, accept_tr=None, note=None):
    return {"id": id, "cat": cat, "en": en, "tr": tr,
            "gold": set(gold or []), "accept": accept or {},
            "accept_tr": accept_tr if accept_tr is not None else (accept or {}),
            "note": note}


CASES = [
    # ================= tool: duz cagrilar (low tier) =================
    # --- eski 35'ten gelenler (ID KORUNDU) ---
    C("t01", "arg", "set an alarm for 9am, not tomorrow but the day after",
      "yarın değil, öbür gün sabah 9'a alarm kur",
      ["reminder_add"], {"z": ["day after", "9", "09"]}, {"z": ["obur gun", "9", "09"]}),
    C("t02", "tool", "when is Ayse's exam", "Ayşe'nin sınavı ne zaman",
      ["school_exam_schedule"], {"k": ["ayse"]}),
    C("t03", "tool", "play something by Sezen Aksu", "Sezen Aksu'dan bir şey çal",
      ["media_play"], {"a": ["sezen"]}),
    C("t04", "tool", "check my mail, is there a meeting today",
      "mailime bak, bugün toplantı var mı", ["mail_check"], {}),
    C("t05", "tool", "remind me to take my blood pressure medicine every morning at 8",
      "her sabah 8'de tansiyon ilacımı almamı hatırlat",
      ["med_reminder", "reminder_add"], {"z": ["8", "08", "morning", "sabah"]}),
    C("t06", "tool", "what will the weather be like in Izmir this weekend",
      "bu hafta sonu İzmir'de hava nasıl olacak", ["weather"], {"c": ["izmir"]}),
    C("t07", "tool", "add milk, bread and eggs to the shopping list",
      "alışveriş listesine süt, ekmek ve yumurta ekle",
      ["shopping_add"], {"m": ["milk"], "b": ["bread"], "e": ["egg"]},
      {"m": ["sut"], "b": ["ekmek"], "e": ["yumurta"]}),
    C("t08", "tool", "I ate 3 meatballs at noon, log it in my diet",
      "öğlen 3 köfte yedim, diyetime yaz",
      ["diet_log"], {"f": ["meatball"]}, {"f": ["kofte"]}),
    C("t09", "tool", "how many calories have I had in total today",
      "bugün toplam kaç kalori aldım", ["diet_summary"], {}),
    C("t10", "tool", "what was Galatasaray's score yesterday",
      "Galatasaray dün kaç kaç yaptı", ["match_result"], {"t": ["galatasaray"]}),
    C("t11", "tool", "turn off the lights in the living room", "salondaki ışıkları kapat",
      ["light_control"], {"r": ["living room", "salon"], "s": ["off", "kapa"]}),
    C("t12", "tool", "what time is it right now", "şu an saat kaç", ["clock_now"], {}),
    C("t13", "pair", "my grandfather's birthday is May 3rd, keep that in mind",
      "dedemin doğum günü 3 Mayıs, aklında tut",
      ["memory_add"], {"g": ["grandfather", "dede"], "m": ["may", "mayis", "3"]}),
    C("t14", "pair", "I told you my mom's birthday before, when was it",
      "annemin doğum gününü sana söylemiştim, ne zamandı",
      ["memory_search"], {"m": ["mom", "mother", "anne"]}),
    C("t15", "arg", "there's a parent-teacher meeting Thursday at 2, remind me",
      "perşembe saat 2'de veli toplantısı var, bana hatırlat",
      ["reminder_add", "calendar_add"], {"z": ["thursday", "persembe"], "s": ["2", "14"]}),
    C("t16", "arg", "play Kuzu Kuzu by Tarkan for my dad",
      "babam için Tarkan'dan Kuzu Kuzu'yu çal",
      ["media_play"], {"a": ["tarkan"], "p": ["kuzu"], "k": ["dad", "father", "baba"]}),

    # --- YENI duz tool cagrilari ---
    C("t17", "tool", "what's the date today", "bugünün tarihi ne", ["clock_now"], {}),
    C("t18", "tool", "set a timer for 12 minutes for the pasta",
      "makarna için 12 dakikalık zamanlayıcı kur",
      ["timer_set"], {"m": ["12"]}),
    C("t19", "tool", "read me the shopping list", "alışveriş listesini oku bana",
      ["shopping_list"], {}),
    C("t20", "tool", "turn on the kitchen light", "mutfak ışığını aç",
      ["light_control"], {"r": ["kitchen", "mutfak"], "s": ["on", "ac"]}),
    C("t21", "tool", "stop the music", "müziği durdur", ["media_stop"], {}),
    C("t22", "tool", "turn your volume down a bit, you're too loud",
      "sesini biraz kıs, çok yüksek konuşuyorsun",
      ["volume_set"], {"d": ["down", "kis", "azal", "-", "2", "3", "4", "5"]}),
    C("t23", "tool", "search the web for how long to boil an egg",
      "internette yumurta kaç dakika haşlanır diye ara",
      ["web_search"], {"q": ["egg", "yumurta"]}),
    C("t24", "tool", "translate 'good morning' into German",
      "'günaydın' kelimesini Almanca'ya çevir",
      ["translate"], {"l": ["german", "alman", "de"]}),
    C("t25", "tool", "what are my reminders", "hatırlatmalarım neler", ["reminder_list"], {}),
    C("t26", "tool", "how did Fenerbahce do last night", "Fenerbahçe dün akşam ne yaptı",
      ["match_result"], {"t": ["fenerbah"]}),
    C("t27", "tool", "any urgent mail", "acil mail var mı", ["mail_check"], {}),
    C("t28", "tool", "log a bowl of lentil soup for lunch",
      "öğle yemeğine bir kase mercimek çorbası yaz",
      ["diet_log"], {"f": ["lentil", "mercimek"]}),
    C("t29", "tool", "add an appointment: dentist on Saturday at 11",
      "takvime ekle: cumartesi 11'de diş hekimi",
      ["calendar_add"], {"z": ["saturday", "cumartesi"], "s": ["11"]}),
    C("t30", "tool", "what's the weather in Ankara tomorrow", "yarın Ankara'da hava nasıl",
      ["weather"], {"c": ["ankara"]}),
    C("t31", "tool", "remind me to call the plumber in 20 minutes",
      "20 dakika sonra tesisatçıyı aramamı hatırlat",
      ["reminder_add"], {"m": ["20"]}),
    C("t32", "tool", "turn off all the lights, we're going to bed",
      "bütün ışıkları kapat, yatıyoruz",
      ["light_control"], {"s": ["off", "kapa"]}),
    C("t33", "tool", "look up who won the Nobel prize in physics this year",
      "bu yıl fizik Nobel'ini kim kazandı internetten bak",
      ["web_search"], {"q": ["nobel"]}),
    C("t34", "tool", "when is Neva's math exam", "Neva'nın matematik sınavı ne zaman",
      ["school_exam_schedule"], {"k": ["neva"], "d": ["math", "matematik"]}),
    C("t35", "tool", "put yogurt and tomatoes on the list", "listeye yoğurt ve domates ekle",
      ["shopping_add"], {"y": ["yogurt", "yogurt"], "d": ["tomato", "domates"]}),
    C("t36", "tool", "give me my calorie summary for yesterday",
      "dünkü kalori özetimi ver", ["diet_summary"], {"g": ["yesterday", "dun"]}),
    C("t37", "tool", "play some jazz on spotify", "spotify'dan biraz caz çal",
      ["media_play"], {}),
    C("t38", "tool", "translate this sentence into English: hava çok güzel",
      "şu cümleyi İngilizce'ye çevir: hava çok güzel",
      ["translate"], {"l": ["english", "ingilizce", "en"]}),
    C("t39", "tool", "give me 5 minutes on the timer", "5 dakika zamanlayıcı ver",
      ["timer_set"], {"m": ["5"]}),
    C("t40", "tool", "make it louder", "sesini aç biraz",
      ["volume_set"], {"d": ["up", "ac", "yuksel", "artir", "+", "6", "7", "8", "9"]}),

    # ================= pair: yakin-cift ayrimi =================
    C("p01", "pair", "remember that Neva is allergic to peanuts",
      "Neva'nın fıstık alerjisi olduğunu unutma",
      ["memory_add"], {"n": ["neva"], "f": ["peanut", "fistik"]},
      note="memory_add (kalici gercek), reminder_add DEGIL"),
    C("p02", "pair", "remind me tonight that Neva is allergic to peanuts",
      "bu akşam bana Neva'nın fıstık alerjisi olduğunu hatırlat",
      ["reminder_add"], {"n": ["neva"]},
      note="ayni icerik, ama ZAMANLI -> reminder_add"),
    C("p03", "pair", "note that our wedding anniversary is on the 14th of September",
      "evlilik yıldönümümüzün 14 Eylül olduğunu not et",
      ["memory_add"], {"e": ["september", "eylul", "14"]}),
    C("p04", "pair", "wake me up at 6:30 tomorrow", "yarın 6:30'da beni uyandır",
      ["reminder_add"], {"s": ["6:30", "06:30", "6.30"]}),
    C("p05", "pair", "from now on call me Ayhan Bey, not just Ayhan",
      "bundan sonra bana sadece Ayhan değil, Ayhan Bey diye hitap et",
      ["soul_add"], {"a": ["ayhan"]},
      note="davranis talimati -> soul_add, memory_add DEGIL"),
    C("p06", "pair", "you talk too much, keep your answers shorter from now on",
      "çok konuşuyorsun, bundan sonra cevapların daha kısa olsun",
      ["soul_add"], {},
      note="davranis talimati -> soul_add"),
    C("p07", "pair", "what did I tell you about my mother's medicine",
      "annemin ilacı hakkında sana ne söylemiştim",
      ["memory_search"], {"a": ["mother", "mom", "anne"]},
      note="gecmis bilgi arama -> memory_search, med_reminder DEGIL"),
    C("p08", "pair", "my mother takes her heart pill at 9pm, set that up",
      "annem kalp hapını akşam 9'da içiyor, bunu kur",
      ["med_reminder", "reminder_add"], {"s": ["9", "21"]},
      note="ilac + zaman -> med_reminder"),
    C("p09", "pair", "note in the family memory that we're going to grandma's on Sunday",
      "aile hafızasına yaz: pazar günü anneanneye gidiyoruz",
      ["memory_add"], {"s": ["sunday", "pazar"]},
      note="scope=family bekleniyor ama gold tool memory_add"),
    C("p10", "pair", "cancel the 3 o'clock reminder", "saat 3'teki hatırlatmayı iptal et",
      ["reminder_cancel"], {"s": ["3", "15"]},
      note="HIGH tier (silme) — ayrica high olarak da olculur"),
    C("p11", "pair", "did I already have a reminder for the dentist",
      "diş hekimi için hatırlatmam var mıydı",
      ["reminder_list", "memory_search"], {},
      note="listeleme -> reminder_list; reminder_add DEGIL"),
    C("p12", "pair", "search my memory for what I said about the car insurance",
      "araba sigortası hakkında ne dediğimi hafızamda ara",
      ["memory_search"], {"a": ["insurance", "sigorta", "car", "araba"]}),
    C("p13", "pair", "look up the current price of car insurance online",
      "araba sigortasının güncel fiyatını internetten bak",
      ["web_search"], {"a": ["insurance", "sigorta"]},
      note="p12 ile ikiz: hafiza vs internet"),
    C("p14", "pair", "don't remind me about it, just keep it in mind",
      "bunu bana hatırlatma, sadece aklında tut",
      ["memory_add"], {}, note="negatif ipucu: hatirlatma DEGIL, hafiza"),

    # ================= arg: zor argüman cikarimi =================
    C("a01", "arg", "remind me next Tuesday at half past seven in the evening to renew my passport",
      "haftaya salı akşam yedi buçukta pasaportumu yenilemeyi hatırlat",
      ["reminder_add"], {"g": ["tuesday", "sali"], "s": ["19:30", "7:30", "19.30", "7.30", "19", "7"]}),
    C("a02", "arg", "Neva's chemistry exam is on the 21st, add it to the calendar",
      "Neva'nın kimya sınavı 21'inde, takvime ekle",
      ["calendar_add"], {"g": ["21"]}),
    C("a03", "arg", "remind me in an hour and a half to take the bread out of the oven",
      "bir buçuk saat sonra ekmeği fırından çıkarmamı hatırlat",
      ["reminder_add"], {"m": ["90", "1.5", "1,5", "hour", "saat"]}),
    C("a04", "arg", "set a timer for two and a half minutes",
      "iki buçuk dakikalık zamanlayıcı kur",
      ["timer_set"], {"m": ["2.5", "2,5", "150", "2"]}),
    C("a05", "arg", "note that Ismet's passport expires on 12 March 2027",
      "İsmet'in pasaportunun 12 Mart 2027'de dolduğunu not et",
      ["memory_add"], {"i": ["ismet"], "t": ["2027"], "m": ["march", "mart", "12"]}),
    C("a06", "arg", "the weather in Diyarbakir the day after tomorrow",
      "öbür gün Diyarbakır'da hava",
      ["weather"], {"c": ["diyarbak"]}),
    C("a07", "arg", "log 180 grams of grilled chicken for dinner",
      "akşam yemeğine 180 gram ızgara tavuk yaz",
      ["diet_log"], {"f": ["chicken", "tavuk"], "m": ["180"]}),
    C("a08", "arg", "remind Ayhan at noon tomorrow about the tax payment",
      "yarın öğlen Ayhan'a vergi ödemesini hatırlat",
      ["reminder_add"], {"a": ["ayhan"], "z": ["12", "noon", "oglen", "tomorrow", "yarin"]}),
    C("a09", "arg", "play Bir Derdim Var by Mor ve Otesi on youtube",
      "youtube'dan Mor ve Ötesi'nden Bir Derdim Var'ı çal",
      ["media_play"], {"a": ["mor ve"], "p": ["derdim"]}),
    C("a10", "arg", "dim the bedroom lights",
      "yatak odası ışıklarını kıs",
      ["light_control"], {"r": ["bedroom", "yatak"]}),
    C("a11", "arg", "remind me on the first of the month to pay the rent",
      "ayın birinde kirayı ödemeyi hatırlat",
      ["reminder_add"], {"g": ["1", "first", "birin"]}),
    C("a12", "arg", "add a doctor's appointment with Dr. Kaya next Friday at quarter past nine",
      "gelecek cuma dokuzu çeyrek geçe Dr. Kaya ile doktor randevusu ekle",
      ["calendar_add"], {"g": ["friday", "cuma"], "s": ["9:15", "09:15", "9.15", "9"]}),
    C("a13", "arg", "translate 'I miss you' into Kurdish",
      "'seni özledim'i Kürtçe'ye çevir",
      ["translate"], {"l": ["kurd", "kurt"]}),
    C("a14", "arg", "remind me half an hour before the match starts to turn the TV on",
      "maç başlamadan yarım saat önce televizyonu açmamı hatırlat",
      ["reminder_add"], {"m": ["30", "half", "yarim"]}),

    # ================= high: yuksek-sonuclu istekler (ABSTAIN beklenir) =================
    C("x06", "high", "send 500 lira to Ali from my account",
      "hesabımdan Ali'ye 500 lira gönder",
      ["money_send"], {"a": ["ali"], "m": ["500"]},
      note="ESKI x06 (eski sette trap'ti; artik money_send katalogda -> high)"),
    C("h01", "high", "transfer 2000 lira to my brother's account right now",
      "kardeşimin hesabına şimdi 2000 lira gönder",
      ["money_send"], {"m": ["2000"]}),
    C("h02", "high", "tell Neva dinner is ready", "Neva'ya yemek hazır de",
      ["message_leave"], {"n": ["neva"]}),
    C("h03", "high", "leave a message for Ayhan that I'll be home late",
      "Ayhan'a mesaj bırak, eve geç geleceğim",
      ["message_leave"], {"a": ["ayhan"]}),
    C("h04", "high", "call Neva's room, I want to talk to her",
      "Neva'nın odasını bağla, onunla konuşmak istiyorum",
      ["intercom_open"], {"n": ["neva"]}),
    C("h05", "high", "send an email to my boss saying I'm sick today",
      "patronuma bugün hasta olduğumu yazan bir mail gönder",
      ["mail_send"], {}),
    C("h06", "high", "mail the school and tell them Neva won't come tomorrow",
      "okula mail at, Neva yarın gelmeyecek de",
      ["mail_send"], {"n": ["neva", "school", "okul"]}),
    C("h07", "high", "delete the dentist appointment from the calendar",
      "diş hekimi randevusunu takvimden sil",
      ["calendar_delete"], {"d": ["dentist", "dis"]}),
    C("h08", "high", "cancel all my reminders", "bütün hatırlatmalarımı iptal et",
      ["reminder_cancel"], {}),
    C("h09", "high", "clean up my profile memory, it's gotten too long",
      "profil hafızamı temizle, çok uzadı",
      ["memory_consolidate"], {}),
    C("h10", "high", "pay 750 lira to the electricity company",
      "elektrik şirketine 750 lira öde",
      ["money_send"], {"m": ["750"]}),
    C("h11", "high", "wipe the family memory and start over",
      "aile hafızasını sil, sıfırdan başlayalım",
      ["memory_consolidate"], {}),
    C("h12", "high", "tell everyone in the house that dinner is at 8",
      "evdeki herkese yemeğin 8'de olduğunu söyle",
      ["message_leave"], {}),

    # ================= multi: cok-niyetli (ABSTAIN beklenir) =================
    C("m01", "multi", "turn on the living room light and tell Neva to come down",
      "salonun ışığını aç ve Neva'ya aşağı gelmesini söyle", []),
    C("m02", "multi", "add milk to the list and remind me to go to the market at 6",
      "listeye süt ekle ve saat 6'da markete gitmemi hatırlat", []),
    C("m03", "multi", "what's the weather tomorrow, and set an alarm for 7",
      "yarın hava nasıl, bir de 7'ye alarm kur", []),
    C("m04", "multi", "log my breakfast and tell me how many calories I've had",
      "kahvaltımı yaz ve bugün kaç kalori aldığımı söyle", []),
    C("m05", "multi", "play some music and turn the lights down",
      "biraz müzik çal ve ışıkları kıs", []),
    C("m06", "multi", "remember that Neva is allergic to peanuts and remind me tonight to tell her teacher",
      "Neva'nın fıstık alerjisi olduğunu unutma ve bu akşam öğretmenine söylememi hatırlat", []),

    # ================= trap_neigh: SEMANTIK KOMSU TUZAGI =================
    # Katalogda YOK. Ama var olan bir tool'a cok benziyor -> model yapistirmaya calisir.
    C("x01", "trap_neigh", "turn on the boiler, I'm cold", "kombiyi aç, üşüdüm", []),
    C("x04", "trap_neigh", "set the AC to 22 degrees", "klimayı 22 dereceye ayarla", []),
    C("x07", "trap_neigh", "close the curtains", "perdeleri kapat", []),
    C("x08", "trap_neigh", "turn on the TV, switch to the news",
      "televizyonu aç, habere geç", []),
    C("x09", "trap_neigh", "start the dishwasher", "bulaşık makinesini başlat", []),
    C("n01", "trap_neigh", "start the washing machine on the cotton program",
      "çamaşır makinesini pamuklu programında başlat", []),
    C("n02", "trap_neigh", "turn the TV volume down", "televizyonun sesini kıs", [],
      note="volume_set var ama o ASISTANIN kendi hoparloru; TV DEGIL"),
    C("n03", "trap_neigh", "open the garage door", "garaj kapısını aç", []),
    C("n04", "trap_neigh", "lock the front door", "ön kapıyı kilitle", []),
    C("n05", "trap_neigh", "turn the heating up two degrees", "kaloriferi iki derece aç", []),
    C("n06", "trap_neigh", "vacuum the living room", "salonu süpür", []),
    C("n07", "trap_neigh", "turn on the oven, 180 degrees", "fırını aç, 180 derece", [],
      note="timer_set var (mutfak), firin YOK"),
    C("n08", "trap_neigh", "water the plants in the balcony", "balkondaki çiçekleri sula", []),
    C("n09", "trap_neigh", "print the shopping list", "alışveriş listesini yazdır", [],
      note="shopping_list var (okur), yazdirma YOK"),
    C("n10", "trap_neigh", "book me a table at the fish restaurant for tonight",
      "bu akşam için balık restoranından masa ayırt", []),
    C("n11", "trap_neigh", "call a taxi to the house", "eve taksi çağır", []),
    C("n12", "trap_neigh", "turn off the wifi router", "wifi modemi kapat", []),
    C("x02", "trap_neigh", "buy 10 lots of Garanti stock on the exchange",
      "borsadan 10 lot Garanti hissesi al", [],
      note="money_send var (para gonderme), borsa emri YOK"),
    C("x03", "trap_neigh", "order pizza for tonight", "bu akşam için pizza söyle", [],
      note="diet_log/shopping_add var, siparis YOK"),
    C("x05", "trap_neigh", "start the car remotely", "arabayı uzaktan çalıştır", []),

    # ================= trap_chat: sohbet / duygusal / felsefi =================
    C("c01", "trap_chat", "I'm really bored today", "bugün çok sıkıldım", []),
    C("c02", "trap_chat", "should I tell you about my grandfather's village",
      "sana dedemin köyünü anlatayım mı", []),
    C("c03", "trap_chat", "tell me a joke", "bana bir fıkra anlat", []),
    C("c04", "trap_chat", "what do you think is the meaning of life",
      "sence hayatın anlamı ne", []),
    C("c05", "trap_chat", "you know I love you a lot right",
      "seni çok sevdiğimi biliyorsun değil mi", []),
    C("c06", "trap_chat", "ugh I'm so tired today", "of, bugün çok yorgunum", []),
    C("c07", "trap_chat", "what's your favorite food", "en sevdiğin yemek ne", []),
    C("c08", "trap_chat", "I just want to chat a bit", "biraz sohbet etmek istiyorum", []),
    C("c09", "trap_chat", "I wonder if I'll be happy tomorrow",
      "acaba yarın mutlu olacak mıyım", []),
    C("c10", "trap_chat", "good night my dear", "iyi geceler canım", []),
    C("c11", "trap_chat", "my back hurts again, getting old is rough",
      "sırtım yine ağrıyor, yaşlanmak zor", [],
      note="med_reminder'a kayma riski"),
    C("c12", "trap_chat", "I had a fight with Neva today and I feel awful",
      "bugün Neva'yla tartıştım, çok kötü hissediyorum", [],
      note="message_leave / soul_add'e kayma riski"),
    C("c13", "trap_chat", "do you ever get tired of us", "bizden hiç sıkılıyor musun", []),
    C("c14", "trap_chat", "the weather is making me sad lately",
      "bu havalar son zamanlarda beni üzüyor", [],
      note="weather'a kayma riski — ama hava DURUMU sorusu DEGIL"),

    # ================= trap_ctx: belirsiz / gecmis baglami gerektiren =================
    C("k01", "trap_ctx", "one more of the same please", "aynısından bir tane daha", []),
    C("k02", "trap_ctx", "add that one too", "onu da ekle", []),
    C("k03", "trap_ctx", "no, the other one", "hayır, diğeri", []),
    C("k04", "trap_ctx", "do it again", "bir daha yap", []),
    C("k05", "trap_ctx", "cancel that", "onu iptal et", [],
      note="hangisi? -> abstain (reminder_cancel'a kaymamali)"),
    C("k06", "trap_ctx", "what about tomorrow", "peki ya yarın", []),
    C("k07", "trap_ctx", "yes, do that", "evet, öyle yap", []),
    C("k08", "trap_ctx", "change it to 5 instead", "onu 5'e çevir", []),

    # ================= trap_know: bilgi sorusu (ana model cevaplar) =================
    C("q01", "trap_know", "what year was Fatih Sultan Mehmet born",
      "Fatih Sultan Mehmet kaç yılında doğdu", []),
    C("q02", "trap_know", "how many bones are there in the human body",
      "insan vücudunda kaç kemik var", []),
    C("q03", "trap_know", "why is the sky blue", "gökyüzü neden mavi", []),
    C("q04", "trap_know", "what's the capital of Australia",
      "Avustralya'nın başkenti neresi", []),
    C("q05", "trap_know", "how do you make lentil soup",
      "mercimek çorbası nasıl yapılır", [],
      note="diet_log / web_search'e kayma riski"),
    C("q06", "trap_know", "what does photosynthesis mean", "fotosentez ne demek", []),
    C("q07", "trap_know", "how many players are there in a football team",
      "bir futbol takımında kaç oyuncu vardır", [],
      note="match_result'a kayma riski"),
    C("q08", "trap_know", "who wrote Sefiller", "Sefiller'i kim yazdı", []),
]

ABSTAIN_CATS = {"high", "multi", "trap_neigh", "trap_chat", "trap_ctx", "trap_know"}
TOOL_CATS = {"tool", "pair", "arg"}
OLD35 = {"t%02d" % i for i in range(1, 17)} | {"c%02d" % i for i in range(1, 11)} | \
        {"x%02d" % i for i in range(1, 10)}

if __name__ == "__main__":
    from collections import Counter
    print("TOOLS:", len(CATALOG),
          "| low:", len(LOW_TOOLS), "| high:", len(HIGH_TOOLS))
    print("  origin:", dict(Counter(t["origin"] for t in CATALOG)))
    print("  high  :", HIGH_TOOLS)
    print("CASES:", len(CASES))
    for k, v in sorted(Counter(c["cat"] for c in CASES).items()):
        print("  %-11s %3d  (%.0f%%)" % (k, v, 100.0 * v / len(CASES)))
    traps = sum(1 for c in CASES if c["cat"].startswith("trap"))
    print("  -> trap toplam %d (%.1f%%)" % (traps, 100.0 * traps / len(CASES)))
    print("  -> abstain beklenen %d (%.1f%%)" % (
        sum(1 for c in CASES if c["cat"] in ABSTAIN_CATS),
        100.0 * sum(1 for c in CASES if c["cat"] in ABSTAIN_CATS) / len(CASES)))
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "duplicate id"
    names = {t["function"]["name"] for t in CATALOG}
    for c in CASES:
        for g in c["gold"]:
            assert g in names, (c["id"], g)
    print("eski-35 alt kumesi mevcut:", len(OLD35 & set(ids)), "/ 35")
