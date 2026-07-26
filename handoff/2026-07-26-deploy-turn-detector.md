# DEPLOY — semantik tur-sonu (EOU) modeli

**Hedef:** `root@192.168.0.25:/opt/candan-lite`, servis `candan-worker.service`
**Tarih:** 2026-07-26
**Not:** Bu adımları KULLANICI çalıştırır. Ajan sunucuya yazmadı.

Ne değişti: `worker/agent.py` artık `turn_detection=MultilingualModel()` veriyor.
Öncesinde bu alan HİÇ verilmiyordu → framework varsayılanı LiveKit **Cloud** EOT'ydi →
`401 Unauthorized` → zayıf yerel "mini" modele düşüş → cümle ortasındaki nefes turu bölüyordu.
Yeni model YEREL ve **CPU**'da koşar (GPU'ya dokunmaz). `endpointing_delay` ELLE değiştirilmedi.

---

## 1) Yedek al

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && cp -a agent.py agent.py.bak-20260726 \
  && cp -a requirements.txt requirements.txt.bak-20260726 \
  && ./.venv/bin/pip list | grep -i -E "livekit|transformers|onnxruntime"'
```

Çıktıdaki `livekit-agents` sürümünü not al — **1.6.5** bekleniyor.

## 2) Dosyaları gönder (Mac'ten)

```bash
cd /Users/drascom/Documents/work/candan-lite
rsync -av worker/agent.py worker/requirements.txt \
  root@192.168.0.25:/opt/candan-lite/worker/
```

## 3) Paketi kur

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && ./.venv/bin/pip install "livekit-plugins-turn-detector==1.6.5" \
  && ./.venv/bin/pip list | grep -i -E "livekit-agents|turn-detector|transformers"'
```

⚠️ Sürüm **sabit 1.6.5**. `livekit-plugins-turn-detector` her 1.6.x sürümü
`livekit-agents>=aynı sürüm` ister; serbest bıraksaydık pip `livekit-agents`'ı da
yükseltirdi. Kurulum sonrası `livekit-agents` hâlâ **1.6.5** olmalı — değiştiyse DUR.

Yan bağımlılıklar (otomatik gelir): `transformers`, `tokenizers`, `huggingface-hub`,
`jinja2`, `safetensors`, `regex`. **PyTorch GELMEZ** — çıkarım saf onnxruntime.

## 4) Model ağırlıklarını indir (~460 MB)

Ağırlıklar pip'le gelmez, HF cache'ine iner ve runtime'da `local_files_only=True` ile
okunur → **indirilmezse model yüklenmez**.

```bash
# Servis hangi kullanıcı ile koşuyor? HF cache KULLANICIYA ÖZELDİR.
ssh root@192.168.0.25 'systemctl show -p User candan-worker.service'

# root ise:
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && ./.venv/bin/python -m livekit.agents download-files'

ssh root@192.168.0.25 'du -sh ~/.cache/huggingface/hub/models--livekit--turn-detector'
```

`User=` root DEĞİLSE komutu o kullanıcıyla koştur (`sudo -u <kullanıcı> ...`), yoksa model
servis tarafından bulunamaz.

`du` çıktısı **~460 MB** olmalı. İki revizyon birden iner (`v0.4.1-intl` + `v1.2.2-en`) —
bu beklenen: `livekit.plugins.turn_detector` paketi import edilince İKİ runner da kaydolur.

## 5) Derleme kontrolü + restart

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && ./.venv/bin/python -m compileall -q . && echo "compileall OK"'

ssh root@192.168.0.25 'systemctl restart candan-worker.service'
ssh root@192.168.0.25 'sleep 5; systemctl is-active candan-worker.service; \
  journalctl -u candan-worker.service -n 60 --no-pager'
```

## 6) Log'da NE ARANACAK

| Görülmeli | Görülmemeli |
|---|---|
| traceback yok, `registered worker` | `cloud turn detector failed ... 401 Unauthorized` |
| — | `falling back to local mini model` |
| — | `EOU turn-detector yüklenemedi → VAD tabanlı tur tespitine düşülüyor` |

Son satır çıkarsa **adım 4 eksik/yanlış kullanıcıyla yapılmış** demektir. Worker yine çalışır
(eski davranışa döner) ama düzeltme bu deploy'un amacıydı.

GPU'nun boş kaldığını teyit (konuşma sırasında):

```bash
ssh root@192.168.0.25 'nvidia-smi --query-compute-apps=pid,used_memory,name --format=csv'
```

Listede yeni bir python süreci ÇIKMAMALI (model CPU'da).

---

## GERİ DÖNÜŞ

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker \
  && mv agent.py.bak-20260726 agent.py \
  && mv requirements.txt.bak-20260726 requirements.txt \
  && ./.venv/bin/pip uninstall -y livekit-plugins-turn-detector \
  && systemctl restart candan-worker.service'
```

(Model dosyalarını silmeye gerek yok; `rm -rf ~/.cache/huggingface/hub/models--livekit--turn-detector`
ile 460 MB geri alınır.)

---

## KULLANICININ KONUŞARAK TEYİT EDECEĞİ TEK ŞEY

Bir cümlenin **ORTASINDA 1-2 saniye durakla** (nefes al), sonra devam et.
Candan araya girmemeli; cümlenin tamamını TEK tur olarak almalı.

Yan etkiye dikkat: bu detektörle framework'ün endpointing varsayılanı
0.3/2.5 sn yerine **0.5/3.0 sn**'ye çıkıyor (elle ayarlanmadı, tipe bağlı otomatik).
Yani gerçekten bitirdiğin cümlelerde yanıt ~0.2 sn daha geç başlayabilir.
Bu his rahatsız ediciyse ayrı bir turda `endpointing` ayarlanır.
