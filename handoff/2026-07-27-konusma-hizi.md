# Konuşma hızı — gerçek bir kol, kalıcı bir ayar (27 Tem)

Görev: `handoff/task-2026-07-27-konusma-hizi.md`. Taban commit `82d6d19`.
Ölçüm takımı: `experiments/konusma-hizi/` (README'de tam tablo).

## 0. Geri alma (tek blok)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/higgs_tts.py.bak-hiz-20260727 worker/higgs_tts.py && \
  cp worker/truth_check.py.bak-hiz-20260727 worker/truth_check.py && \
  cp worker/pi_brain.py.bak-hiz-20260727 worker/pi_brain.py && \
  cp worker/agent.py.bak-hiz-20260727 worker/agent.py && \
  cp pi/AGENTS.md.bak-hiz-20260727 pi/AGENTS.md && \
  cp pi/personas/candan.md.bak-hiz-20260727 pi/personas/candan.md && \
  rm -f worker/tempo.py worker/speech_speed.py && systemctl restart candan-worker'
```
Sadece kolu KAPATMAK (kod kalsın) için tek satır yeter — işaret yine silinir,
tempo uygulanmaz, ses bugünküyle bire bir aynı olur:
```bash
ssh root@192.168.0.25 'echo "SPEECH_SPEED=0" >> /opt/candan-lite/worker/.env && systemctl restart candan-worker'
```
Yereldeki karşılığı: `git checkout -- worker/ pi/ && rm worker/tempo.py worker/speech_speed.py`.

## 1. Canlı hata

```
Ayhan : Evet, biraz konuşma hızlandırır mısın?
Candan: Tabii Ayhan, konuşma hızımı biraz daha artırıyorum.
Ayhan : Hayır olmadı. İki birim daha arttır.
Candan: Tamam Ayhan, hızımı iki birim daha artırıyorum.
Ayhan : Hayır değil. Hâlâ çok yavaş konuşuyorsun.
```

**Tempo hiç değişmedi ve değişemezdi**; üstelik model olmayan bir "birim" uydurdu.
`truth_check`'in kapattığı *araç yalanı*nın kardeşi: **yetenek yalanı**.

## 2. ÖLÇÜM — önce ölç, sonra seç

Koşul başına **12 örnek** (3 metin × 4), canlı `POST /api/tts/stream`, referans
klonu, Whisper geri-dönüşü. Asıl sayı Δsüre DEĞİL **kelime/saniye**: kullanıcının
sorusu "duyulur biçimde hızlandı mı".

| yol | kelime/s | kazanç | WER | karar |
|---|---|---|---|---|
| taban | 2.498 | — | 0.004 | — |
| `<\|prosody:speed_slow\|>` | 2.324 | -%7.0 | 0.009 | red |
| `<\|prosody:speed_fast\|>` | 2.646 | +%5.9 | **0.030** | **RED** |
| `<\|prosody:speed_very_fast\|>` | 2.677 | +%7.2 | **0.023** | **RED** |
| WSOLA 0.85 → `slow` | 2.124 | -%15.0 | 0.004 | **CANLI** |
| WSOLA 1.15 → `fast` | 2.868 | **+%14.8** | 0.004 | **CANLI** |
| WSOLA 1.30 → `very_fast` | 3.239 | **+%29.7** | 0.004 | **CANLI** |
| WSOLA 1.45 | 3.607 | +%44.4 | 0.004 | aday, kademe DEĞİL |

Karar ölçütü "≥%15 kelime/s **ve** anlaşılırlığı bozmadan"dı. Token yolu ikisinde
de kaldı: kazanç eşiğin yarısı ve WER'i tabanın **4-7 katına** çıkarıyor.
WSOLA'da WER hiçbir kademede kıpırdamıyor (0.004 = tabanın kendisi, 12/12 anlaşıldı).

### Motorun `speed` parametresi YOK (docstring yalan söylüyordu)

`server/higgs-tts/server.py` sözleşmesinde `{"speed": 1.0}` yazıyor ama `do_POST`
ve `_do_stream` `params.get("speed")`'i **hiç okumuyor**. Canlı doğrulama
(`speed_probe.py --speed-param-testi`): `speed` yok / 0.7 / 1.4 → 8.60 / 8.04 /
8.52 s ses, yani fark örnekleme gürültüsü. Sunucuya parametre eklemek `higgs-tts`
restart'ı isterdi → bu turda **dokunulmadı** (görev şartı).

### İlk ses gecikmesi BOZULMADI

Tek cümlede (livekit TTS'e cümle cümle gider), canlı akış, 7 tekrar medyanı:

```
filtresiz  : 517 ms        tempo 1.30 : 517 ms   (+1 ms)
filtresiz  : 547 ms        tempo 1.15 : 547 ms   ( 0 ms)
```
Sebep: filtre ilk çıktısı için ~55 ms girdi ister, sunucudan gelen **ilk blok
320 ms**. Bekleme ilk bloğun İÇİNDE soğuruluyor. Streaming blok/lookahead/sol
bağlam mantığına **dokunulmadı** — filtre onun **çıkışında** duruyor.

### Perde korunuyor (basit resample olsaydı Candan'ın sesi değişirdi)

WSOLA yalnız temposu değiştirir. Otokorelasyonla ölçüldü, regresyon testine
bağlandı: 0.85 / 1.15 / 1.30 oranlarında F0 **200.0 → 200.0 Hz**.

## 3. Canlıya giren davranış

* **Dört kademe, adlandırılmış, MUTLAK**: `[speed:slow]` · (normal = işaretsiz) ·
  `[speed:fast]` · `[speed:very_fast]`. Serbest sayı YOK — "iki birim" uydurmasının
  panzehiri bu. Uçlarda clamp (`speech_speed.step`).
* **Oturum ömürlü.** Kullanıcının asıl şikâyeti "sonraki cevapta eski tempoya
  döndü"ydü. Kademe TTS eklentisinde yaşar; `reset_mood()` (tur sınırı) ona
  **dokunmaz** — mood cümlelik bir renk, hız kalıcı ayar.
* **Mekanizma yeni değil:** `[mood:X]` deseninin ikizi (`_extract_speed`, aynı
  `_is_mention` koruması, aynı "tanınmayan `[...]` silinir" garantisi). Sunucuya
  giden metinde köşeli parantez yine KALMAZ.
* **Cache bozulmadı:** `tts_cache` ham (oran 1.0) sesi saklar, tempo çalarken
  uygulanır → aynı cümle her kademede doğru hızda çalar, anahtar değişmez, bayat
  cache silmek GEREKMEDİ.
* **Varsayılan bugünkü hız.** `normal` = oran 1.0 = filtre hiç kurulmaz. Bayrak
  `SPEECH_SPEED=0` iken kademe yine tutulur ama tempo uygulanmaz.

## 4. Doğruluk denetimi — "hızlandırdım" artık denetleniyor

`truth_check` katman 2'ye eklendi (deterministik, **LLM çağrısı SIFIR**), mod
denetimiyle birebir aynı ilke: *model NE YAPILACAĞINA karar verir, harness NE
OLDUĞUNU söyler.* Kademe TUR BAŞINDA dondurulur (`PiStream._speed_before`) — tur
sonunda okumak yanıltırdı, çünkü işaret ilk cümleyle birlikte zaten uygulanmış olur.

| durum | kullanıcının duyduğu |
|---|---|
| iddia var, `[speed:X]` YOK | "Aslında konuşma hızımı değiştiremedim." |
| tavandayken "artırıyorum" | "Aslında daha hızlı konuşamıyorum, bu en hızlı kademem." |
| tabandayken "yavaşlatıyorum" | "Aslında daha yavaş konuşamıyorum, bu en yavaş kademem." |
| işaret var ve kademe gerçekten değişti | (müdahale YOK) |
| "istersen artırabilirim" (TEKLİF) | (müdahale YOK — iddia değil) |

Sınır: düzeltme, mevcut 2b deseni gibi modelin cümlesinin ARDINA eklenir
(streaming'de söylenmiş söz geri alınamaz). Kritik yazma araçlarındaki gibi tam
bastırma değil.

## 5. Yetenek tarifi gerçeğe uyduruldu (Aşama 3)

Aynı konuşmada ikinci yalan: Candan yeteneklerini sayarken **"onaylama"** dedi —
`[confirmation-en]` eşlemede SİLİNİYOR, öyle bir tepki yok. Duygu gösterisinin
sonundaki tonsuz "Peki, sen bu konuda ne düşünüyorsun?" da bundandı.

* **Çıkarıldı:** `[question-en]`, `[confirmation-en]` (ikisi de siliniyor, tepki yok).
* **Eklendi:** `[mood:warm|calm|proud|confused]` (candan.md'de yoktu), `[speed:X]`.
* **Yeni kural (iki dosyada da):** *"Elindeki işaretlerin TAMAMI bu listedir; listede
  olmayan işaret sessizce silinir. Yeteneklerini sayarken tam bu listeyi say."*
* **Prompt maliyeti:** `pi/AGENTS.md` 121 → **129** satır, `pi/personas/candan.md`
  34 → **37**. Net **+11 satır**; eklenenlerin bir kısmı, iki ölçüm dipnotunun
  birleştirilmesi ve mood/efekt satırlarının sıkıştırılmasıyla karşılandı.

## 6. Kanıt / durum

* Testler: `cd worker && ./.venv/bin/python -m unittest discover -s tests` →
  **301 OK** (271 taban + 30 yeni: `SpeedControlTest` 9×2 streaming, `TempoFilterTest`
  2, `SpeedClaimTest` 10).
* `./check.sh` → 4 ruff bulgusu, **hepsi taban** (`bench/ab_bench.py`,
  `pi_brain.py:4482/4539`); `git stash` ile HEAD'de de aynısı çıkıyor. Yeni kod 0 bulgu.
* Canlı duman testi (gerçek akış + gerçek tempo + cache yolu):
  normal 6.16 s → `[speed:fast]` 4.77 s → sonraki tur hâlâ `fast` →
  `reset_mood` sonrası hâlâ `fast` → `[speed:very_fast]` 4.23 s.
  Kısa metin cache miss/HIT ikisi de 0.439 s (tempo iki yolda da tutarlı).
  Sunucuya giden metinlerde köşeli parantez: **yok**.
* Deploy: 8 dosya, **md5 sekizi de eşleşti**, sunucuda import + tempo + denetim
  doğrulandı, `candan-worker` restart (YALNIZ o), journalctl traceback **0**.
* **`higgs-tts` ve `pi-service`'e DOKUNULMADI** (üçü de `active`). Ölçüm boyunca
  higgs-tts'e yalnız HTTP isteği atıldı; restart edilmedi.

## 7. Açık kalan

1. **KULAK TESTİ bekliyor** — ölçüm "anlaşılıyor mu"yu söylüyor, "bu kademe *biraz
   daha hızlı* demek mi"yi söylemiyor. Kullanıcı seçecek:
   ```bash
   cd experiments/konusma-hizi && ./serve.sh      # → http://localhost:8011/hiz-seti.html
   ```
   Sayfada her metin için yavaş / normal / hızlı / çok hızlı + reddedilen token
   yolu kıyas olarak duruyor.
2. **Tavan `very_fast` = 1.30.** 1.45 ölçüldü ve temiz çıktı (+%44.4, WER 0.004)
   ama kademe YAPILMADI. Kullanıcı "hâlâ yavaş" derse tavanı yükseltmek yeni ölçüm
   gerektirmez — sayfadaki `1.45 (aday)` satırını dinlemek yeter.
3. **Düzeltme cümlesi ardına ekleniyor, bastırılmıyor** (bkz. §4 sınır).
4. **OmniVoice'a geri dönüş hâlâ iki adım** ve artık `[speed:X]` de listede:
   `omnivoice_tts._MOOD_RE` onu bilmiyor, tanımadığı `[...]`'yi HARFİ HARFİNE OKUR.
   `TTS_ENGINE` satırını silmek YETMEZ, `pi/AGENTS.md` + `pi/personas/candan.md` de
   geri alınmalı.
