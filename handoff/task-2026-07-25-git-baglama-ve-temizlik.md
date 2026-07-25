# GÖREV: git'e bağlama + sunucudan test temizliği

Sen bir WORKER'sın. İşi kendin yap, başka panel/worker AÇMA, cmux ile delege ETME.
Bitince KISA rapor ver (madde madde, uzun anlatım yok).

Proje: `/Users/drascom/Documents/work/candan-lite`
Repo: https://github.com/drascom/candan-lite (PUBLIC, default branch `main`, son commit 18 Tem)

## DURUM (doğrulandı, tekrar kontrol etme)

- TTS deploy'u bugün 16:27'de ZATEN yapıldı. `trnorm.py`, `tts_cache.py`, `omnivoice_tts.py`
  sunucuda ve md5'leri lokalle birebir aynı. Servis `active/running`, logda traceback yok.
- `pronounce_tr.json` kontrol edildi → trnorm ile ÇAKIŞMA YOK. Konu kapandı.
- Lokal klasörde `.git` YOK. Son iki günün işi (bench + trnorm + cache + guard) hiç commit edilmedi.

## KURALLAR — ihlal etme

- **Sunucuda (`root@192.168.0.25`) SADECE tek bir işlem yapacaksın: İŞ 1'deki test silme.**
  Başka hiçbir yazma yok. Restart YOK, rsync YOK, `.env`'e dokunma.
- **`go.sh`'ı sunucuya GÖNDERME.** Lokal sürüm Mac'e özel; systemd zaten `agent.py dev`
  çalıştırıyor, `go.sh` kullanılmıyor. Göndermek oradaki manuel `./go.sh worker` yolunu kırar.
- **`git push` YAPMA.** Commit'e kadar git, orada dur. Push'u kullanıcı onaylayacak.
- **Görsel test YOK.** Uygulama açma, ses dinleme, GUI doğrulama yok.

## İŞ 1 — Sunucudaki testleri sil

Kullanıcı testlerin ne sunucuda ne git'te olmasını istiyor. Sunucuya rsync'lenmişler:

```bash
ssh root@192.168.0.25 'cd /opt/candan-lite/worker && ls -la tests/'
ssh root@192.168.0.25 'rm -rf /opt/candan-lite/worker/tests'
ssh root@192.168.0.25 'ls -la /opt/candan-lite/worker/ | grep -c tests'
```

Silmeden ÖNCE `tests/` içinde bizim 3 dosyamız (`test_tts_cache.py`,
`test_tts_short_guard.py`, `test_go_readiness.sh`) + `__pycache__` DIŞINDA bir şey varsa
**DUR, silme, rapor et.**

Silme çalışan servisi etkilemez (import edilmiyorlar) ama silme SONRASI teyit et:
`systemctl is-active candan-worker.service` → `active` olmalı.

## İŞ 2 — Git'i bağla

Working tree'yi BOZMADAN mevcut klasörü repoya iliştir:

```bash
cd /Users/drascom/Documents/work/candan-lite
git init -b main
git remote add origin https://github.com/drascom/candan-lite
git fetch origin main
git reset --mixed origin/main      # working tree'ye DOKUNMAZ, sadece index'i hizalar
git status
```

⚠️ `git checkout`, `git restore`, `git clean`, `git reset --hard` **KULLANMA** — dosya siler.

## İŞ 3 — .gitignore'u düzelt (commit'ten ÖNCE)

Repo PUBLIC. Şu ikisi mevcut `.gitignore`'da YOK ve girmemeli:

1. `worker/tests/` — kullanıcı kararı: testler git'e girmesin.
2. `.server-backups/` — **kişisel/aile ses verisi** (`speakers.db`, `expression-samples/`
   altında ayhan/havi ses kayıtları). Public repo'ya ASLA girmemeli. Mevcut `.gitignore`
   `worker/data/` ve `/memory/` için bu korumayı yapıyor, `.server-backups/` unutulmuş.

İkisini de `.gitignore`'a ekle, gerekçeyi tek satır yorumla yaz (dosyanın mevcut üslubuna uy).

## İŞ 4 — Commit öncesi denetim (EN KRİTİK ADIM)

`git status` çıktısını İNCELE. Üç şeye bak:

1. **`deleted:` satırları** — remote'da olup lokalde olmayan dosyalar. Bunları commit'lersen
   repodan SİLİNİRLER. Her birini listele ve raporda sor; **kendiliğinden silme.**
2. **Sızıntı taraması** — staged olacak dosyalarda sır var mı:
   ```bash
   git add -A
   git diff --cached --stat | tail -5
   git diff --cached --name-only | grep -iE "\.env|secret|token|key|\.db$|credential" || echo "temiz"
   git diff --cached -S"sk-" --name-only; git diff --cached -S"api_key" --name-only
   ```
   Şüpheli bir şey çıkarsa **DUR, commit etme, rapor et.**
3. **Boyut** — `git diff --cached --stat` toplamı makul mü? Bench 6.9 GB; `.gitignore`
   `out/`, `venvs/`, `vendor/`, `refs/*.wav`'ı dışlıyor. Toplam onlarca MB'ı geçiyorsa
   bir şey kaçmış demektir → DUR, rapor et.

## İŞ 5 — Commit (denetim temizse)

Mantıklı parçalara ayır, tek dev commit yapma. Önerilen sıra:

1. `.gitignore` güncellemesi
2. TTS bench araçları (`experiments/tts-local-bench/` — runners, sentences, compare.html, README)
3. TTS production kodu (`worker/trnorm.py`, `worker/tts_cache.py`, `worker/omnivoice_tts.py`)
4. `worker/go.sh` (Mac-only istemci moduna geçiş)
5. `handoff/` + `docs/` dokümanları
6. Kalan her şey (22 Tem ASR/speaker işi vb.) — mantıklı gruplayarak

Commit mesajları Türkçe, tek satır özet + gerekirse kısa gövde. Mevcut repo üslubuna bak
(`git log --oneline -15`).

**PUSH ETME.** `git log --oneline` ve `git status` çıktısıyla bitir.

## RAPOR (kısa)

- İŞ 1: silindi mi, servis hâlâ active mi.
- `deleted:` çıkan dosyalar (varsa) — liste.
- Sızıntı taraması sonucu + staged toplam boyut.
- Atılan commit'ler (`git log --oneline`).
- Push için hazır mı, kullanıcının bilmesi gereken bir şey var mı (TEK madde).
