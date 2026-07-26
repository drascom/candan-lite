# DEPLOY — doğruluk denetimi (modelin ANLATTIĞI vs araçların YAPTIĞI)

**Hedef:** `root@192.168.0.25:/opt/candan-lite`, servis `candan-worker.service`
**Tarih:** 2026-07-27
**Not:** Bu adımları KULLANICI çalıştırır. Ajan sunucuya YAZMADI.

Ne değişti (ilke: *model NE YAPILACAĞINA karar verir, harness NE OLDUĞUNU söyler*):

- **YENİ** `worker/truth_check.py` — üç katman (tur defteri / kritik yazma sonucunu
  harness söyler / gated küçük LLM yargıcı).
- `worker/pi_brain.py` — tur döngüsüne bağlandı: `toolResult` defteri, kritik yazma
  hatasında modelin delta'ları bastırılıyor, tur sonunda deterministik cümle.
- `pi/AGENTS.md` — "tool'un DÖNÜŞÜ tek gerçektir" kuralına "bu kural DENETLENİYOR"
  bölümü eklendi (silinen yok, tamamlandı). **pi süreçleri yeniden doğmadan
  yürürlüğe girmez** → servis restart şart.
- `worker/.env.example` — Katman 3 ayarları belgelendi. `.env`'e ekleme ZORUNLU
  DEĞİL (varsayılanlar kodda; uç zaten `.25:8082`).

Yeni bağımlılık YOK (`urllib` + `asyncio`, stdlib). GPU'ya ek yük YOK: Katman 1-2
deterministik, Katman 3 yalnız turda hata dönen araç varken çağrılır.

---

## 1) Yedek al

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite \
  && cp -a worker/pi_brain.py worker/pi_brain.py.bak-20260727 \
  && cp -a pi/AGENTS.md pi/AGENTS.md.bak-20260727 \
  && ls -la worker/pi_brain.py.bak-20260727 pi/AGENTS.md.bak-20260727'
```

## 2) Dosyaları gönder (Mac'ten)

```bash
cd /Users/drascom/Documents/work/candan-lite
rsync -av worker/truth_check.py worker/pi_brain.py worker/.env.example \
  root@192.168.0.25:/opt/candan-lite/worker/
rsync -av pi/AGENTS.md root@192.168.0.25:/opt/candan-lite/pi/
```

## 3) Import + testleri sunucuda doğrula (servis DURMADAN)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && ./.venv/bin/python -c "import truth_check, pi_brain; print(\"import ok\")" \
  && ./.venv/bin/python -m unittest discover -s tests 2>&1 | tail -3'
```

Beklenen: `OK` (Mac'te 162 test). Sunucuda test dizini yoksa bu adım atlanabilir —
import satırı yine de koşmalı.

## 4) Sınıflandırıcı ucu ayakta mı (salt-okuma, ~120 ms)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && ./.venv/bin/python -c "
import asyncio, time, truth_check as t
a=time.monotonic(); print(asyncio.run(t.claims_success(\"Notumu aldım.\")), round((time.monotonic()-a)*1000), \"ms\")"'
```

Beklenen: `True  ~90-130 ms`. **Uç kapalıysa (None) DEPLOY YİNE GEÇERLİDİR** —
Katman 1-2 deterministik ve bağımsız çalışır.

## 5) Servisi yeniden başlat

```bash
ssh root@192.168.0.25 'systemctl restart candan-worker.service && sleep 3 \
  && systemctl is-active candan-worker.service'
```

`pi_broker.service` kullanılıyorsa AGENTS.md değişikliğinin yürürlüğe girmesi için
sıcak pi süreçleri de tazelenmeli:

```bash
ssh root@192.168.0.25 'systemctl restart pi-broker.service 2>/dev/null; \
  systemctl restart candan-worker.service; sleep 3; \
  systemctl is-active candan-worker.service'
```

## 6) Canlı izleme (kullanıcı sesle test ederken)

```bash
ssh root@192.168.0.25 'journalctl -u candan-worker.service -f | grep -E "truth:|pi_brain"'
```

Aranan satırlar:
- `truth: model anlatısı BASTIRILDI (memory_add=hata) → '…'` — Katman 2 devreye girdi.
- `truth: harness düzeltmesi (…) → …` — kullanıcıya giden deterministik cümle.
- `iddia sınıflandırıcı atlandı (…)` — Katman 3 best-effort atlaması (zararsız).

## 7) Geri alma

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite \
  && mv worker/pi_brain.py.bak-20260727 worker/pi_brain.py \
  && mv pi/AGENTS.md.bak-20260727 pi/AGENTS.md \
  && rm -f worker/truth_check.py \
  && systemctl restart candan-worker.service'
```

Sadece Katman 3'ü kapatmak (deterministik katmanlar kalsın):

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && echo "CLAIM_CHECK_ENABLED=false" >> .env \
  && systemctl restart candan-worker.service'
```

---

## Canlı test senaryoları (kullanıcı yapar — ajan uygulama çalıştırmadı)

1. **Guest ile not**: tanınmayan/guest bir sesle "şunu not al" → `memory_add`
   "kaydedilmedi" döner. Beklenen ses: **"Kaydedemedim, seni henüz tanımıyorum."**
   ve modelin "notunu aldım" cümlesi DUYULMAMALI.
2. **Normal not**: tanınan kişiyle "şunu not al" → tek onay duyulmalı, harness
   ARAYA GİRMEMELİ (çift onay = hata).
3. **Tool'suz iddia**: model tool çağırmadan "aklımda tutacağım" derse arkasından
   **"Aslında bunu kaydetmedim, kusura bakma."** duyulmalı.
4. **Mod**: "geliştirme moduna geç" → sonraki turda "normal moddayım" derse
   **"Aslında hâlâ geliştirme modundayım."** duyulmalı.
