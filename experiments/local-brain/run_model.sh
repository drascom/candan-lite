#!/bin/bash
# llama-server'i beyin konfigurasyonuyla baslat, ACILIS LOGUNU sakla (KV/model/compute
# buffer boyutlari oradan KESIN okunur), hazir olunca cik.
#   ./run_model.sh <gguf> <port> <ctx_size> <parallel> <logfile>
set -u
GGUF="$1"; PORT="$2"; CTX="$3"; PAR="$4"; LOG="$5"; shift 5
pkill -f "llama-se[r]ver" 2>/dev/null
sleep 3
nohup /root/llama.cpp/build/bin/llama-server \
  --model "$GGUF" --port "$PORT" --host 127.0.0.1 \
  -ngl 999 --ctx-size "$CTX" --parallel "$PAR" \
  --jinja --no-webui --ubatch-size 512 --flash-attn on "$@" \
  > "$LOG" 2>&1 &
echo "pid=$!"
for i in $(seq 1 240); do
  if curl -s -o /dev/null -m 2 "http://127.0.0.1:$PORT/health" 2>/dev/null; then
    echo "READY after ${i}s"; break
  fi
  sleep 1
done
echo "--- BUFFER BOYUTLARI (acilis logundan) ---"
grep -iE "model buffer size|KV buffer size|compute buffer size|kv_cache|n_ctx |n_ctx_per_seq|n_parallel|flash_attn" "$LOG" | head -20
echo "--- VRAM ---"
nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader
