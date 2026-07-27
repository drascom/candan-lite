# SÖZÜNÜ KESME — yeni komut mu, sadece sohbet mi? (27 Temmuz 2026)

Kod: `worker/barge.py` (yeni) · `worker/pi_brain.py` (tur akışı) · `worker/agent.py` (kanca)
Test: `worker/tests/test_barge_resume.py` — **339 test geçiyor** (301 → +38)
Bayrak: `BARGE_RESUME_ENABLED` (varsayılan **açık**) · geri dönüş §6, tek satır.

✅ **CANLIDA — 27 Tem 21:16.** Yedekler `*.bak-kesme-20260727`, md5 **3/3** eşleşti,
traceback 0, worker `registered`, **yalnız `candan-worker` restart** (`higgs-tts`
13:45:56 ve `pi-service` 14:43:43 başlangıç damgaları DEĞİŞMEDİ — dokunulmadı).

---

## 0. Neden

Canlı vaka, 27 Tem 18:37. Candan duygu örneklerini sayarken kullanıcı araya girdi:

> **Ayhan:** Beni duydun mu?
> **Candan:** *(cevap "Harika bir haber"de kesildi)* … Evet, seni duydum.
> **Az önceki örnekleri dinledin sanırım.**

İki ayrı kusur:

1. **Kesilen cevap ölüyordu.** Kullanıcı sadece "hı hı" dese de, yeni bir soru sorsa da
   aynı muamele: kalan metin çöpe.
2. **Model kesilen metnin DUYULDUĞUNU sandı.** Kullanıcı hiçbir örneği duymamıştı ve
   hepsini baştan istemek zorunda kaldı. Bu, `truth_check`'in düzelttiği yalanın
   kardeşi: model TAHMİN ediyor, oysa harness GERÇEĞİ biliyor.

## 1. İki tasarım kararı (kullanıcının, tartışılmadı)

| # | Karar | Gerekçe |
|---|---|---|
| 1 | Sohbet sayılırsa **kaldığı CÜMLENİN BAŞINDAN** devam | Yarım cümleden sürmek kopuk duyulur. Küçük tekrar > kopukluk; insan da böyle yapar. Metin **yeniden ÜRETİLMEZ** — yeniden üretim gecikme ekler ve içeriği değiştirir. |
| 2 | **Şüphede YENİ KOMUT say, sussun** | Yanlış kararın telafisi kolay: "devam et" demek yeter. Tersinde kullanıcının sözünü İKİNCİ kez kesmesi gerekir. |

Elenen seçenekler: kesildiği HECEDEN devam + kalanı özetletme (kullanıcı eledi).

## 2. Sınıflandırma sırası — ucuzdan pahalıya

`truth_check`'in MALİYET KAPISI deseninin aynısı. Kesme anı gecikmeye duyarlı.

```
0. Kesme YOKSA hiçbir şey çalışmaz          ← tipik turda barge.py'den tek satır bile yürümez
1. Geri-bildirim sözlüğü (tam metin)         "hı hı" "tamam" "aynen" "haha" "Candan?"   → SOHBET
2. Yeni-istek işaretleri                     "?" · soru eki/zamiri · emir kipi fiilleri → YENİ
3. Çok kısa ifade (≤2 kelime, 2'ye takılmadı)                                          → SOHBET
4. Kalan belirsiz durum → küçük LLM (truth_check katman-3'ün AYNI ucu, AYNI taşıma)
5. Sınıflandırıcı kararsız / erişilemez / yavaş                                        → YENİ
```

**Yeni model/mekanizma GETİRİLMEDİ.** Uç ve model `CLAIM_CHECK_URL`/`CLAIM_CHECK_MODEL`
ile ortak; yalnız prompt farklı ("YENI mi TEPKI mi").

⚠️ Sıra önemli: sözlük **soru işaretinden ÖNCE** bakılır. "Candan?" bir isim
seslenişidir, istek değil — kullanıcının kendi örneği.

### Ölçülen gecikme (yerel, 2000 koşum)

| | medyan | en yavaş |
|---|---|---|
| `classify_fast` (deterministik kapı) | **2.5 µs** | 65 µs |
| `resume_from` (devam metnini kırp) | **60 µs** | 82 µs |
| 12 ifadelik korpusun tamamı | 35 µs | — |

Yani **kesme → devam gecikmesi pratikte sıfır**: karar + metin hazırlığı toplam
~0.06 ms, ardından normal TTS ilk-ses süresi (Higgs streaming ~0.55 s) işler.
Sohbet kararında **pi'ya HİÇ gidilmez** → 0 token, 0 prefill.

**Sınıflandırıcı tipik turda çağrılmıyor — testle kanıtlı:**
`test_typical_turn_never_touches_the_classifier` (kesme yoksa `classify_fast` bile
çağrılmıyor) ve `test_deterministic_gate_never_calls_the_classifier` (12 tipik
kesme ifadesi, 0 HTTP isteği).

## 3. Devam mekaniği

- **"Ne söylendi" TAHMİN EDİLMEZ.** livekit `conversation_item_added` olayını
  ses/transkript senkronizasyonundan gelen `forwarded_text` ile yayar ve
  `interrupted` bayrağını verir (`voice/generation.py::_ForwardOutput`). Kanca
  `worker/agent.py`'de; harness gerçeği oradan okur.
- **Hizalama SAYAR, eşleştirmez.** Odaya giden transkript `[mood:x]` gibi işaretleri
  temizlemiş olabilir. İki metin de "seslendirilen harf sayısı" ölçüsüne indirilip
  o kadarı ham metin üzerinde tüketilir → biçim farkları kesme noktasını kaydırmaz.
- **Kesilen cümle neredeyse bitmişse tekrar edilmez.** Tek eşik: `%85`
  (`BARGE_RESUME_NEAR_END_RATIO`). **Ölçüldü/denendi ve karar:** ayrı bir "cümle çok
  kısaysa" kuralı EKLENMEDİ — kısa cümlede oran kuralı zaten devreye giriyor (2-3
  kelimelik cümlenin yarısı duyulmuşsa oran yüksek çıkar), iki kural aynı işi yapardı.
  Kesilen cümlenin başı duyulmuşsa tekrar KISA ve doğaldır. `1.0` yaparsan cümle
  her zaman baştan söylenir.
- **Ton korunur:** cevabın başındaki `[mood:X]` devam metninin önüne geri konur
  (yeni turda `reset_mood` çalıştığı için aksi hâlde renk kaybolurdu). `[speed:X]`
  BİLEREK taşınmaz — hız kalıcı ayardır, ikinci kez uygulanmamalı.
- **İkinci kesme:** devam metni de normal bir cevap gibi deftere yazılır → aynı
  kurallar yeniden işler (testte kilitli).
- **Ömür:** bekleyen devam `BARGE_RESUME_TTL` (30 sn) sonra düşer; kullanıcı konuyu
  değiştirdiyse eski cevabın yarısı sonradan ortaya çıkmaz.
- **Bayat devam sızmaz:** pending, tur başında HER YOLDA düşürülür. Kimlik/kayıt/
  sıfırlama gibi scripted yollar turu erken kapatsa bile sonraki tura sarkmaz.
- **Öncelik:** kimlik onayı, kayıt sihirbazı, rol komutu, sohbet sıfırlama her zaman
  devam mekaniğinden ÖNCE gelir.

## 4. Model yanlış varsaymasın (geçmiş dürüstlüğü)

Yeni-istek kararında kalan metin atılır — ama pi'nın geçmişinde cevabın TAMAMI durur.
O yüzden bir sonraki prompt'a tek deterministik satır iliştirilir:

```
(Sistem — GERÇEK: bir önceki cevabın sözü kesildi. Kullanıcı YALNIZCA şu kadarını
duydu: "…Harika bir haber" — gerisi SÖYLENMEDİ. Duyulduğunu varsayma, gerekiyorsa
kısaca özetle ya da kaldığın yerden anlat.)
```

Duyulan kısım son 200 karakterle sınırlı (prefill yakmasın). Sohbet kararında bu not
EKLENMEZ: kalan gerçekten söyleneceği için geçmiş zaten doğrudur.
`truth_check` ilkesinin aynısı: **harness NE OLDUĞUNU bilir, model tahmin etmez.**

## 4b. ⚠️ DEPLOY SIRASINDA BULUNAN TUZAK — geri dönüş kolu çalışmıyordu

`candan-worker.service` `.env`'i **`EnvironmentFile=` ile YÜKLEMEZ** (unit'te yazılı:
boşluklu değerler systemd ayrıştırıcısını kırıyor) — `agent.py` kendi `load_dotenv()`
çağrısını yapar ve o çağrı **import blokundan SONRA**dır. Modül seviyesinde
`os.environ` okuyan bir dosya import anında `.env`'i GÖREMEZ.

**Sunucuda ölçüldü (21:20), restart'tan önce:**
```
import aninda RESUME_ENABLED : True
.env okundu, environ         : false
modul degeri                 : True    ← BARGE_RESUME_ENABLED=false ETKİSİZ
```
Yani §6'daki tek satırlık geri dönüş **hiçbir şey yapmayacaktı.**

**Düzeltme (kapsam DAR):** `barge.reload_settings()` eklendi, `agent.py`
`load_dotenv()`'den hemen sonra çağırıyor. `load_dotenv`'i import'ların önüne almak
TÜM modüllerin (`pi_brain`, `truth_check`, `higgs_tts`…) bugünkü davranışını
değiştirirdi — bilerek yapılmadı. Ayrıca `resume_from(near_end_ratio=…)` ve
`Pending.expired(ttl=…)` varsayılan argümanları `None`'a çevrildi: varsayılanlar
TANIM anında bağlanır, env'den gelen değer hiç kullanılmazdı.
Regresyon kilidi: `EnvReloadTest.test_env_is_picked_up_after_reload`.

⚠️ **Ders:** bu tuzak `truth_check`'in `CLAIM_CHECK_*` ayarları için de geçerli
(aynı desen, ana süreçte `.env` görülmüyor). Bu turda DOKUNULMADI — ayrı iş.

## 5. Deploy — YAPILDI (21:16). Komutlar (tekrarı için)

⚠️ Yalnız `candan-worker` restart. **`pi-service`'e ve `higgs-tts`'e DOKUNMA.**
(`candan-worker`, `pi-service`'e `Requires=` ile bağlı — bkz. DEVİR §1.)

```bash
cd /Users/drascom/Documents/work/candan-lite

# 1) yedek
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/pi_brain.py worker/pi_brain.py.bak-kesme-20260727 && \
  cp worker/agent.py    worker/agent.py.bak-kesme-20260727'

# 2) gönder
scp worker/barge.py worker/pi_brain.py worker/agent.py \
    root@192.168.0.25:/opt/candan-lite/worker/
scp worker/tests/test_barge_resume.py root@192.168.0.25:/opt/candan-lite/worker/tests/

# 3) doğrula (md5 3/3 eşleşmeli)
md5 -q worker/barge.py worker/pi_brain.py worker/agent.py
ssh root@192.168.0.25 'md5sum /opt/candan-lite/worker/{barge.py,pi_brain.py,agent.py}'

# 4) SADECE worker restart
ssh root@192.168.0.25 'systemctl restart candan-worker && sleep 4 && \
  systemctl is-active candan-worker higgs-tts pi-service'

# 5) log temiz mi (traceback OLMAMALI)
ssh root@192.168.0.25 'journalctl -u candan-worker --since "-2 min" --no-pager | \
  grep -Ei "traceback|error|registered worker" | tail -20'
```

Canlıda izlenecek satırlar:
```
kesme: cevap N/M harf duyuldu → devam bekliyor
kesme: sohbet sayıldı (...) → kaldığı cümlenin başından devam (N harf)
kesme: isim seslenişi → devam (N harf)
kesme: devam süresi doldu (30s) → kalan atıldı
```

## 6. GERİ DÖNÜŞ (tek blok)

```bash
# Özelliği KAPAT — davranış bugünküyle BİRE BİR aynı (kod kalır)
ssh root@192.168.0.25 'echo "BARGE_RESUME_ENABLED=false" >> /opt/candan-lite/worker/.env && \
  systemctl restart candan-worker'

# Yalnız LLM'e sormayı kapat (deterministik kapı çalışmaya devam eder,
# belirsiz kesme hep "yeni istek" sayılır)
ssh root@192.168.0.25 'echo "BARGE_CHECK_ENABLED=false" >> /opt/candan-lite/worker/.env && \
  systemctl restart candan-worker'

# Kesilen cümle HER ZAMAN baştan söylensin (tekrar etmeme eşiğini kaldır)
ssh root@192.168.0.25 'echo "BARGE_RESUME_NEAR_END_RATIO=1.0" >> /opt/candan-lite/worker/.env && \
  systemctl restart candan-worker'

# Dosyaları tamamen geri al
ssh root@192.168.0.25 'cd /opt/candan-lite && \
  cp worker/pi_brain.py.bak-kesme-20260727 worker/pi_brain.py && \
  cp worker/agent.py.bak-kesme-20260727    worker/agent.py && \
  rm -f worker/barge.py && systemctl restart candan-worker'
```

## 7. Kullanıcının canlıda deneyeceği

1. Candan uzun bir şey anlatırken **"hı hı"** de → cümlenin başından devam etmeli,
   kopukluk olmamalı.
2. Aynı yerde **"Beni duydun mu?"** de → susmalı, kalan gelmemeli, ve **"az önce
   anlattıklarını duydun sanırım" DEMEMELİ** (kanıt bu).
3. Kes, sonra **30 saniyeden fazla başka bir konu konuş** → eski cevabın yarısı
   sonradan ortaya ÇIKMAMALI.
4. Devam ederken **tekrar kes** ("evet") → devamın devamı gelmeli.
5. Kes ve sadece **"Candan?"** de → isim seslenişi, devam etmeli.

## 8. Açık kalan / dikkat

- Sözlük Türkçe geri-bildirim sözlerine göre elle kuruldu. Canlıda kaçan olursa
  `journalctl -u candan-worker | grep "kesme sınıfı"` ile hangi ifadelerin LLM'e
  gittiğine bak; sık geçenler sözlüğe eklenir (bedava kazanç).
- `%85` eşiği **kulakla doğrulanmadı** — kullanıcı "tekrar fazla/az" derse tek env
  satırıyla oynatılır.
- Uyurken (wake gate kapalı) devam YOK, proaktif hatırlatma sürerken de YOK. Bilerek.
