# Sesle geliştirme modu (self-development, Faz 0)

Kullanıcı **sesle** "geliştirme moduna geç" deyince asistan, kendi kodunu
düzenleyebilen bir geliştirme oturumuna geçer; "normal moda dön" deyince geri
döner. Terminal yok — her şey sesle. Değişiklikler **izole** bir git worktree'de
kalır ve ana koda **sadece elle, açık onayla** alınır.

## Nasıl çalışır

Sesli worker (`worker/pi_brain.py`) her oturumda kalıcı bir `pi --mode rpc`
alt-süreci çalıştırır. Geliştirme modu bu alt-süreci **swap** eder:

| | Normal mod | Dev mod |
|---|---|---|
| Model | Gemma (yerel) | GPT-5.6 (`openai-codex/gpt-5.6-terra`, uzak) |
| Kod tool'ları (read/bash/edit/write/grep/find/ls) | KAPALI | AÇIK |
| family-memory (hafıza tool'ları) | AÇIK | AÇIK — ama **kendi kökünde** (kimlik kapısı açıksa) |
| Hafıza kökü (`MEM_DIR`) | `memory/` | `memory/personas/dev/` |
| Hafıza kimliği (`MEM_USER`) | tanınan kişi (`ayhan`) | sabit `dev` (yalnız ev sahibi açar) |
| Çalışma dizini | repo kökü | izole worktree (`../candan-lite-selfdev`, `self-dev` branch) |
| session-id | kişiye özel | ayrı (`self-dev`) |
| persona | `candan` (vb.) | `dev` (`pi/personas/dev.md`) |

Normal modun davranışı **hiç değişmez**; dev'e özgü her şey `dev=True` yolunun
arkasında. `DEV_MODE_ENABLED=false` ile tüm mekanizma kapatılır (o zaman
`enter_dev_mode` tool'u bile sunulmaz, davranış bugünküyle bire bir aynı).

## Tetikleyici — neden tool, transkript değil

Geçiş, native bir pi tool'u ile olur: `pi/extensions/mode-switch/index.ts`
`enter_dev_mode` / `exit_dev_mode` tool'larını kaydeder. Kullanıcı "geliştirme
moduna geç" deyince model bu tool'u çağırır; worker pi'nin stdout event akışındaki
`toolCall`'ı görür (`PiBrain._detect_mode_signal`, family-memory'nin olay yolunun
aynısı) ve swap'ı tetikler.

Transkript-cümle yakalama (fallback) **seçilmedi**: Türkçe STT gürültülü (wake-word
için koca bir fuzzy katmanı var), sabit cümleye bağlamak kırılgan olurdu. Tool yolu
niyeti anlamsal çözer ve mevcut olay altyapısını aynen kullanır — ek bağımlılık yok.

Swap zamanlaması: tool çağrısı turun ORTASINDA gelir ama swap turun SONUNDA (bir
sonraki tur başında, `_current_client`) uygulanır. Böylece komutu söyleyen pi
"geçiyorum" cevabını temiz verir, sonra süreç değişir.

## Güvenlik ve izolasyon

- **Worktree izolasyonu.** Dev pi ayrı bir çalışma ağacında (`self-dev` branch)
  çalışır. Kod EDIT'leri oraya düşer; ana çalışma ağacın etkilenmez.
- **Otomatik merge YOK.** `self-dev` → `main` yazımı yalnızca elle + açık onayla
  (`scripts/self-dev.sh merge`, "onayla" yazmadan geçmez).
- **Kapsam.** Dev persona'sı yalnızca `pi/` altını değiştirmesini söyler; merge
  helper'ı `pi/` dışına çıkan dosyaları ayrıca uyarır.
- **Hafıza karışmaz.** Dev, Candan'ın bir modu değil **ayrı bir kişilik**tir: kendi
  hafıza kökü (`memory/personas/dev/`) ve kendi ruh dosyası vardır. Kök ayrı
  olduğu için izolasyon **yol düzeyinde** garantidir — dev sohbeti Candan'ın
  `soul.md` / `family.md` / `users/<kişi>/` dosyalarını ne **bağlamına alır** ne de
  oraya **yazabilir** (extension yalnız `MEM_DIR`'i görür); tersi de doğru: normal
  sohbet dev alanını görmez. Ayrı session-id ile sohbet geçmişi de karışmaz.
- **Dev hafızasına yazma kimliğe bağlı.** `MEM_USER=dev` yalnızca konuşmacı ses
  tanımayla **doğrulanmış**, normal `policy.json`'da `adult` ve (varsayılan)
  `DEV_MEM_OWNER=ayhan` ise açılır. Tanınmayan biri dev moda geçerse hafıza
  **kapalıdır** (bugünkü davranış): "dev" ile "guest" ayrı şeylerdir.
- **Dev kimliğinin rolü `child`.** Dev alanının kendi `policy.json`'ında
  `{"dev": "child"}` girdisi otomatik açılır → dev yalnız kendi private notlarına
  (`users/dev/notes/`) ve kendi `soul.md`'sine yazar; `scope: family` (ortak ruh)
  ve `project:` scope'una **yazamaz**. Normal `memory/policy.json`'a hiç dokunulmaz.

## Worktree + merge helper

```
scripts/self-dev.sh status          # worktree + branch durumu
scripts/self-dev.sh worktree        # worktree'yi oluştur/yeniden kullan
scripts/self-dev.sh diff            # self-dev'in main'e göre farkı
scripts/self-dev.sh merge           # farkı göster → "onayla" → main'e merge
scripts/self-dev.sh merge --yes     # onay sorusunu atla (yine de elle komut)
scripts/self-dev.sh remove          # worktree'yi kaldır
scripts/self-dev.sh remove --branch # worktree + self-dev branch'ini de sil
```

Worktree ilk dev-mode girişinde worker tarafından otomatik oluşturulur
(`_ensure_dev_worktree`); helper aynı worktree'yi yönetir/temizler. Mevcut
worktree'yi ya da branch'i **asla sıfırlamaz** (önceki dev işi korunur).

> Not: `merge` yalnızca **commit'li** işi alır. Dev pi'nin worktree'de bıraktığı
> commit'lenmemiş değişiklikler için helper uyarır; önce worktree'de commit'le.

## Ayarlar (worker/.env)

| Değişken | Default | Açıklama |
|---|---|---|
| `DEV_MODE_ENABLED` | `true` | Tüm mekanizmayı aç/kapa |
| `DEV_PERSONA` | `dev` | `pi/personas/<ad>.md` |
| `DEV_SESSION_ID` | `self-dev` | Dev sohbeti session-id'si |
| `DEV_MODEL` | `openai-codex/gpt-5.6-terra` | Dev beyni (uzak, GPU gerekmez) |
| `DEV_THINKING` | `minimal` | Dev thinking seviyesi |
| `DEV_WORKTREE` | `../candan-lite-selfdev` | İzole çalışma dizini |
| `DEV_BRANCH` | `self-dev` | İzole branch |
| `DEV_TOOLS_ALLOWLIST` | (boş) | Boş = tüm native tool'lar açık; liste verirsen `exit_dev_mode`'u da ekle |
| `DEV_MEM_SUBDIR` | `personas/dev` | Dev hafıza kökü (`memory/` altında) |
| `DEV_MEM_USER` | `dev` | Dev alanındaki kimlik = `users/<ad>/` |
| `DEV_MEM_ROLE` | `child` | Dev kimliğinin rolü (ortak ruh + project scope kapalı) |
| `DEV_MEM_OWNER` | `ayhan` | Dev hafızasını açabilen kişi; boş = herhangi bir doğrulanmış `adult` |

## Dokunulan dosyalar

- `worker/pi_brain.py` — dev config, `_build_pi_args(dev=…, mem_user=…)`,
  `PiRpcClient(cwd,dev,mem_user)`, `PiBrain` mod-swap (`_switch_mode`/`request_mode`/
  `_detect_mode_signal`), worktree helper, dev hafıza kökü (`_mem_root`/`_dev_mem_user`/
  `_ensure_dev_mem_root`/`pi_mem_env`).
- `worker/pi_broker.py` — `BrokerKey.mem_user` (hafızası açık/kapalı dev süreçleri
  paylaşılmaz) + alt-sürecin hafıza ortamı `pi_mem_env` ile kurulur.
- `worker/tests/test_dev_memory.py` — kimlik kapısı + iki yönlü izolasyon testleri.
- `pi/extensions/mode-switch/index.ts` (+ `tsconfig.json`) — enter/exit tool'ları.
- `pi/personas/dev.md` — dev persona (kapsam: yalnız `pi/`).
- `scripts/self-dev.sh` — worktree + onaylı merge helper.
