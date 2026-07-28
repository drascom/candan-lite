# Görev — CLIENT AÇILIŞINDA WARM-UP GECİKMESİ (kök sebep + seçenekler)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

**Bu görev SALT-OKUNUR analizdir.** Kod DEĞİŞTİRME, deploy ETME, sunucuya YAZMA.
Çıktın: hangi bileşenin soğuk olduğu + ölçüm + seçenekler. Uygulama ayrı bir turda.

## Önce codebase-memory

Kod aramaya Grep/Glob ile BAŞLAMA. Önce `codebase-memory-mcp`: `search_graph`,
`trace_path`, `get_code_snippet`, `get_architecture`, `search_code`. İndeks yoksa
önce `index_repository`. Grep/Glob ancak bundan sonra.

## Şikâyet (kullanıcıdan, 28 Tem)

> "Client'i her başlattığımda bir warm-up süresi oluyor. 5 saniye, 10 saniye, 15 saniye.
> Bunu engellemek için modelin sürekli arkada hazır beklemesini istiyorum."

İlk konuşma turu geç geliyor; sonrakiler normal. Süre değişken (5/10/15 sn) —
bu, sabit bir kurulum maliyeti DEĞİL, muhtemelen tembel (lazy) yükleme +
bağlantı kurulumu karışımı.

## Cevaplanacak sorular (ÖLÇ, tahmin etme)

1. **Hangi bileşen soğuk?** Zinciri çıkar ve her halkanın ilk-tur maliyetini AYRI ölç:
   * STT (Wyoming / ses tanıma)
   * TTS (`higgs-tts` — model ağırlığı yüklemesi? ilk sentez?)
   * LLM (`candan-brain` — ilk token gecikmesi / prefill)
   * LiveKit oda bağlantısı + `candan-worker` job başlangıcı
   * speaker-ID modeli (`worker/models/`, *.onnx yüklemesi)
   Hangisi 5-15 sn'yi açıklıyor? Tek suçlu mu, toplam mı?
2. **Neden değişken?** 5 ile 15 sn arasındaki farkı ne belirliyor? (soğuk disk cache,
   model ağırlığı boyutu, ilk HTTP bağlantısı, GPU/CPU tahsisi, süreç mi yeniden doğuyor)
3. **Süreç ömrü:** Servisler sürekli ayakta mı, yoksa client bağlanınca mı doğuyor?
   `candan-worker` her LiveKit job'ında yeni süreç mi başlatıyor? Ağırlıklar süreçler
   arasında paylaşılıyor mu, her seferinde yeniden mi yükleniyor?
4. **Zaten var olan warm-up var mı?** Kod tabanında preload/warmup/keepalive denemesi
   var mı? Varsa neden yetmiyor?
5. **Log kanıtı:** `logs/` altında açılış turlarının zaman damgalarına bak. İlk turun
   nerede beklediğini gösteren satırları çıkar.

## Seçenekleri değerlendir (kodu YAZMA, sadece öner)

Her biri için: kazanç, maliyet (RAM/GPU sürekli tutulur mu), risk.
* Servis başlangıcında model preload (ilk istek yerine boot'ta yükle)
* Periyodik keepalive/ısıtma isteği (idle'da modeli düşürmeyen sahte tur)
* Kalıcı worker süreci (job başına yeniden doğmayı bırak)
* Ağırlıkları `mmap`/paylaşımlı cache ile süreçler arası paylaşma
* Sadece algı düzeltmesi (ilk turda "bir saniye" dolgusu) — asıl çözüm değil, NOT DÜŞ

Kullanıcının makinesinde RAM/GPU'nun sürekli meşgul kalmasının maliyeti VAR.
"Hep açık tut" önerisini bu maliyeti söylemeden verme.

## Sınırlar

* Kod DEĞİŞTİRME. Deploy YOK. `systemctl` YOK. Canlı `.25` ve oracle-stage'e DOKUNMA.
* Görsel/canlı test YAPMA — kullanıcı kendi yapar. Sen ölçümü log'dan ve koddan çıkar.
* Ölçüm için yerel bir şey çalıştırman gerekirse önce raporda söyle, kendi başına
  canlı servis başlatma.

## Rapor (KISA — madde madde, 15 satırı geçme)

* Soğuk zincirin halkaları ve her birinin ölçülen/tahmini payı (tahminse "TAHMİN" yaz)
* Asıl suçlu (`dosya:satır`)
* 5-15 sn değişkenliğinin sebebi
* En iyi 2 seçenek: kazanç / RAM-GPU maliyeti / risk
* Önerin (tek cümle)
