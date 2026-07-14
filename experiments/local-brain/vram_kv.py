#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""llama-server ile GERCEK VRAM olcumu: agirlik + KV/token + 5 oturum toplami.

TAHMIN YOK. Iki kaynak:
  1) llama-server'in ACILIS LOGU  -> "KV buffer size = X MiB" (KESIN, tahsis edilen)
                                  -> "model buffer size = Y MiB" (agirlik)
                                  -> "compute buffer size = Z MiB"
  2) nvidia-smi deltasi           -> surecin GERCEK toplam VRAM'i (CUDA context dahil)

KV/token = KV_buffer_MiB / ctx_size  (llama.cpp KV'yi ctx-size'a gore ONCEDEN tahsis eder)

Koordinator uyarisi: Ministral-3-3B'nin KV'si Qwen3.5-4B'nin 2.7 KATIYDI (zayif GQA).
14B'de bu carpani DOGRULA — agirliktaki avantaji geri alabilir.
"""
import argparse
import json
import re
import subprocess
import sys
import time

import requests

LLAMA = "/root/llama.cpp/build/bin/llama-server"


def vram_used():
    o = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], text=True)
    return int(o.strip().splitlines()[0])


def wait_ready(port, proc, timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            return False
        try:
            r = requests.get("http://localhost:%d/health" % port, timeout=2)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


def launch(model, port, ctx, parallel, extra=None):
    cmd = [LLAMA, "--model", model, "--port", str(port), "--host", "127.0.0.1",
           "-ngl", "999", "--ctx-size", str(ctx), "--parallel", str(parallel),
           "--jinja", "--no-webui", "--ubatch-size", "512"]
    if extra:
        cmd += extra
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         bufsize=1)
    return p


def read_log_until_ready(proc, port):
    """Acilis logunu oku, buffer boyutlarini cikar."""
    info = {"kv_mib": None, "model_mib": None, "compute_mib": None, "n_ctx": None,
            "n_ctx_per_seq": None, "kv_type": None}
    t0 = time.time()
    lines = []
    while time.time() - t0 < 300:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                break
            continue
        lines.append(line)
        m = re.search(r"KV (?:buffer size|self size)\s*=\s*([\d.]+)\s*MiB", line)
        if m:
            info["kv_mib"] = float(m.group(1))
        m = re.search(r"kv_unified:.*?size\s*=\s*([\d.]+)\s*MiB", line)
        if m and info["kv_mib"] is None:
            info["kv_mib"] = float(m.group(1))
        m = re.search(r"CUDA\d+ model buffer size\s*=\s*([\d.]+)\s*MiB", line)
        if m:
            info["model_mib"] = float(m.group(1))
        m = re.search(r"CUDA\d+ compute buffer size\s*=\s*([\d.]+)\s*MiB", line)
        if m:
            info["compute_mib"] = float(m.group(1))
        m = re.search(r"n_ctx\s*=\s*(\d+)", line)
        if m and info["n_ctx"] is None:
            info["n_ctx"] = int(m.group(1))
        m = re.search(r"n_ctx_per_seq\s*=\s*(\d+)", line)
        if m:
            info["n_ctx_per_seq"] = int(m.group(1))
        if "server is listening" in line or "starting the main loop" in line:
            break
    info["_log"] = "".join(lines[-60:])
    return info


def measure(model, tag, ctxs, parallel, port=8099):
    base = vram_used()
    out = {"tag": tag, "model": model, "baseline_vram_mib": base, "runs": []}
    for ctx in ctxs:
        proc = launch(model, port, ctx, parallel)
        info = read_log_until_ready(proc, port)
        ok = wait_ready(port, proc, timeout=240)
        time.sleep(4)
        used = vram_used()
        delta = used - base
        rec = {"ctx_size": ctx, "parallel": parallel,
               "vram_delta_mib": delta,
               "kv_buffer_mib": info["kv_mib"],
               "model_buffer_mib": info["model_mib"],
               "compute_buffer_mib": info["compute_mib"],
               "n_ctx": info["n_ctx"], "n_ctx_per_seq": info["n_ctx_per_seq"],
               "ready": ok}
        if info["kv_mib"] and ctx:
            rec["kv_mib_per_token"] = round(info["kv_mib"] / ctx, 5)
        out["runs"].append(rec)
        print("  %-16s ctx=%-6d par=%d | VRAM +%5d MiB | KV %8s MiB | model %8s | compute %6s | KV/tok %s"
              % (tag, ctx, parallel, delta, info["kv_mib"], info["model_mib"],
                 info["compute_mib"], rec.get("kv_mib_per_token")), flush=True)
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except Exception:
            proc.kill()
        time.sleep(6)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--ctxs", default="8192,20480")
    ap.add_argument("--parallel", type=int, default=1)
    ap.add_argument("--port", type=int, default=8099)
    a = ap.parse_args()
    ctxs = [int(x) for x in a.ctxs.split(",")]
    r = measure(a.model, a.tag, ctxs, a.parallel, a.port)
    with open(a.out, "w") as f:
        json.dump(r, f, indent=2)
    print("out:", a.out)


if __name__ == "__main__":
    main()
