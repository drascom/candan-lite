#!/usr/bin/env bash
# SUNUCUDA koşar (/opt/higgs-exp/run_all.sh). Tüm bench'i sırayla yapar ve
# omnivoice-bridge'i NE OLURSA OLSUN geri başlatır (trap).
#
#   ssh root@192.168.0.25 'cd /opt/higgs-exp && ./run_all.sh'
#
# whisper.service ve candan-brain.service'e DOKUNULMAZ — STT ve beyin ölür.
set -euo pipefail
cd "$(dirname "$0")"

PY=/opt/higgs-venv/bin/python
export HF_HOME=/opt/higgs-models/hf

restore() {
  echo
  echo "── omnivoice-bridge geri başlatılıyor ──"
  systemctl start omnivoice-bridge.service || true
  sleep 20
  echo -n "durum: "; systemctl is-active omnivoice-bridge.service || true
  echo -n "/api/default: "; curl -s --max-time 15 http://127.0.0.1:8808/api/default || echo "CEVAP YOK"
  echo
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv
}
trap restore EXIT

echo "── 1/3 TABAN: OmniVoice (köprü AÇIK) ──"
"$PY" run_omnivoice_server.py

echo
echo "── 2/3 VRAM takası: omnivoice-bridge DURDURULUYOR ──"
systemctl stop omnivoice-bridge.service
sleep 5
nvidia-smi --query-gpu=memory.free --format=csv,noheader

echo
echo "── 3/3 Higgs (köprü KAPALI) ──"
"$PY" run_higgs.py

echo
"$PY" merge_manifest.py
# trap restore burada devreye girer.
