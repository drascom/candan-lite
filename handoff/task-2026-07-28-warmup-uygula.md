# Görev — WARM-UP: prewarm'a kuru prompt + periyodik ısıtma (UYGULAMA)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

Analiz bitti: `handoff/task-2026-07-28-warmup-gecikmesi.md`. Tekrar analiz ETME, uygula.

## Önce codebase-memory

Kod aramaya Grep/Glob ile BAŞLAMA. Önce `codebase-memory-mcp`: `search_graph`,
`trace_path`, `get_code_snippet`, `get_architecture`, `search_code`. İndeks yoksa
önce `index_repository`. Grep/Glob ancak bundan sonra.

## Tespit edilen kök sebep (kanıtlanmış, tartışma yok)

`worker/pi_broker.py:323-340` + `:234-240` — prewarm pi sürecini doğuruyor ama
**bilerek prompt göndermiyor** (`:326-328`). Süreç ayakta, slot ayrılmış, ama
`candan-brain`'in KV cache'i BOŞ. İlk gerçek tur sistem prompt'u + oturum geçmişini
sıfırdan prefill ediyor → **9-17 sn** (ölçülü ~1120 tok/s).

TTS zaten ısıtılıyor (`server/higgs-tts/server.py:213`), suçu yok. Süreç doğumu 0.63-0.77 sn.

## ADIM 1 — kuru ısıtma prompt'u (asıl iş)

Prewarm, süreci doğurduktan sonra **çıktısı yutulan tek bir kısa prompt** göndersin ki
KV cache dolsun.

### ⚠️ EN KRİTİK KISIT — ısıtma turu geçmişe SIZMAMALI

Bu ısıtma turu pi'nın **oturum geçmişine, hafızasına, transcript'ine, kullanıcıya görünen
hiçbir yere yazmamalı.** Sızarsa bugün bulduğumuz kimlik hatasının kardeşini üretir:
`pi_brain.py`'de "bayat direktif geçmişte kalıyor" sorunu tam olarak böyle oluşuyor
(bkz. `handoff/task-2026-07-28-kimlik-bilinip-inkar.md`).

Bunu **KANITLA**: ısıtmadan sonra oturum geçmişinin/hafızanın değişmediğini gösteren
bir test yaz. "Sanırım sızmıyor" kabul edilmez.

### Diğer kısıtlar

* Isıtma prompt'u kısa ve nötr olsun; kişisel/aile verisi İÇERMESİN.
* `.env` ile kapatılabilir olsun (varsayılan AÇIK). Anahtar adını DEVİR'deki
  isimlendirmeye uydur, yeni bir stil icat etme.
* `candan-brain` **tek slotlu**. Isıtma, gerçek bir kullanıcı turuyla YARIŞMAMALI —
  gerçek istek geldiğinde ısıtma bekletilmeli ya da iptal edilmeli. Kullanıcıyı
  ısıtma yüzünden BEKLETME; bu düzeltmenin amacı gecikmeyi azaltmak.
* Isıtma başarısız olursa akış NORMAL devam etsin (sessiz düşüş, exception yutulmaz —
  loglanır ama turu düşürmez).

## ADIM 2 — periyodik ısıtma (keepalive)

Tek seferlik ısıtma yetmez; boşta kalınca KV düşüyor. Periyodik hafif ısıtma ekle.

* Aralık `.env` ile ayarlanabilir, kapatılabilir olsun.
* Aralığı **körlemesine seçme** — KV'nin ne kadar sürede düştüğünü koddan/config'den
  çıkar, çıkaramıyorsan raporda "TAHMİN" diye işaretle ve muhafazakâr bir değer seç.
* Aktif konuşma sırasında keepalive çalışmasın (slot çakışması).

## ADIM 3 — ucuz ek (ayrı commit)

`worker/agent.py:761-767` — `WorkerOptions`'a `prewarm_fnc` + `num_idle_processes`.
campplus.onnx + silero VAD + EOU yüklemelerini job yolundan çıkarır (~2-2.7 sn).
Maliyet: birkaç yüz MB boşta RAM. Risk düşük.

Bunu ADIM 1-2 çalıştıktan SONRA, **ayrı commit** olarak yap.

## Kapsam dışı (DOKUNMA)

* `truth_check` / `CLAIM_CHECK` sınıflandırıcısının slot çalması — DEVİR §4-6'da
  "bozuk" kayıtlı, ayrı iş. Sadece raporda etkisini NOT DÜŞ.
* Kimlik inkârı düzeltmesi (`_identity_note`, `_enroll_hint`) — SIRADAKİ iş, sen yapma.
* "Bir saniye, aklımı topluyorum" dolgusu — algı yaması, kaldırma/değiştirme.

## Sınırlar

* **Deploy YOK. `systemctl` YOK. Canlı `.25` ve oracle-stage'e DOKUNMA.**
  Deploy komutlarını çalıştırma; raporda TEK BLOK halinde YAZ, kullanıcı kendi koşacak.
* Gerçek `speakers.db`'ye yazma.
* **Görsel/canlı test YAPMA** — kullanıcı kendi gözüyle bakar. Sen sadece test suite'i koştur.
* Şu an 402 test geçiyor. Sayı DÜŞMESİN.
* Commit at (main'de kal, branch açma), PUSH ETME.

## Rapor (KISA — 15 satırı geçme)

* Isıtma nereye eklendi (`dosya:satır`)
* Sızmadığının KANITI (hangi test, ne doğruluyor)
* Keepalive aralığı ve nasıl seçildi (ölçüm mü tahmin mi)
* `.env` anahtarları (ad + varsayılan)
* Test sayısı (önce/sonra)
* Commit hash'leri
* Deploy komutları — TEK BLOK, kullanıcı kopyalayıp koşacak
* Beklenen kazanç ve neyin ölçülmeden kaldığı
