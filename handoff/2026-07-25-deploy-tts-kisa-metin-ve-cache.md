# DEPLOY — trnorm + kısa-metin guard + kalıp cümle ses cache'i

**Hedef:** `root@192.168.0.25:/opt/candan-lite`, servis `candan-worker.service`
**Tarih:** 2026-07-25
**Not:** Bu adımları KULLANICI çalıştırır. Ajan sunucuya yazmaz.

Bu deploy iki şeyi birden götürüyor:
- **1.A trnorm** (`handoff/2026-07-25-tts-arastirma-ve-server-adimlari.md` §1.A) — onaylıydı, hiç deploy edilmedi.
- **Kısa-metin guard + ses cache'i** — bugün yazıldı, lokalde birim test + sahte sunucuyla doğrulandı,
  gerçek konuşmayla HİÇ denenmedi (lokal beyin `pi` CLI eksik olduğu için çalışmıyor).

`.25`'teki OmniVoice'a, `bridge_server.py`'a, `default-ref.wav`'a DOKUNULMUYOR.
Referans kısaltma (§1.B) bu deploy'un parçası DEĞİL.

---

## 0) Deploy öncesi salt-okuma kontrolü — `pronounce_tr.json` çakışması

trnorm artık `yüzde`, `bin`, `yüz`, `on`, `otuz`, `lira`, `virgül`, ay adları gibi kelimeler
üretiyor. Eğer `pronounce_tr.json` bunlarla zayıf kelime-sınırıyla eşleşen bir giriş içeriyorsa
çift uygulama olur ("yüzde yüzde"). `handoff/2026-07-16.md`'de tam bu tür bir sınır hatası kayıtlı.

```bash
ssh root@192.168.0.25 'cat /opt/omnivoice/pronounce_tr.json'
```

Bak: (a) sayı/yüzde ile ilgili giriş var mı — trnorm'dan sonra gereksiz;
(b) `on`, `bin`, `yüz` gibi KISA token'lar var mı. Varsa deploy'dan önce bana söyle.

---

## 1) Yedek al

`omnivoice_tts.py` ve `go.sh` değişiyor; `trnorm.py` ile `tts_cache.py` YENİ (yedek gerekmez,
ama üzerine yazmadığımızı doğrula).

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && cp -a omnivoice_tts.py omnivoice_tts.py.bak-20260725 \
  && cp -a go.sh go.sh.bak-20260725 \
  && ls -la trnorm.py tts_cache.py 2>&1'
```

Son `ls` "No such file" derse beklenen durum. Dosya ÇIKARSA dur, önce bana söyle.

---

## 2) Dosyaları gönder (Mac'ten)

```bash
cd /Users/drascom/Documents/work/candan-lite

rsync -av \
  worker/trnorm.py \
  worker/tts_cache.py \
  worker/omnivoice_tts.py \
  worker/go.sh \
  root@192.168.0.25:/opt/candan-lite/worker/

rsync -av \
  worker/tests/test_tts_cache.py \
  worker/tests/test_tts_short_guard.py \
  worker/tests/test_go_readiness.sh \
  root@192.168.0.25:/opt/candan-lite/worker/tests/
```

Yeni pip bağımlılığı YOK — `tts_cache.py` stdlib + zaten kullanılan `aiohttp` ile çalışıyor.
venv'e dokunmaya gerek yok.

---

## 3) Sunucuda testleri koştur (restart'tan ÖNCE)

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && ./.venv/bin/python -m compileall -q . && echo "compileall OK" \
  && ./.venv/bin/python trnorm.py --selftest'
```

`trnorm --selftest` → **26/26** beklenir. Geçmezse restart ETME.

---

## 4) Servisi yeniden başlat

```bash
ssh root@192.168.0.25 'systemctl restart candan-worker.service'
ssh root@192.168.0.25 'sleep 3; systemctl is-active candan-worker.service; \
  journalctl -u candan-worker.service -n 40 --no-pager'
```

`active` görmelisin ve log'da traceback OLMAMALI.

---

## 5) Doğrula — konuşarak

Web arayüzünden veya pi'den konuş. Bakılacaklar:

1. **Kısa yanıtlar** — "Anladın mı?", "Tamam mı?" gibi tek kelimelik cevap aldıracak sorular sor.
   Eskiden bu sınıfta ~%27 boş çıktı oluyordu; artık sessizlik OLMAMALI.
2. **Cache** — aynı kalıp cümleyi ikinci kez tetikle, ikincisi anında gelmeli:
   ```bash
   ssh root@192.168.0.25 'ls -la /opt/candan-lite/worker/data/tts-cache/ | head'
   ```
   Dosyalar birikiyorsa cache çalışıyor. **Dizin hiç oluşmadıysa** cache devre dışı kalmış
   demektir — sebebi `GET /api/default` okunamamasıdır, log'da
   `pinned referans okunamadı ... → ses cache'i devre dışı` satırını ara.
3. **Sayı/tarih telaffuzu** (trnorm) — "%25", "3.500 lira", "14:30'da" içeren cümleler söylet.

Kulakla teyit: tek kelimeliklere eklenen noktanın sonda garip bir duraklama yaratmadığı,
ve cache'ten gelen sesin canlı üretilenle aynı tonda olduğu.

---

## GERİ DÖNÜŞ

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && mv omnivoice_tts.py.bak-20260725 omnivoice_tts.py \
  && mv go.sh.bak-20260725 go.sh \
  && rm -f trnorm.py tts_cache.py \
  && rm -rf data/tts-cache \
  && systemctl restart candan-worker.service'
```

`trnorm.py`/`tts_cache.py` silinince `omnivoice_tts.py`'ın eski sürümü onları zaten
import etmiyor — temiz döner.

---

## Bilinen risk

Bu kod gerçek bir konuşmada hiç çalışmadı. Doğrulama lokal birim testleri (44 test) ve
sahte OmniVoice sunucusuna karşı 18 uçtan uca kontrolle sınırlı. İlk canlı turda log'u
izle; sorun çıkarsa yukarıdaki geri dönüş tek komut.
