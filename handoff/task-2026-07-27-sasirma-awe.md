# Görev — `[surprise-*]` → `<|emotion:awe|>` (kulakla seçildi), test + deploy

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Karar ve gerekçesi

Kullanıcı `surprise.html` setini dinledi (16 wav, iki cümle × 8 aday). Sonuç:

* **B cümlesi** ("Sınavdan tam not almışsın…" — ünlem YOK, şaşkınlığı yalnız ton
  taşıyor, yani ZOR sınav): **`<|emotion:awe|>` kazandı** (4.08 s, Δ +0.64 s).
* **A cümlesi** ("Vay canına! Kargon bir gün erken gelmiş." — ünlem var):
  `<|emotion:surprise|><|prosody:expressive_high|>` kombosu beğenildi
  (3.12 s, Δ −0.04 s). **Kombo ÖLÇÜLMEMİŞ** → canlıya ALINMIYOR.
* Mevcut `<|emotion:surprise|>` tek başına tabandan neredeyse farksız — şaşkın
  duyulmuyor. Kullanıcının ilk şikâyeti buydu.

**Canlıya girecek olan: `awe`.** 27 Tem ölçümünde 12/12 anlaşıldı, WER 0.000,
Δsüre +0.35 s (`handoff/2026-07-27-duygu-katmani.md` §2) — yani ölçülü, kural ihlali yok.

Kullanıcının notu (koda değil, kayda geçsin): `awe` A cümlesinde "şuh / romantik,
yumuşak" duyulmuş. Ünlemli şaşırma cümlelerinde bu ton çıkarsa kombo yeniden
gündeme gelir — ama önce ölçülmesi şartıyla.

## Yapılacaklar

### 1. Eşleme değişikliği — `worker/higgs_tts.py`

`HIGGS_TAG_MAP` içindeki dört `surprise-*` anahtarı `<|emotion:surprise|>` yerine
`<|emotion:awe|>` verecek. **Etiket adları AYNEN KALIYOR** (`[surprise-oh]` vb.) —
`pi/AGENTS.md` ve `pi/personas/candan.md` içindeki tarif ve örnekler DEĞİŞMEZ,
prompt maliyeti artmaz. `_READABLE` karşılığı ("şaşırma") da değişmez.

Yorumda kısaca gerekçe: `surprise` ölçümde temizdi ama kulakta şaşkın duyulmadı
(Δsüre 21 emotion içinde en düşük, −0.02 s); `awe` kullanıcı tarafından ünlemsiz
cümlede seçildi; kombo ölçülmediği için alınmadı.

### 2. Test

* Mevcut testlerde `surprise` → `<|emotion:surprise|>` bekleyen assert varsa güncelle.
* **Tüm takım koşsun** (bugün 243 test geçiyordu). Sayı ve sonuç raporda olsun.

### 3. Deploy (kullanıcı yetkilendirdi — sıra ŞART)

```
yedek al → gönder → sunucuda import/syntax doğrula → systemctl restart candan-worker
→ journalctl'de traceback yok doğrula → md5 karşılaştır
```

* Sunucu: `root@192.168.0.25`, kök `/opt/candan-lite`.
* Yedek adı: `worker/higgs_tts.py.bak-sasirma-20260727`.
* ⚠️ **`pi-service`'e DOKUNMA.** `candan-worker` ona `Requires=` ile bağlı —
  pi-service'i durdurmak worker'ı da durdurur ve geri getirmez (27 Tem 14:31'de
  asistan bu yüzden 9 dk kapalı kaldı). Yalnız `candan-worker` restart edilecek.
* TTS cache: eşleme değişti, eski PCM'ler bayat.
  `rm -f /opt/candan-lite/worker/data/tts-cache/*.pcm` gerekiyorsa yap ve raporda söyle.
* Kullanıcının gerçek verisine (`memory/`, `policy.json`, `speakers.db`) DOKUNMA.

### 4. Belgeleme

* `handoff/2026-07-27-duygu-katmani.md`'ye kısa bir bölüm: kulak testi sonucu,
  seçilen token, elenen kombo ve nedeni, `surprise_set.py`/`surprise.html` yolu.
* `handoff/2026-07-27-DEVIR.md` §3 ve §5'i güncelle: şaşırma maddesi çözüldü,
  "duygu demosunu dinle" adımı tamamlandı. §5'te kalan sıradaki adımlar (kalabalık
  ortam kararı, SGLang-Omni, compaction) aynen kalsın.
* Geri dönüşü **TEK BLOK** yaz (kullanıcı telefondan çalıştırabilmeli).
* Commit at (tek commit, açıklayıcı Türkçe mesaj). Push ETME.

## Sınırlar

* Ölçülmemiş token canlıya girmez — kombo canlıya ALINMAYACAK.
* Görsel/işitsel test senin işin değil; dinlemeyi kullanıcı yaptı, karar verildi.
* Deploy dışında sunucuda başka hiçbir şeye dokunma.

## Rapor (KISA)

* Değişen satırlar, test sonucu (sayı)
* Deploy adımlarının her birinin sonucu (md5 eşleşti mi, log temiz mi)
* Tek blok geri dönüş komutu
* Commit hash
