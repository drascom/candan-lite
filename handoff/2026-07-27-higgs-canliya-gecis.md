# Canlı TTS → Higgs TTS 3 (4B). OmniVoice beklemede.

**Tarih:** 2026-07-27 · **Sunucu:** root@192.168.0.25 · **Durum:** CANLI, çalışıyor
İlgili: `handoff/2026-07-27-higgs-tts3-hazirlik.md` (gece ölçümleri), `experiments/higgs-tts3/README.md`

---

## 0. GERİ DÖNÜŞ — TEK BLOK (telefondan kopyala-yapıştır)

OmniVoice'a dönmek için **tek komut bloğu**. `Conflicts=` sayesinde
`start omnivoice-bridge` higgs-tts'i kendiliğinden durdurur.

> **Her iki blok da 27 Tem'de GERÇEKTEN ÇALIŞTIRILDI**, kopyalanıp bırakılmadı:
> Higgs → OmniVoice → Higgs turu yapıldı. OmniVoice dönüşünde canlı sentez alındı
> (`POST :8808/api/tts` → 200, ASR: *"geri dönüş yolu çalışıyor"*), worker log'u temiz.

```bash
ssh root@192.168.0.25 'sed -i "/^TTS_ENGINE=/d" /opt/candan-lite/worker/.env && \
systemctl stop higgs-tts && systemctl disable higgs-tts && \
systemctl enable --now omnivoice-bridge && sleep 25 && \
/opt/candan-lite/worker/.venv/bin/python -c "import sys; sys.path.insert(0,\"/opt/candan-lite/worker\"); import tts_cache; print(\"cache silinen:\", tts_cache.clear())" && \
systemctl restart candan-worker && sleep 15 && \
systemctl is-active omnivoice-bridge candan-worker && \
curl -s -m 5 http://127.0.0.1:8808/api/default'
```

Beklenen çıktı: `active` `active` ve `{"ref_audio":"/opt/omnivoice/default-ref.wav",...}`.

**Higgs'e geri dönmek** (aynı mantık, ters yön):

```bash
ssh root@192.168.0.25 'grep -q "^TTS_ENGINE=" /opt/candan-lite/worker/.env || \
printf "\nTTS_ENGINE=higgs\n" >> /opt/candan-lite/worker/.env; \
systemctl stop omnivoice-bridge && systemctl disable omnivoice-bridge && \
systemctl enable --now higgs-tts && sleep 40 && \
/opt/candan-lite/worker/.venv/bin/python -c "import sys; sys.path.insert(0,\"/opt/candan-lite/worker\"); import tts_cache; print(\"cache silinen:\", tts_cache.clear())" && \
systemctl restart candan-worker && sleep 15 && \
systemctl is-active higgs-tts candan-worker && curl -s -m 5 http://127.0.0.1:8809/health'
```

> **Cache silmek ŞART.** Anahtar motor kimliğini içeriyor, yani teknik olarak eski
> girdiler zaten okunmaz; ama diskte durup yer kaplamalarının anlamı yok ve motor
> değiştikten sonra temiz başlamak teşhisi kolaylaştırıyor.

---

## 1. Ne değişti

| Katman | Önce | Şimdi |
|---|---|---|
| Servis | `omnivoice-bridge.service` :8808 | **`higgs-tts.service` :8809** |
| Sunucu kodu | `/opt/omnivoice/bridge_server.py` | **`/opt/higgs-tts/server.py`** (repo: `server/higgs-tts/`) |
| Worker eklentisi | `worker/omnivoice_tts.py` | **`worker/higgs_tts.py`** |
| Seçim | (yok) | `worker/.env` → `TTS_ENGINE=higgs` |
| Boot | omnivoice enabled | higgs-tts **enabled**, omnivoice **disabled** (unit duruyor) |

**OmniVoice'a DOKUNULMADI:** `/opt/omnivoice/`, `/opt/omnivoice-venv/`,
`omnivoice-bridge.service` ve `worker/omnivoice_tts.py` aynen duruyor. Tek fark:
servis durduruldu ve boot'ta otomatik başlamıyor.

`worker/.env` yedeği: `/opt/candan-lite/worker/.env.bak-pre-higgs-20260727`

---

## 2. Higgs servisi

`/opt/higgs-tts/server.py` (+ `higgs.env`, `refs/default-ref.codes.pt`),
unit `higgs-tts.service`. Yalnız stdlib + torch/transformers — fastapi/uvicorn
KURULMADI (yeni paket = yeni kırılma yüzeyi).

* Model ve referans kodları **süreç başında BİR KEZ** yükleniyor: yükleme 2.95 s,
  ısıtma 1.29 s, sonra her istek yalnız `generate_speech()`.
* Referans OmniVoice ile **AYNI wav** (`/opt/omnivoice/default-ref.wav`, salt okunur),
  180 kare kodlanmış hâli diskten okunuyor.

| uç | ne döner |
|---|---|
| `GET /health` | hazır mı, yükleme/ısıtma süresi, sentez ve hata sayacı, son sentezin RTF'i · yüklenirken **503** |
| `GET /api/default` | motor + referans parmak izi (cache anahtarı buradan) |
| `POST /api/tts` | JSON `{"text","mood","format"}` → **audio/wav 24 kHz mono s16le** |

Hata politikası — **sessizce boş ses YOK**: `400` metin boş · `503` model hazır değil ·
`502` model 0 frame üretti · `500` beklenmedik hata (gövdede JSON `error`).

Parmak izi referans **KODLARININ** sha256'sı: aynı yola başka bir wav yazılsa bile
değişir. (OmniVoice köprüsü yalnız yol+metin döndürüyordu; bilinen zayıflığı buydu.)

---

## 3. Worker tarafı — korunan kazanımlar

`worker/higgs_tts.py`, `OmniVoiceTTS` ile aynı yüzey (`synthesize` + `reset_mood`),
`agent.py` içindeki mood/session kablolaması motordan bağımsız. **`turn_detection`
bloğuna dokunulmadı.**

* `normalize_tr()` (trnorm) — aynı yerde, aynı sırada.
* `tts_cache` — aynı depo, **anahtar `higgs-tts-3-4b:<parmak izi>` ile önekli**.
* Kısa metin guard'ı: cümle sonu noktası → tek retry → sessizlik.
* TTS hatası turu öldürmüyor: her yolda en az bir parça push ediliyor.

### ⚠️ Etiketler — geçişin en kritik kısmı

Higgs `PROMPTING.md`: *tanınmayan etiket çıktıyı bozar ya da **harfi harfine okunur***.
`pi/AGENTS.md` + `pi/personas/candan.md` hâlâ OmniVoice etiketleri ürettiriyor
(**prompt tarafına bilerek dokunulmadı** — OmniVoice'a dönüş bozulmasın) ve `trnorm`
köşeli parantez içini bilerek koruyor. Müdahalesiz Candan sesli olarak "laughter",
"sigh", "mood excited" derdi.

`_to_higgs_markup()` trnorm'dan **SONRA** çalışır. Eşleme tablosu tek yerde
(`MOOD_PRESETS` + `HIGGS_TAG_MAP`, `worker/higgs_tts.py`):

| OmniVoice | Higgs | yerleşim |
|---|---|---|
| `[mood:excited]` | `<|emotion:enthusiasm|>` | cümle başı |
| `[mood:sad]` | `<|emotion:sadness|>` | cümle başı |
| `[laughter]` | `<|sfx:laughter|>Haha, ` | yerinde (taklit etikete bitişik) |
| `[sigh]` | `<|sfx:sigh|>Haah, ` | yerinde |
| `[surprise-ah/oh/wa/yo]` | `<|emotion:surprise|>` | cümle başı |
| `[question-*]`, `[confirmation-en]`, `[dissatisfaction-hnn]` | — | **SİLİNİR** |
| tanınmayan her `[...]` | — | **SİLİNİR** |

emotion/style/prosody token'ları cümle başına **taşınır**, kategori başına bir tane.

### `<|emotion:elation|>` KULLANMAYIN — ölçüldü, bozuk

"excited" için akla ilk gelen `elation`'dı. Aynı cümle, 12'şer örnek, Whisper
geri-dönüşüyle anlaşılırlık:

| token | anlaşılır |
|---|---|
| düz (etiketsiz) | 12/12 |
| `<\|emotion:enthusiasm\|>` | **12/12** ← seçilen |
| `<\|emotion:amusement\|>` | 12/12 |
| `<\|emotion:sadness\|>` | 12/12 |
| `<\|emotion:surprise\|>` | 12/12 |
| `<\|sfx:laughter\|>` / `<\|sfx:sigh\|>` | 12/12 |
| `<\|emotion:elation\|>` | **5–7/12** — cümle BAŞINI yiyor, 3 örnek tamamen boş, 2'si alakasız ("Bye bye!") |

**Ders:** tokenizer etiketi tanıyor olması (hepsi tek özel token) çalıştığı anlamına
GELMİYOR. Duygu işi derinleştirilirken her yeni token aynı şekilde ölçülmeli.

---

## 4. Kanıt

```
higgs-tts /health : ready=true, load 2.95 s, warmup 1.29 s, synth 163, fail 0
POST /api/tts     : 200, 357164 B WAV, 7.44 s ses, RTF 0.504  (bench: 0.516 ✓)
ASR geri-dönüş    : "Merhaba Ismet, ben Camdan. Toplantı saat 14.30'da başlıyor ve
                     şirket 1.250.000 lira kar açıkladı."   ← metin birebir
Etiket kanıtı     : "[laughter] Çok komikti…"  → ses: "Çok komikti gerçekten güldüm."
                    "[question-en] Bunu sen mi yaptın? [uydurma-etiket] Söyle bakalım."
                                              → ses: "Bunu sen mi yaptın? Söyle bakalım."
                    hiçbirinde "laughter"/"question"/"mood" SESLENDİRİLMEDİ
Cache             : ilk tur 0.89 s sentez → ikinci tur 0.00 s (HIT)
```

VRAM (kart 24.5 GB): llama-server 9556 + whisper 2386 + **higgs 9134** = 21.1 GB,
**~3.4 GB marj**. `candan-brain` ve `whisper` hiç durdurulmadı.

Testler: `cd worker && ./.venv/bin/python -m unittest discover -s tests` → **202 OK**
(179 taban + 23 yeni, `worker/tests/test_higgs_tts.py`).

---

## 5. Bilinen sınırlar / açık kalanlar

1. **Streaming yok.** Higgs cümleyi bitirip tek parça veriyor: cümle başına
   1.0–2.5 s (ölçülen RTF ~0.52). OmniVoice'ta WS streaming vardı. Algılanan gecikme
   kısa turlarda ARTAR. Kendi kare-blok streaming'imiz mümkün (ilk blok 0.25 s
   ölçülmüştü, `run_higgs.py:first_audio_latency`) — ayrı iş.
2. **Duygu eşlemesi minimum.** Tablodakiler dışında hiçbir Higgs özelliği (style,
   prosody, pause) kullanılmıyor. Genişletmeden önce §3'teki ölçümü tekrarla.
3. **Kapanmamış etiket** (`[laughter` gibi, kapanış parantezi yok) temizlik regex'ine
   takılmaz ve sesli okunur. Model böyle bozuk bir etiket üretirse tek kelime kaçar.
4. `omnivoice-bridge` artık boot'ta başlamıyor (`disabled`). Unit ve tüm dosyaları
   yerinde; §0'daki blok geri açıyor.
5. `/opt/candan-lite` çalışma ağacı kirli olduğu için `candan-auto-deploy` zaten
   atlıyor — Higgs dosyaları oto-deploy tarafından EZİLMEZ. (Bu geçişten önce de
   böyleydi.)
