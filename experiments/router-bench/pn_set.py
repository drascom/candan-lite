#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OZEL ISIM (proper-noun) agirlikli vaka seti — CEVIRI KATMANI RISK OLCUMU.

Soru: TR cumleyi opus-mt ile EN'e cevirip router'a verirsek, ARGUMANDAKI OZEL ISIM
bozulur mu? ("Kuzu Kuzu" -> "Lamb Lamb" -> media_play KIRILIR.)

Bu setteki her vakanin `accept` degeri TURKCE ORIJINALDIR (her uc varyantta AYNI).
Olcut tektir: router'in dondurdugu argumanda ozel ismin TURKCE hali duruyor mu?
Ceviri katmani, tool'u dogru secse bile ismi bozarsa vaka DUSER — istenen budur.

router_set.py'ye DOKUNULMADI (baska bir worker orada calisiyor); ayri dosya.
"""
from router_set import C

# accept degerleri norm() sonrasi karsilastirilir (i/s/g/u/o/c'ye indirgenmis, kucuk harf)
PN_CASES = [
    # --- sarki/sanatci: cevirinin en kolay bozdugu yer ---
    C("pn01", "arg", "play Kuzu Kuzu", "Kuzu Kuzu çal",
      ["media_play"], {"t": ["kuzu kuzu"]}),
    C("pn02", "arg", "play Simarik by Tarkan", "Tarkan'dan Şımarık çal",
      ["media_play"], {"a": ["tarkan"], "t": ["simarik"]}),
    C("pn03", "arg", "play Gulumse by Sezen Aksu", "Sezen Aksu'dan Gülümse çal",
      ["media_play"], {"a": ["sezen"], "t": ["gulumse"]}),
    C("pn04", "arg", "play Her Seyi Yak by Duman", "Duman'dan Her Şeyi Yak çal",
      ["media_play"], {"a": ["duman"], "t": ["her seyi yak"]}),
    C("pn05", "arg", "play Sila's song Vaziyetler", "Sıla'nın Vaziyetler şarkısını aç",
      ["media_play"], {"a": ["sila"], "t": ["vaziyetler"]}),
    C("pn06", "arg", "play Affet by Muslum Gurses", "Müslüm Gürses'ten Affet çal",
      ["media_play"], {"a": ["muslum"], "t": ["affet"]}),
    C("pn07", "arg", "play Geceler by Ezhel", "Ezhel'in Geceler şarkısını çal",
      ["media_play"], {"a": ["ezhel"], "t": ["geceler"]}),
    C("pn08", "arg", "put on Yuzuklerin Efendisi", "Yüzüklerin Efendisi'ni aç",
      ["media_play"], {"t": ["yuzukler"]}),

    # --- kisi adlari ---
    C("pn09", "arg", "remember Neva's birthday is March 12", "Neva'nın doğum günü 12 Mart, aklında tut",
      ["memory_add"], {"k": ["neva"], "d": ["12"]}),
    C("pn10", "arg", "remind me to call Ayhan at 7 in the evening",
      "akşam 7'de Ayhan'ı aramamı hatırlat",
      ["reminder_add", "med_reminder"], {"k": ["ayhan"], "z": ["7", "19"]}),
    C("pn11", "arg", "when is Neva's maths exam", "Neva'nın matematik sınavı ne zaman",
      ["school_exam_schedule"], {"k": ["neva"]}),
    C("pn12", "arg", "search my memory for Ayhan's phone number",
      "Ayhan'ın telefon numarası neydi, hafızana bak",
      ["memory_search"], {"k": ["ayhan"]}),
    C("pn13", "arg", "call me Ayhan Bey from now on", "bundan sonra bana Ayhan Bey diye hitap et",
      ["soul_add"], {"k": ["ayhan"]}),

    # --- yer / takim / marka / yemek adlari ---
    C("pn14", "arg", "what was the Besiktas score", "Beşiktaş maçı kaç kaç bitti",
      ["match_result"], {"t": ["besiktas"]}),
    C("pn15", "arg", "what is the weather in Sanliurfa", "Şanlıurfa'da hava nasıl",
      ["weather"], {"c": ["sanliurfa", "urfa"]}),
    C("pn16", "arg", "I ate Iskender at noon, log it", "öğlen İskender yedim, diyetime yaz",
      ["diet_log"], {"f": ["iskender"]}),
    C("pn17", "arg", "add Torku milk and Eti biscuits to the shopping list",
      "alışveriş listesine Torku süt ve Eti bisküvi ekle",
      ["shopping_add"], {"m": ["torku"], "b": ["eti"]}),
    C("pn18", "arg", "remind me to pick Neva up from Bostanci on Saturday",
      "cumartesi Neva'yı Bostancı'dan almam gerek, hatırlat",
      ["reminder_add"], {"k": ["neva"], "y": ["bostanci"]}),
    C("pn19", "arg", "the shop in Kadikoy is called Ciya, remember that",
      "Kadıköy'deki dükkanın adı Çiya, unutma",
      ["memory_add"], {"k": ["ciya"], "y": ["kadikoy"]}),
    C("pn20", "arg", "search the web for Ataturk Airport news",
      "Atatürk Havalimanı ile ilgili haberleri ara",
      ["web_search"], {"k": ["ataturk"]}),
]
