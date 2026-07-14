# Web erişimi: SearXNG (yerel arama) + pi-web-access (sayfa getirme)

**Neden bu dosya var:** aşağıdaki yapılandırmanın TAMAMI repo DIŞINDA yaşıyor
(`~/.pi/...` ve `.25` sunucusu). `models.json`'ın repo dışı kalması handoff'ta zaten
bir sorun olarak işaretliydi — aynı hatayı tekrarlamamak için burası **yeniden kurulum
reçetesi**. Yeni makinede/sunucuda bu dosyayı takip et.

## İlke

**Mümkün olan her şey yerel kalır; servis sağlayıcı ancak zorunluysa.**
Arama için hiçbir yerde anahtar, hesap veya üçüncü-taraf aboneliği YOK.

> **Dürüst not:** SearXNG bir *toplayıcı*. Kendi kutumuzda çalışıyor ama arka planda
> üst-motorlara (Bing, Startpage, DuckDuckGo, Brave...) gidiyor. Yani "sorgu evden hiç
> çıkmıyor" DEĞİL. Kazandığımız şey: **bizim kimliğimiz/hesabımız/anahtarımız yok**,
> sorgular bir hesaba bağlı değil ve hiçbir sağlayıcıya kayıtlı değiliz.

## Mimari (kim hangi tool'u veriyor)

| Tool | Veren | Nasıl çalışır | Anahtar |
|---|---|---|---|
| `web_search` | `@oresk/pi-searxng` | Kendi SearXNG'miz → `192.168.0.25:8888` | yok |
| `fetch_content` | `pi-web-access` | Doğrudan HTTP + Readability + Turndown → markdown | yok |

`pi-web-access` paketinin KENDİ `web_search`'ü de var ama **kapatıldı** (aşağıda) →
iki `web_search` tool'u çakışmıyor.

---

## 1) Sunucu: SearXNG (`.25`, Ubuntu 24.04)

`.25`'te **docker YOK** — tüm servisler native systemd (`candan-brain.service` deseni).
SearXNG de öyle kuruldu: venv + systemd.

```bash
ssh root@192.168.0.25

apt-get install -y python3-dev python3-venv build-essential \
  libxslt1-dev zlib1g-dev libffi-dev libssl-dev

useradd --system --shell /bin/false --home-dir /usr/local/searxng --create-home searxng
git clone --depth=1 https://github.com/searxng/searxng.git /usr/local/searxng/searxng-src
chown -R searxng:searxng /usr/local/searxng

su -s /bin/bash searxng -c '
  python3 -m venv /usr/local/searxng/venv
  /usr/local/searxng/venv/bin/pip install --upgrade pip setuptools wheel
  cd /usr/local/searxng/searxng-src
  /usr/local/searxng/venv/bin/pip install -r requirements.txt -r requirements-server.txt
'
```

> **Tuzak:** `pip install -e .` ÇALIŞMAZ (build backend `msgspec`'i build-time'da arıyor,
> repoda `pyproject.toml` yok). Gerek de yok: `PYTHONPATH` ile kaynaktan koşuyoruz.

### `/etc/searxng/settings.yml`

Kritik olan üç şey — üçü de SearXNG varsayılanında YANLIŞ tarafta:

1. **`search.formats` içine `json` EKLE.** SearXNG varsayılanı JSON'ı **kapalı** tutar
   (sadece `html`). Bu satır olmadan pi eklentisi `format=json` çağrısında **403** alır.
2. **`server.limiter: false`.** `true` iken bot-tespiti KENDİ JSON çağrılarımızı 429'lar.
3. **`engines.keep_only` + motorları açıkça `disabled: false` yap** (aşağıdaki acı ders).

```yaml
use_default_settings:
  engines:
    keep_only: [duckduckgo, brave, startpage, mojeek, bing, qwant, google, wikipedia]

general:
  instance_name: 'candan-search'
  debug: false
  enable_metrics: false

server:
  secret_key: '<openssl rand -hex 32>'
  bind_address: '0.0.0.0'     # worker Mac'te, SearXNG .25'te → LAN bind şart
  port: 8888
  limiter: false              # KENDİ çağrılarımızı 429'lamasın
  public_instance: false
  image_proxy: false

search:
  safe_search: 0
  formats: [html, json]       # ← KRİTİK: json varsayılanda KAPALI
  default_lang: 'all'

outgoing:
  request_timeout: 3.0        # sesli akış: yavaş bir motor turu 12 sn'ye sürüklemesin
  max_request_timeout: 5.0
  pool_connections: 100
  pool_maxsize: 20

engines:
  # keep_only'ye koymak bunları AÇMAZ — varsayılanda 'disabled: true' geliyorlar.
  - {name: bing,   disabled: false}
  - {name: qwant,  disabled: false}
  - {name: mojeek, disabled: false}
```

### ACI DERS — motor havuzu (ölçüldü, 2026-07-14)

Önce "hafif olsun" diye **5 motor** bırakmıştık. Art arda birkaç test sorgusu atınca
**duckduckgo CAPTCHA**, **brave "too many requests"** verdi ve arama **0 sonuç** döndü —
yani *eski Qwant eklentisini öldüren arızanın aynısı*.

Motorlar **paralel** sorgulanıyor (3 sn timeout ile sınırlı) → motor eklemek duvar-saati
gecikmesini neredeyse **artırmıyor**, ama tek motorun bloke olması aramayı öldürmüyor.
**Dayanıklılık > hafiflik.** Kanıt: brave + duckduckgo + qwant üçü birden blokeyken
bing + startpage **~20 sonuç / ~1 sn** getirdi.

`google` keep_only'de olmasına rağmen `/config`'de hiç görünmüyor (bu sürümde yüklenmiyor).
Zaten en agresif bloklayan motor — peşine düşmedik.

### `/etc/systemd/system/searxng.service`

```ini
[Unit]
Description=SearXNG meta arama (candan-lite yerel web_search)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=searxng
Group=searxng
WorkingDirectory=/usr/local/searxng/searxng-src
Environment=SEARXNG_SETTINGS_PATH=/etc/searxng/settings.yml
Environment=PYTHONPATH=/usr/local/searxng/searxng-src
ExecStart=/usr/local/searxng/venv/bin/granian --interface wsgi \
          --host 0.0.0.0 --port 8888 --workers 1 searx.webapp:app
Restart=on-failure
RestartSec=5
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/usr/local/searxng

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload && systemctl enable --now searxng.service
```

**Sağlık kontrolü** (Mac'ten):

```bash
curl -s "http://192.168.0.25:8888/search?q=test&format=json" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['results']),'sonuc')"
# Hangi motorlar açık:
curl -s http://192.168.0.25:8888/config | python3 -c "import json,sys;print([e['name'] for e in json.load(sys.stdin)['engines'] if e.get('enabled') is not False])"
```

---

## 2) İstemci: pi eklentileri (Mac, repo DIŞI)

```bash
cd ~/.pi/agent/npm
npm install @oresk/pi-searxng@0.2.2 pi-web-access@0.13.0
```

Worker bunları **kurulu node_modules yolundan explicit `-e`** ile yüklüyor
(`worker/pi_brain.py` → `_build_pi_args`). `npm:paket` deseydik **her pi doğuşunda**
(persona swap sık!) paket temp dizine kurulurdu = saniyeler kaybı.

### `~/.pi/agent/pi-searxng.jsonc`

```jsonc
{
  "searxngUrl": "http://192.168.0.25:8888",
  "timeoutMs": 8000,
  "maxResults": 5,      // az sonuç = kısa tool çıktısı = bağlam şişmez
  "safesearch": "off"
}
```

### `~/.pi/web-search.json` (pi-web-access)

```json
{
  "webSearch": { "enabled": false },
  "workflow": "none",
  "allowBrowserCookies": false
}
```

- **`webSearch.enabled: false`** → pi-web-access `web_search` tool'unu **hiç kaydetmez**
  (`index.ts:1241` bu bayrakla korunuyor), ama `fetch_content` (`index.ts:1789`, koşulsuz)
  yaşar. **İki `web_search` çakışması böyle önlendi.**
- **`workflow: "none"` ZORUNLU.** Varsayılan `summary-review` bir **curator UI** (tarayıcı
  penceresi) açıp **20 sn** onay bekliyor → sesli akışta ÖLÜMCÜL. `web_search` zaten kapalı
  olduğu için ölü yol, ama savunma katmanı olarak duruyor.

### pi-web-access **Jina yaması** (yerel-öncelik)

`fetch_content` mutlu yolda **sağlayıcısız**: doğrudan HTTP → Mozilla Readability →
Turndown → markdown (`extract.ts:455`).

Ama doğrudan çekim başarısız olursa paket **sessizce `r.jina.ai`'ye** (üçüncü taraf)
düşüyordu — anahtar istemediği için kullanıcının haberi bile olmadan. Yerel-öncelik
ilkesine aykırı → **kapattık**. Bloke sayfada artık dürüstçe "getiremedim" diyor.

`~/.pi/agent/npm/node_modules/pi-web-access/extract.ts`, `extractWithJinaReader()`
fonksiyonunun **ilk satırı**:

```ts
if (process.env.PI_WEB_ALLOW_JINA !== "1") return null;
```

> ⚠️ **Bu yama `npm update pi-web-access` ile SİLİNİR.** Paketin config bayrağı YOK
> (kaynağı tarandı) → kalıcı yol maalesef yok. Güncelleme sonrası yeniden uygula.
> Kontrol:
> ```bash
> grep -c PI_WEB_ALLOW_JINA ~/.pi/agent/npm/node_modules/pi-web-access/extract.ts  # 2 olmalı
> ```
> Geri açmak istersen: `PI_WEB_ALLOW_JINA=1`.

`fetch_content`'in diğer fallback'leri (Parallel, Gemini) anahtar istiyor, bizde anahtar
yok → kendiliğinden atlanıyorlar (ölü yol).

---

## 3) Worker tarafı (repo İÇİ — `worker/pi_brain.py`)

- Allowlist'e `web_search` + `fetch_content` eklendi (`PI_TOOLS_ALLOWLIST`).
- `PI_NPM_DIR` (varsayılan `~/.pi/agent/npm/node_modules`) → eklentiler buradan `-e` ile.
- `WEB_SEARCH_LEGACY_QWANT=true` → eski Qwant eklentisine geri dön (CAPTCHA yüzünden ölü;
  sadece acil geri dönüş için).

### Eski Qwant eklentisi

`pi/extensions/websearch/index.ts` **SİLİNMEDİ**, sadece artık **yüklenmiyor**.
Qwant CAPTCHA döndürdüğü için canlıda zaten ölüydü. Silinip silinmeyeceğine kullanıcı
karar verecek.

---

## Ölçüm (2026-07-14)

| Ne | Süre |
|---|---|
| SearXNG ham JSON API (LAN) | **0.4 – 1.3 sn** (üst sınır: `request_timeout` 3 sn) |
| `web_search` uçtan uca (yerel Gemma beyin + tool + cevap) | **3.1 – 7.0 sn** (pi soğuk başlatma ~1.5 sn dahil) |
| `fetch_content` (büyük Wikipedia sayfası) | ~8.2 sn |

`PI_TURN_STALL_TIMEOUT=12` sınırının **altında**. Canlıda pi zaten ayakta (kalıcı rpc
alt-süreci) → soğuk başlatma maliyeti ödenmiyor, tur daha da kısa.

**İçerik kalitesi:** `web_search` **ham HTML DÖNDÜRMÜYOR**. Markdown liste:
başlık + URL + SearXNG'nin kendi snippet'i (400 karakterde kırpılıyor), `maxResults: 5`
→ tool çıktısı ~1-2 KB. Bağlamı şişirmiyor.
`fetch_content` ise **tam sayfayı** markdown'a çevirip veriyor → büyük sayfalarda bağlamı
şişirebilir; beynin ihtiyacı yoksa `web_search` snippet'leri yetiyor.
