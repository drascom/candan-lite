# Görev — GECİKME ve TUR DÜŞMESİ: stall eşiği, önbellek ön eki, compaction

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

## Ölçülen kanıt (27 Tem 22:05-22:25, kullanıcının uzun testi)

46 tur konuşuldu. Kullanıcının gözlemi: *"iki senaryo çok güzel çalıştı, heyecanlı
kız senaryosu önce başarılı ikinci denemede başarısız oldu, model cevap süreleri çok
uzadı, sanırım kontekst oldu."* Doğru teşhis etmiş. Log'lar:

```
22:09:56  pi tur sonu compaction (reason=threshold) → sıkıştırma ARKA PLANDA
22:10:30  pi tur stall: 12s ilerleme yok → tur kapatılıyor (got_delta=False)
22:11:22  pi tur stall: 12s ...        (toplam 7 stall, 22:10-22:21 arası)
22:21:15  pi tur sonu compaction (reason=threshold)
```

`candan-brain` (llama-server) aynı aralıkta:
```
prompt eval time = 14745.35 ms / 17000 tokens     ← bağlam BAŞTAN işleniyor
eval time        =  8543.23 ms /   619 tokens
total time       = 23288.58 ms
```

Aynı oturumdaki diğer turlar ucuz: `181 ms / 101 token`, `880 ms / 717 token`,
`1194 ms / 879 token`. Yani KV önbelleği ÇOĞU turda çalışıyor. Patladığı turlar:
`4479 ms / 4135`, `8408 ms / 8520`, `14745 ms / 17000`.

Sebep de log'da:
```
slot get_availabl: selected slot by LCP similarity, sim_best = 0.861
slot get_availabl: selected slot by LCP similarity, sim_best = 0.529
slot get_availabl: selected slot by LCP similarity, sim_best = 0.382
```
Ortak ön ek bozulunca (0.38) bağlamın tamamı yeniden işleniyor.

`candan-brain` zaten `--cache-reuse` ve `--cache-ram` ile başlatılıyor; sorun sunucu
ayarı değil, **gönderdiğimiz prompt'un ön ekinin turdan tura değişmesi**.

## Üç iş — sırayla

### 1. Stall watchdog meşru turları öldürüyor (EN ACİL)

12 s ilerleme yoksa tur kapatılıyor. Ama prompt eval TEK BAŞINA 14.7 s sürüyor —
beyin çalışıyorken, daha ilk token'ı üretemeden turu öldürüyoruz. `got_delta=False`
bunu söylüyor: delta yok çünkü model hâlâ prompt'u okuyor.

* Watchdog **prefill'e duyarlı** olsun: model prompt işlerken geçen süre "ilerleme
  yok" sayılmamalı. llama-server bu bilgiyi veriyor mu (slot durumu / `n_past` /
  ilk delta öncesi sinyal) — ÖNCE ona bak. Veremiyorsa eşiği bağlam boyutuna göre
  ölçekle, sabit 12 s bırakma.
* Eşiği kaldırma ya da sonsuz yapma: gerçek takılma da oluyor, watchdog'un varlık
  sebebi o. Amaç meşru prefill'i takılmadan AYIRT ETMEK.
* Stall olduğunda kullanıcıya ne oluyor bak: sessizlik mi, yarım cevap mı? Sessizse
  harness deterministik bir cümle söylemeli ("Biraz düşünmem gerekti, tekrar sorar
  mısın?") — sessizce yutmak en kötüsü.

### 2. Önbellek ön ekini ne bozuyor (EN BÜYÜK KAZANÇ)

Prompt'un BAŞINDA turdan tura değişen ne varsa, ondan sonraki her token'ın önbelleği
çöpe gidiyor. Aday şüpheliler (kod okunarak DOĞRULANACAK, tahmin edilmeyecek):

* `_identity_note()` — kimlik notu (bugün eklenen aday/onay bilgisi dahil)
* `MEMORY_NOTE` — ortak odada boot'ta veriliyor
* konuşmacı bağlamı (`user-transcript context aktif (speaker=True)`)
* saat/tarih gibi her turda değişen alanlar
* compaction'ın geçmişi yeniden yazması

**Ölç, tahmin etme:** art arda iki turun gönderilen prompt'unu karşılaştır, ilk
farklılık hangi karakterde başlıyor — asıl sayı bu. Ön ekteki değişkenler prompt'un
**SONUNA** taşınabiliyorsa taşı; taşınamıyorsa neden taşınamadığını yaz.

Düzeltme sonrası aynı ölçüm: `sim_best` ve `prompt eval token` sayısı düşmeli.
Öncesi/sonrası sayı vermeden "düzeltildi" deme.

### 3. Bağlam 17 bin token'a çıkıyor

Compaction `reason=threshold` ile tetikleniyor ve **arka planda** çalışıyor
(bilerek — sessizlik sorunu böyle çözülmüştü). Ama:

* Sıkıştırma bittiğinde bağlam gerçekten küçülüyor mu? Öncesi/sonrası token sayısı ölç.
* Eşik doğru yerde mi? 17 bin token'da prompt eval 14.7 s ise, eşik çok geç kalıyor
  olabilir.
* Sıkıştırma sürerken gelen turlar ne oluyor (DEVİR §4 madde 2: "bekleme çözülmedi").
  7 stall'ın kaçı compaction penceresine denk geliyor — say.

⚠️ Compaction'ı senkron hale getirme; o sessizlik hatasını geri getirir.

## Sınırlar

* **Ölç, tahmin etme.** Bu iki günün altı yanlış teşhisi ölçümle düzeltildi.
* `candan-brain`'i (llama-server) YENİDEN BAŞLATMA — kullanıcı canlıda kullanıyor,
  model yükleme uzun sürer. Ayarını değiştirmen gerekiyorsa ÖNCE SOR.
* `higgs-tts`'e ve `pi-service`'e DOKUNMA. Deploy'da yalnız `candan-worker` restart.
* Kullanıcının gerçek oturum dosyalarına (`sessions/*.jsonl`) yazma; okumak serbest.
* Şu an 367 test geçiyor.

## Belgeleme

`handoff/2026-07-27-gecikme-ve-stall.md`: ölçüm tabloları (öncesi/sonrası),
üç maddenin her birinde ne yapıldı/yapılmadı, tek blok geri dönüş.
DEVİR §4 madde 2 (compaction beklemesi) bu bulguyla güncellensin — başka ajanların
maddelerini SİLME. Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Stall: neye göre ayırt ediliyor artık, meşru prefill kaç saniyeye kadar korunuyor
* Ön ek: ilk fark nerede başlıyordu, ne taşındı, `sim_best` ve prompt eval öncesi/sonrası
* Compaction: bağlam gerçekten küçülüyor mu (sayı), eşik değişti mi
* Test sayısı, deploy sonucu, tek blok geri dönüş, commit hash
