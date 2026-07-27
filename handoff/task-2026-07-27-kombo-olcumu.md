# Görev — KOMBO ÖLÇÜMÜ (kulakla seçilenler canlıya girmeden önce)

Sen bir worker'sın. İşi kendin yap, kısa rapor ver. Panel/subagent açma.

⚠️ **YALNIZ `experiments/` altında çalış.** `worker/` altındaki hiçbir dosyaya
DOKUNMA — şu anda başka bir ajan orada `.env` kollarını denetliyor. Eşleme
değişikliği BU GÖREVİN İŞİ DEĞİL, sonraki turda yapılacak.

## Neden

Kullanıcı duygu atlasını kulaklıkla dinledi (44 satır: 28 iyi, 12 idare, 4 kötü).
Notlar: `experiments/duygu-atlasi/` sayfasından alınan JSON, özeti aşağıda.

Kulakla beğenilen KOMBOLAR canlıya alınacak, ama **hiçbiri ölçülmedi**. Kural ayakta:
ölçülmemiş token canlıya girmez. Bu görev o ölçümü yapar.

⚠️ Ölçüm **CANLI YOLDAN** olacak (`POST /api/tts/stream`, :8809, referans klonu,
streaming). Deney koşumu yanıltıyor — `elation` bir süre boşuna "bozuk" sanılmıştı.
Takım hazır: `experiments/higgs-tts3/token_probe.py` + `token_eval.py`.

## Ölçülecek kombolar

| kombo | ne için | kullanıcının notu |
|---|---|---|
| `<\|emotion:surprise\|><\|prosody:expressive_high\|>` | **`[surprise-*]` eşlemesi** | atlasta "iyi"; A/B testinde de ünlemli cümlede seçilmişti |
| `<\|emotion:pride\|><\|prosody:expressive_high\|>` | `[mood:proud]` yükseltmesi | **"mükemmel"** |
| `<\|emotion:contentment\|><\|prosody:expressive_low\|>` | `[mood:calm]` gözden geçirmesi | "iyi" |
| `<\|emotion:awe\|><\|prosody:expressive_high\|>` | yedek aday | "iyi" |

Tek başına `awe` **KÖTÜ** çıktı ("şuh kalmış, heyecan yok") — bugün canlıya alınan
eşleme buydu, değişecek. Kombosu iyi bulunduğu için yedek olarak ölçülüyor.

Ölçüm ölçütü öncekiyle AYNI olsun (karşılaştırılabilir olması için):
kombo başına **≥12 örnek**, Whisper geri-dönüşü, dört ölçüt — boş çıktı · çok kısa ·
uydurma konuşma (medyanın 2 katı) · **cümlenin başını yeme**. Sonuç tablosu
`handoff/2026-07-27-duygu-katmani.md` §2 biçiminde olsun.

⚠️ **Δsüre ELEME ÖLÇÜTÜ DEĞİL.** Kullanıcı açıkça söyledi: *"çalışıyorlarsa süresi
çok önemli değil, koyabiliriz onları da sisteme."* Yani kombo cümleyi uzatıyorsa bu
tek başına ret sebebi değil. Ret sebebi YALNIZ anlaşılırlık: boş çıktı, çok kısa,
uydurma konuşma, cümlenin başını yeme, WER yükselmesi. Δsüre yine ÖLÇÜLÜP raporlansın
(etiketin iş yapıp yapmadığının göstergesi olarak) ama karara girmesin.

**Token sırası da bir değişken:** `<|emotion:X|><|prosody:Y|>` ile
`<|prosody:Y|><|emotion:X|>` aynı sonucu vermeyebilir. En az bir kombo için iki sırayı
da ölç; fark varsa hepsinde ölç ve hangi sıranın kullanılacağını raporda söyle.

## Cümleler

Gerçek kullanım cümleleri olsun, nötr tek cümle değil — atlasın dersi buydu.
`[surprise-*]` için hem ünlemli ("Vay canına! Kargon bir gün erken gelmiş.") hem
ünlemsiz ("Sınavdan tam not almışsın, hem de tek başına çalışarak.") cümle kullan;
şaşırmanın zor sınavı ünlemsiz olan.

## Kulak seti

Ölçüm biterken kullanıcının son bir kez dinleyeceği küçük bir sayfa üret
(`duygu-atlasi` sayfa deseniyle, kulak notu + JSON kopyalama dahil): her kombo,
tekil hâliyle ve tabanla yan yana. Kullanıcı ölçüm temiz çıkan adaylar arasından
son seçimi yapacak.

## Sınırlar

* `worker/` DEĞİŞMEZ. Sunucuya HİÇBİR değişiklik yok — yalnız HTTP isteği.
* `higgs-tts` servisini RESTART ETME.
* Ölçüm uzun sürebilir; hata olursa DUR ve raporla, yarım kalırsa kaldığı yerden
  devam edebilsin (üretilmiş wav'ı yeniden üretme).

## Belgeleme

`handoff/2026-07-27-kombo-olcumu.md`: sonuç tablosu, token sırası bulgusu, öneri
(hangi kombo hangi etikete). DEVİR'e kısa madde — başka ajanların maddelerini silme.
Tek commit, Türkçe, push YOK.

## Rapor (KISA)

* Sonuç tablosu (kombo · anlaşılan · WER · Δsüre · karar)
* Token sırası fark ediyor mu
* Kulak setinin komutu
* Commit hash
