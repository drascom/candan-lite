| model | quant | lg | tier | recall | arg | TUZAK-abst | komsu | chat | ctx | know | multi | high-abst | high-FIRE | old35 | p50ms | VRAM |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen35-4b-q8 | Q8_0 | en | full | 98 | 97 | **88** | 90 | 100 | 75 | 75 | 0 | 8 | **92** | 34/35 | 827 | 4.9GB |
| qwen35-4b-q8 | Q8_0 | en | low | 97 | 96 | **90** | 90 | 100 | 88 | 75 | 0 | 85 | **15** | 35/35 | 758 | 4.9GB |
| qwen35-4b-q8 | Q8_0 | tr | full | 94 | 85 | **88** | 85 | 100 | 75 | 88 | 0 | 15 | **85** | 33/35 | 815 | 4.9GB |
| qwen35-4b-q8 | Q8_0 | tr | low | 94 | 84 | **86** | 80 | 100 | 75 | 88 | 0 | 77 | **23** | 33/35 | 821 | 4.9GB |
| qwen35-4b-q6 | Q6_K | en | full | 98 | 97 | **90** | 90 | 100 | 88 | 75 | 0 | 8 | **92** | 34/35 | 924 | 4.9GB |
| qwen35-4b-q6 | Q6_K | en | low | 97 | 96 | **90** | 90 | 100 | 88 | 75 | 0 | 85 | **15** | 35/35 | 874 | 4.9GB |
| qwen35-4b-q5 | Q5_K_M | en | full | 97 | 96 | **90** | 90 | 100 | 88 | 75 | 0 | 15 | **85** | 34/35 | 870 | 4.5GB |
| qwen35-4b-q5 | Q5_K_M | en | low | 97 | 96 | **92** | 90 | 100 | 100 | 75 | 0 | 92 | **8** | 35/35 | 843 | 4.5GB |
| qwen35-4b-q4 | Q4_K_M | en | low | 97 | 96 | **92** | 90 | 100 | 88 | 88 | 0 | 100 | **0** | 35/35 | 786 | 4.1GB |
| xlam2-3b-q8 | Q8_0 | en | full | 97 | 91 | **76** | 75 | 93 | 50 | 75 | 0 | 8 | **92** | 32/35 | 291 | 3.8GB |
| xlam2-3b-q8 | Q8_0 | en | low | 93 | 88 | **72** | 65 | 93 | 62 | 62 | 0 | 23 | **77** | 31/35 | 292 | 3.8GB |
| xlam2-3b-q8 | Q8_0 | tr | full | 88 | 76 | **64** | 55 | 86 | 50 | 62 | 0 | 8 | **92** | 28/35 | 314 | 3.8GB |
| xlam2-3b-q8 | Q8_0 | tr | low | 85 | 75 | **54** | 35 | 86 | 62 | 38 | 0 | 23 | **77** | 28/35 | 314 | 3.8GB |
| xlam2-3b-q6 | Q6_K | en | full | 96 | 91 | **76** | 75 | 93 | 50 | 75 | 0 | 8 | **92** | 32/35 | 330 | 3.1GB |
| xlam2-3b-q6 | Q6_K | en | low | 94 | 88 | **72** | 65 | 93 | 62 | 62 | 0 | 31 | **69** | 31/35 | 323 | 3.1GB |
| xlam2-3b-q5 | Q5_K_M | en | low | 93 | 84 | **62** | 50 | 93 | 50 | 50 | 0 | 23 | **77** | 30/35 | 290 | 2.8GB |
| nemotron3-nano-4b | Q4_K_M | en | full | 96 | 91 | **74** | 70 | 93 | 88 | 38 | 0 | 0 | **100** | 31/35 | 756 | 3.1GB |
| nemotron3-nano-4b | Q4_K_M | en | low | 94 | 91 | **74** | 65 | 93 | 100 | 38 | 0 | 38 | **62** | 32/35 | 747 | 3.1GB |
| nemotron3-nano-4b | Q4_K_M | tr | low | 72 | 66 | **78** | 70 | 100 | 100 | 38 | 0 | 69 | **31** | 30/35 | 724 | 3.1GB |


### Abstain gerekirken cagrilan tool'lar (semantik komsu yapistirma)
- **qwen35-4b-q8 Q8_0 en/full** (24 hata): money_send×3, message_leave×3, mail_send×2, reminder_cancel×2, volume_set×2, web_search×2
- **qwen35-4b-q8 Q8_0 en/low** (13 hata): light_control×2, volume_set×2, web_search×2, reminder_add×1, shopping_add×1, weather×1
- **qwen35-4b-q8 Q8_0 tr/full** (23 hata): message_leave×3, money_send×2, reminder_cancel×2, light_control×2, intercom_open×1, mail_send×1
- **qwen35-4b-q8 Q8_0 tr/low** (16 hata): light_control×5, mail_check×1, shopping_add×1, weather×1, diet_log×1, media_play×1
- **qwen35-4b-q6 Q6_K en/full** (23 hata): money_send×3, message_leave×3, mail_send×2, reminder_cancel×2, web_search×2, intercom_open×1
- **qwen35-4b-q6 Q6_K en/low** (13 hata): light_control×2, volume_set×2, web_search×2, reminder_add×1, shopping_add×1, weather×1
- **qwen35-4b-q5 Q5_K_M en/full** (22 hata): message_leave×3, money_send×2, mail_send×2, reminder_cancel×2, web_search×2, intercom_open×1
- **qwen35-4b-q5 Q5_K_M en/low** (11 hata): diet_log×2, web_search×2, light_control×1, shopping_add×1, weather×1, media_play×1
- **qwen35-4b-q4 Q4_K_M en/low** (10 hata): volume_set×2, light_control×1, shopping_add×1, weather×1, diet_log×1, media_play×1
- **xlam2-3b-q8 Q8_0 en/full** (30 hata): message_leave×3, light_control×3, web_search×3, money_send×2, intercom_open×2, mail_send×2
- **xlam2-3b-q8 Q8_0 en/low** (30 hata): light_control×6, media_play×3, mail_check×3, web_search×3, reminder_list×2, memory_search×2
- **xlam2-3b-q8 Q8_0 tr/full** (36 hata): web_search×4, media_play×4, diet_log×3, message_leave×3, light_control×3, money_send×2
- **xlam2-3b-q8 Q8_0 tr/low** (39 hata): light_control×9, web_search×5, mail_check×4, memory_search×3, media_play×3, soul_add×2
- **xlam2-3b-q6 Q6_K en/full** (30 hata): message_leave×3, light_control×3, money_send×2, intercom_open×2, mail_send×2, reminder_list×2
- **xlam2-3b-q6 Q6_K en/low** (29 hata): light_control×6, media_play×3, reminder_list×3, memory_search×3, mail_check×2, reminder_add×2
- **xlam2-3b-q5 Q5_K_M en/low** (35 hata): light_control×6, media_play×4, reminder_add×4, memory_search×4, reminder_list×3, web_search×3
- **nemotron3-nano-4b Q4_K_M en/full** (32 hata): light_control×4, web_search×4, money_send×3, message_leave×3, mail_send×2, reminder_cancel×2
- **nemotron3-nano-4b Q4_K_M en/low** (27 hata): light_control×6, web_search×4, diet_log×2, mail_check×2, reminder_add×2, weather×2
- **nemotron3-nano-4b Q4_K_M tr/low** (21 hata): web_search×4, reminder_add×3, media_play×3, weather×2, diet_log×1, memory_add×1


### Hata ornekleri (tier=low, EN — uretim kosulu)

**qwen35-4b-q8 Q8_0** — 16 hata
  - [p10/pair] "cancel the 3 o'clock reminder" -> None {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> soul_add {"text": "don't remind me about it, just keep it in mind", "scope": "s
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [h02/high] "tell Neva dinner is ready" -> light_control {"room": "dining room", "state": "on"}
  - [h12/high] "tell everyone in the house that dinner is at 8" -> reminder_add {"text": "Dinner at 8", "at": "20:00"}
  - [m01/multi] "turn on the living room light and tell Neva to come down" -> light_control {"room": "living room", "state": "on"}
  - [m02/multi] "add milk to the list and remind me to go to the market at 6" -> shopping_add {"items": "milk"}
  - [m03/multi] "what's the weather tomorrow, and set an alarm for 7" -> weather {"city": "New York", "day": "tomorrow"}
  - [m04/multi] "log my breakfast and tell me how many calories I've had" -> diet_log {"food": "breakfast"}
  - [m05/multi] "play some music and turn the lights down" -> media_play {"artist": "", "track": "", "platform": ""}
  - [m06/multi] "remember that Neva is allergic to peanuts and remind me tonight to tell her teacher" -> memory_add {"text": "Neva is allergic to peanuts.", "scope": "private"}
  - [n02/trap_neigh] "turn the TV volume down" -> volume_set {"direction": "down"}
  - [n09/trap_neigh] "print the shopping list" -> shopping_list {}
  - [k08/trap_ctx] "change it to 5 instead" -> volume_set {"level": 5}

**qwen35-4b-q6 Q6_K** — 16 hata
  - [p10/pair] "cancel the 3 o'clock reminder" -> None {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> soul_add {"text": "don't remind me about it, just keep it in mind", "scope": "s
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [h02/high] "tell Neva dinner is ready" -> light_control {"room": "dining room", "state": "on"}
  - [h12/high] "tell everyone in the house that dinner is at 8" -> reminder_add {"text": "Dinner at 8", "at": "20:00"}
  - [m01/multi] "turn on the living room light and tell Neva to come down" -> light_control {"room": "living room", "state": "on"}
  - [m02/multi] "add milk to the list and remind me to go to the market at 6" -> shopping_add {"items": "milk"}
  - [m03/multi] "what's the weather tomorrow, and set an alarm for 7" -> weather {"city": "New York", "day": "tomorrow"}
  - [m04/multi] "log my breakfast and tell me how many calories I've had" -> diet_log {"food": "breakfast"}
  - [m05/multi] "play some music and turn the lights down" -> media_play {"artist": "", "track": "", "platform": ""}
  - [m06/multi] "remember that Neva is allergic to peanuts and remind me tonight to tell her teacher" -> memory_add {"text": "Neva is allergic to peanuts.", "scope": "private"}
  - [n02/trap_neigh] "turn the TV volume down" -> volume_set {"direction": "down"}
  - [n09/trap_neigh] "print the shopping list" -> shopping_list {}
  - [k08/trap_ctx] "change it to 5 instead" -> volume_set {"level": 5}

**qwen35-4b-q5 Q5_K_M** — 14 hata
  - [p10/pair] "cancel the 3 o'clock reminder" -> None {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> None {}
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [h02/high] "tell Neva dinner is ready" -> diet_log {"food": "dinner", "meal": "dinner"}
  - [m01/multi] "turn on the living room light and tell Neva to come down" -> light_control {"room": "living room", "state": "on"}
  - [m02/multi] "add milk to the list and remind me to go to the market at 6" -> shopping_add {"items": "milk"}
  - [m03/multi] "what's the weather tomorrow, and set an alarm for 7" -> weather {"city": "New York", "day": "tomorrow"}
  - [m04/multi] "log my breakfast and tell me how many calories I've had" -> diet_log {"food": "breakfast"}
  - [m05/multi] "play some music and turn the lights down" -> media_play {"artist": "", "track": "", "platform": ""}
  - [m06/multi] "remember that Neva is allergic to peanuts and remind me tonight to tell her teacher" -> memory_add {"text": "Neva is allergic to peanuts.", "scope": "family"}
  - [n02/trap_neigh] "turn the TV volume down" -> volume_set {"direction": "down"}
  - [n09/trap_neigh] "print the shopping list" -> shopping_list {}
  - [q01/trap_know] "what year was Fatih Sultan Mehmet born" -> web_search {"query": "Fatih Sultan Mehmet birth year"}
  - [q08/trap_know] "who wrote Sefiller" -> web_search {"query": "who wrote Sefiller"}

**qwen35-4b-q4 Q4_K_M** — 13 hata
  - [p10/pair] "cancel the 3 o'clock reminder" -> None {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> None {}
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [m01/multi] "turn on the living room light and tell Neva to come down" -> light_control {"room": "living room", "state": "on"}
  - [m02/multi] "add milk to the list and remind me to go to the market at 6" -> shopping_add {"items": "milk"}
  - [m03/multi] "what's the weather tomorrow, and set an alarm for 7" -> weather {"city": "New York", "day": "tomorrow"}
  - [m04/multi] "log my breakfast and tell me how many calories I've had" -> diet_log {"food": "breakfast"}
  - [m05/multi] "play some music and turn the lights down" -> media_play {"artist": "", "track": "", "platform": ""}
  - [m06/multi] "remember that Neva is allergic to peanuts and remind me tonight to tell her teacher" -> memory_add {"text": "Neva is allergic to peanuts.", "scope": "family"}
  - [n02/trap_neigh] "turn the TV volume down" -> volume_set {"level": 50, "direction": "down"}
  - [n09/trap_neigh] "print the shopping list" -> shopping_list {}
  - [k08/trap_ctx] "change it to 5 instead" -> volume_set {"level": 5}
  - [q01/trap_know] "what year was Fatih Sultan Mehmet born" -> web_search {"query": "Fatih Sultan Mehmet birth year"}

**xlam2-3b-q8 Q8_0** — 38 hata
  - [p01/pair] "remember that Neva is allergic to peanuts" -> soul_add {"text": "Neva is allergic to peanuts"}
  - [p06/pair] "you talk too much, keep your answers shorter from now on" -> None {}
  - [p07/pair] "what did I tell you about my mother's medicine" -> mail_check {"filter": "urgent"}
  - [p10/pair] "cancel the 3 o'clock reminder" -> reminder_list {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> None {}
  - [a03/arg] "remind me in an hour and a half to take the bread out of the oven" -> reminder_add {"text": "take the bread out of the oven", "at": "1:30"}
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [a12/arg] "add a doctor's appointment with Dr. Kaya next Friday at quarter past nine" -> calendar_add {"title": "Doctors Appointment", "at": "next Friday at quarter past ni
  - [h02/high] "tell Neva dinner is ready" -> media_play {"artist": "", "track": "dinner is ready", "platform": "none", "for_wh
  - [h03/high] "leave a message for Ayhan that I'll be home late" -> mail_check {"filter": "meeting"}
  - [h04/high] "call Neva's room, I want to talk to her" -> light_control {"room": "Neva's room", "state": "on"}
  - [h05/high] "send an email to my boss saying I'm sick today" -> mail_check {"filter": "urgent"}
  - [h06/high] "mail the school and tell them Neva won't come tomorrow" -> mail_check {"filter": "school"}
  - [h07/high] "delete the dentist appointment from the calendar" -> calendar_add {"title": "delete dentist appointment from calendar"}

**xlam2-3b-q6 Q6_K** — 37 hata
  - [t15/arg] "there's a parent-teacher meeting Thursday at 2, remind me" -> reminder_add {"text": "parent-teacher meeting", "at": "2039-03-15 14:00"}
  - [p06/pair] "you talk too much, keep your answers shorter from now on" -> None {}
  - [p07/pair] "what did I tell you about my mother's medicine" -> None {}
  - [p10/pair] "cancel the 3 o'clock reminder" -> reminder_list {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> None {}
  - [a03/arg] "remind me in an hour and a half to take the bread out of the oven" -> reminder_add {"text": "take the bread out of the oven", "at": "1:30"}
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [a12/arg] "add a doctor's appointment with Dr. Kaya next Friday at quarter past nine" -> calendar_add {"title": "Doctors Appointment", "at": "next Friday at quarter past ni
  - [h02/high] "tell Neva dinner is ready" -> media_play {"artist": "", "track": "dinner is ready", "platform": "none", "for_wh
  - [h03/high] "leave a message for Ayhan that I'll be home late" -> mail_check {"filter": "urgent"}
  - [h04/high] "call Neva's room, I want to talk to her" -> light_control {"room": "Neva's room", "state": "on"}
  - [h05/high] "send an email to my boss saying I'm sick today" -> mail_check {"filter": "urgent"}
  - [h07/high] "delete the dentist appointment from the calendar" -> calendar_add {"title": "delete dentist appointment from calendar"}
  - [h08/high] "cancel all my reminders" -> reminder_list {}

**xlam2-3b-q5 Q5_K_M** — 46 hata
  - [t15/arg] "there's a parent-teacher meeting Thursday at 2, remind me" -> reminder_add {"text": "parent-teacher meeting", "at": "2039-03-15 14:00"}
  - [t34/tool] "when is Neva's math exam" -> school_exam_schedule {"person": "Neva"}
  - [p01/pair] "remember that Neva is allergic to peanuts" -> soul_add {"text": "Neva is allergic to peanuts"}
  - [p06/pair] "you talk too much, keep your answers shorter from now on" -> None {}
  - [p07/pair] "what did I tell you about my mother's medicine" -> mail_check {"filter": "urgent"}
  - [p10/pair] "cancel the 3 o'clock reminder" -> reminder_list {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> soul_add {"text": "don't remind me about it, just keep it in mind"}
  - [a03/arg] "remind me in an hour and a half to take the bread out of the oven" -> reminder_add {"text": "take the bread out of the oven", "at": "01:30"}
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00"}
  - [a09/arg] "play Bir Derdim Var by Mor ve Otesi on youtube" -> media_play {"artist": "", "track": "Bir Derdim Var", "platform": "youtube", "for_
  - [a12/arg] "add a doctor's appointment with Dr. Kaya next Friday at quarter past nine" -> calendar_add {"title": "Doctors Appointment", "at": "next Friday at quarter past ni
  - [h02/high] "tell Neva dinner is ready" -> media_play {"artist": "", "track": "dinner is ready", "platform": "youtube", "for
  - [h03/high] "leave a message for Ayhan that I'll be home late" -> reminder_add {"text": "leave a message for Ayhan that I'll be home late"}
  - [h04/high] "call Neva's room, I want to talk to her" -> light_control {"room": "Neva's room", "state": "on"}

**nemotron3-nano-4b Q4_K_M** — 33 hata
  - [p06/pair] "you talk too much, keep your answers shorter from now on" -> None {}
  - [p07/pair] "what did I tell you about my mother's medicine" -> None {}
  - [p10/pair] "cancel the 3 o'clock reminder" -> reminder_list {}
  - [p14/pair] "don't remind me about it, just keep it in mind" -> None {}
  - [a01/arg] "remind me next Tuesday at half past seven in the evening to renew my passport" -> reminder_add {"text": "renew my passport", "at": "next Tuesday at half past seven i
  - [a08/arg] "remind Ayhan at noon tomorrow about the tax payment" -> reminder_add {"text": "tax payment", "at": "tomorrow 12:00", "in_minutes": 4380}
  - [h02/high] "tell Neva dinner is ready" -> diet_log {"food": "dinner", "meal": "dinner", "amount": "ready"}
  - [h03/high] "leave a message for Ayhan that I'll be home late" -> memory_add {"text": "I'll be home late for Ayhan.", "scope": "family"}
  - [h04/high] "call Neva's room, I want to talk to her" -> light_control {"room": "Neva's room", "state": "on"}
  - [h05/high] "send an email to my boss saying I'm sick today" -> mail_check {}
  - [h06/high] "mail the school and tell them Neva won't come tomorrow" -> mail_check {}
  - [h08/high] "cancel all my reminders" -> reminder_list {}
  - [h09/high] "clean up my profile memory, it's gotten too long" -> memory_search {"query": "", "limit": 10}
  - [h12/high] "tell everyone in the house that dinner is at 8" -> reminder_add {"text": "Dinner is at 8", "at": "08:00"}
