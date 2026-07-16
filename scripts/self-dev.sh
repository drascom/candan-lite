#!/usr/bin/env bash
# self-dev.sh — SESLE geliştirme modu (self-development) worktree + merge yardımcısı.
#
# Sesli worker "geliştirme moduna geç" sinyalinde dev pi'yı İZOLE bir git worktree'de
# (ayrı branch) çalıştırır — bkz. worker/pi_brain.py (_ensure_dev_worktree) ve
# docs/self-dev.md. Bu script o worktree'yi elle yönetmek + dev işini gözden geçirip
# ONAYLA main'e almak içindir. OTOMATİK MERGE YOK: main'e yazım daima elle + açık onayla.
#
# Kullanım:
#   scripts/self-dev.sh status          # worktree + branch durumu
#   scripts/self-dev.sh worktree        # worktree'yi oluştur/yeniden kullan
#   scripts/self-dev.sh diff            # dev branch'in main'e göre farkı
#   scripts/self-dev.sh merge [--yes]   # farkı göster, ONAYLA, main'e merge et
#   scripts/self-dev.sh remove [--branch]  # worktree'yi kaldır (--branch: branch'i de sil)
#
# Ortam (worker/pi_brain.py ile AYNI varsayılanlar):
#   DEV_WORKTREE (default: <repo>/../candan-lite-selfdev)
#   DEV_BRANCH   (default: self-dev)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_BRANCH="${DEV_BRANCH:-self-dev}"
DEV_WORKTREE="${DEV_WORKTREE:-$(cd "$REPO_ROOT/.." && pwd)/candan-lite-selfdev}"
MAIN_BRANCH="${MAIN_BRANCH:-main}"

git_main() { git -C "$REPO_ROOT" "$@"; }

branch_exists() { git_main rev-parse --verify --quiet "refs/heads/$DEV_BRANCH" >/dev/null; }

ensure_worktree() {
  if [ -e "$DEV_WORKTREE/.git" ]; then
    echo "worktree zaten var: $DEV_WORKTREE (branch $DEV_BRANCH)"
    return 0
  fi
  if branch_exists; then
    git_main worktree add "$DEV_WORKTREE" "$DEV_BRANCH"
  else
    git_main worktree add -b "$DEV_BRANCH" "$DEV_WORKTREE"
  fi
  echo "worktree hazır: $DEV_WORKTREE (branch $DEV_BRANCH)"
}

cmd_status() {
  echo "== worktrees =="
  git_main worktree list
  if branch_exists; then
    echo
    echo "== $DEV_BRANCH commit'leri ($MAIN_BRANCH'e göre önde) =="
    git_main log --oneline "$MAIN_BRANCH".."$DEV_BRANCH" || true
    if [ -e "$DEV_WORKTREE/.git" ]; then
      echo
      echo "== worktree çalışma ağacı (commit'lenmemiş) =="
      git -C "$DEV_WORKTREE" status --short || true
    fi
  else
    echo "($DEV_BRANCH branch'i henüz yok — dev moduna hiç girilmemiş)"
  fi
}

cmd_diff() {
  branch_exists || { echo "$DEV_BRANCH yok — gösterilecek fark yok."; exit 0; }
  # Commit'li fark + worktree'deki commit'lenmemiş değişiklikler.
  echo "== $DEV_BRANCH vs $MAIN_BRANCH (commit'li) =="
  git_main diff --stat "$MAIN_BRANCH".."$DEV_BRANCH" || true
  git_main diff "$MAIN_BRANCH".."$DEV_BRANCH" || true
  if [ -e "$DEV_WORKTREE/.git" ] && [ -n "$(git -C "$DEV_WORKTREE" status --porcelain)" ]; then
    echo
    echo "!! worktree'de COMMIT'LENMEMİŞ değişiklik var (merge bunları ALMAZ):"
    git -C "$DEV_WORKTREE" status --short
  fi
}

cmd_merge() {
  local yes=0
  [ "${1:-}" = "--yes" ] && yes=1
  branch_exists || { echo "$DEV_BRANCH yok — merge edilecek bir şey yok."; exit 1; }

  # Güvenlik: worktree'de commit'lenmemiş iş varsa merge onları almaz → uyar.
  if [ -e "$DEV_WORKTREE/.git" ] && [ -n "$(git -C "$DEV_WORKTREE" status --porcelain)" ]; then
    echo "UYARI: dev worktree'de commit'lenmemiş değişiklik var; merge SADECE commit'li işi alır."
    echo "Önce worktree'de commit'le: git -C \"$DEV_WORKTREE\" add -A && git -C \"$DEV_WORKTREE\" commit"
    echo
  fi

  if git_main diff --quiet "$MAIN_BRANCH".."$DEV_BRANCH"; then
    echo "$DEV_BRANCH, $MAIN_BRANCH ile aynı — merge edilecek commit yok."
    exit 0
  fi

  echo "== main'e alınacak fark =="
  git_main diff --stat "$MAIN_BRANCH".."$DEV_BRANCH"
  echo
  # Kapsam kontrolü: dev SADECE pi/ altını değiştirmeli. Dışına çıkıldıysa uyar.
  local outside
  outside="$(git_main diff --name-only "$MAIN_BRANCH".."$DEV_BRANCH" | grep -v '^pi/' || true)"
  if [ -n "$outside" ]; then
    echo "!! DİKKAT: pi/ DIŞINDA değişen dosyalar var:"
    echo "$outside" | sed 's/^/   /'
    echo
  fi

  if [ "$yes" -ne 1 ]; then
    printf "Bu farkı %s'e merge etmek için 'onayla' yaz: " "$MAIN_BRANCH"
    read -r reply
    [ "$reply" = "onayla" ] || { echo "İptal edildi (main'e YAZILMADI)."; exit 1; }
  fi

  local cur
  cur="$(git_main rev-parse --abbrev-ref HEAD)"
  [ "$cur" = "$MAIN_BRANCH" ] || git_main checkout "$MAIN_BRANCH"
  git_main merge --no-ff "$DEV_BRANCH" -m "merge(self-dev): $DEV_BRANCH → $MAIN_BRANCH (sesle geliştirme)"
  echo "Merge tamam. Push OTOMATİK DEĞİL — istersen elle: git -C \"$REPO_ROOT\" push"
}

cmd_remove() {
  local del_branch=0
  [ "${1:-}" = "--branch" ] && del_branch=1
  if [ -e "$DEV_WORKTREE/.git" ]; then
    git_main worktree remove "$DEV_WORKTREE" || git_main worktree remove --force "$DEV_WORKTREE"
    echo "worktree kaldırıldı: $DEV_WORKTREE"
  else
    echo "worktree yok: $DEV_WORKTREE"
  fi
  git_main worktree prune
  if [ "$del_branch" -eq 1 ] && branch_exists; then
    git_main branch -D "$DEV_BRANCH"
    echo "branch silindi: $DEV_BRANCH"
  fi
}

case "${1:-status}" in
  status)   cmd_status ;;
  worktree) ensure_worktree ;;
  diff)     cmd_diff ;;
  merge)    shift || true; cmd_merge "${1:-}" ;;
  remove)   shift || true; cmd_remove "${1:-}" ;;
  *) echo "kullanım: $0 {status|worktree|diff|merge [--yes]|remove [--branch]}"; exit 2 ;;
esac
