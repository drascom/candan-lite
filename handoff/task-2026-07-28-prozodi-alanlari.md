# Görev — Gölge kaydediciye prozodi + akış alanlarını ekle

Sen bir worker'sın. İşi kendin yap, delege etme. Panel açma.

Araştırma bitti: `handoff/arastirma-F-akis-temelli-dogrulama.md`. Tekrar araştırma, UYGULA.

## Önce codebase-memory

`codebase-memory-mcp` ile başla. İndeks yoksa `index_repository`. Grep/Glob sonra.

## Neden acil

Gölge kaydedici (commit `4a7109c`) veri topluyor ama **akışı saklamıyor**. `t_rel` ve
`f0_traj` gibi alanlar sonradan ASLA geri getirilemez — şimdi eklenmezse bugüne kadar
toplanan veri bu iş için ölü. Onun için bu görev önceliklidir.

## Eklenecek alanlar (araştırmanın kesin listesi)

Hepsi mevcut `samples`'tan, **aynı yerde** (`worker/speaker_tap.py:149-192` civarı),
**sadece numpy** ile, **ham ses SAKLAMADAN**:

| alan | dtype | boyut | neden |
|---|---|---|---|
| `t_rel` | f32 | 1 | pencerenin tur başına ofseti — **EN KRİTİK**, akış analizi bununla mümkün |
| `track_id` | i16 | 1 | çok mikrofonlu odada pencere ayrımı; sonradan türetilemez |
| `capture_ok` | i8 | 1 | `speaker_tap.py:708` zaten hesaplıyor, atılıyor (agent yankısı ayıklama) |
| `turn_final_name` | str | tur başına | `resolve_turn` kararı |
| `turn_final_reason` | str | tur başına | karar gerekçesi — bu ikisi olmadan %70.5 metriği offline üretilemez |
| `f0_med`, `f0_p10`, `f0_p90` | f16 | 3 | perde istatistikleri |
| `f0_traj` | f16 | 16 | F0 eğrisinin sabit-uzunluk örneği — **akışı saklayan tek alan**, geri getirilemez |
| `voiced_ratio` | f16 | 1 | sesli çerçeve oranı; ölü bölgenin muhtemel sebebi |
| `hnr` | f16 | 1 | autokorelasyon zirvesinden (Boersma 1993) |
| `alpha_ratio`, `tilt` | f16 | 2 | spektral eğim — kimlik değil, kalibrasyon değişkeni |
| `snr_db` | f16 | 1 | çerçeve enerjisi p10 gürültü tabanı |
| `env_mod_hz` | f16 | 1 | zarf modülasyon zirvesi ≈ hece hızı |
| `ltas` | f16 | 24 | 24 bantlı uzun-dönem ortalama spektrum |

Toplam ~105 B/satır (+%17). **EKLEME:** jitter, shimmer, formant izleme, ham/mel
spektrogram — araştırmada gerekçeleriyle elendi, geri getirme.

## Kısıtlar — sıkı

* **SADECE numpy.** Doğrulandı: lokal ve canlı venv'de numpy 2.5.1, scipy/librosa/
  parselmouth/torch **YOK**. Yeni bağımlılık EKLEME. Her şey `np.fft` ile el yazımı.
* **Gerçek zamanlı yolu bloklamayacak.** Hedef: pencere başına ≤3 ms (embed ~20-40 ms).
  **ÖLÇ ve raporda söyle.** Aşıyorsa hesabı ertele/tamponla, ama konuşmayı geciktirme.
* **Ham ses saklama YOK.** Sadece hesaplanmış sayılar npz'ye girer.
* **Karar mantığına DOKUNMA.** Bu hâlâ salt gözlem. Hızlı yolun davranışı bit düzeyinde
  aynı kalmalı — bunu testle kilitle.
* Biyometrik: `worker/data/` ignore'da olduğunu `git check-ignore -v` ile TEYİT ET.
* Env okuma tuzağı (DEVIR §7, `c9d0d27`): modül seviyesinde okuma YOK, fonksiyon içinde
  çağrı anında. Depoda `test_env_kollari` AST testi var, geçmeli.
* Prozodi hesabı `.env` ile ayrıca kapatılabilsin (varsayılan AÇIK), mevcut
  `SPEAKER_EMB_LOG_*` isimlendirmesine uy.
* Eski npz dosyaları yeni alanları içermiyor — okuma tarafı ileride buna toleranslı
  olmalı; alan adlarını ve sürümü npz içinde belirt (basit bir `schema_version` yeter).

## Yasaklar

* **Deploy YOK. `systemctl` YOK. Canlı `.25`'e YAZMA.**
* `speakers.db`'ye, gerçek `memory/`'ye yazma.
* **Görsel/canlı test YAPMA** — kullanıcı kendi yapar.
* ⛔ **Görselleştirme kapsam dışı** — grafik, panel, spektrogram görüntüsü üretme.
* `git stash` KULLANMA. Commit at (main'de kal), **PUSH ETME**.

## Testler

Şu an **459 test** geçiyor. DÜŞMESİN. Ekle:
* Her prozodi fonksiyonu için bilinen sinyalle doğrulama (örn. saf 200 Hz sinüs →
  `f0_med` ≈ 200; sessizlik → `voiced_ratio` ≈ 0). Uydurma beklenti yazma, matematiği kur.
* Karar mantığının değişmediğini kanıtlayan test.
* `f0_traj` uzunluğunun her zaman 16 olduğu (kısa/uzun pencerede de).
* `./check.sh` — kendi dosyanda temiz olsun (HEAD'de zaten 4 eski ruff bulgusu var, senin değil).

## Rapor (KISA — 12 satır)

* Eklenen alanlar ve npz şema sürümü
* Pencere başına ÖLÇÜLEN prozodi maliyeti (ms)
* Gerçek zamanlı yolu bloklamadığının gerekçesi
* Karar mantığının değişmediğinin kanıtı (hangi test)
* `git check-ignore` çıktısı
* `.env` anahtarı + varsayılan
* Test sayısı önce/sonra, `./check.sh`, commit hash'leri
