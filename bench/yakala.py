# -*- coding: utf-8 -*-
"""pi'yi GERCEK canli argumanlariyla bir kez calistirip modele giden HTTP govdesini
proxy uzerinden yakalar. Amac: sistem promptunun BIREBIR kopyasini almak.

Canli'dan tek farki: --model llama-proxy/... (govde yakalansin diye) ve session
dizini gecici (canli 'sessions/ayhan' oturumu KIRLENMESIN).
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path("/Users/drascom/work/candan-lite")
SCRATCH = pathlib.Path(__file__).parent
sys.path.insert(0, str(REPO / "worker"))

# .env'i canlidaki gibi yukle (PI_THINKING vb. dogru olsun)
for satir in (REPO / "worker" / ".env").read_text().splitlines():
    satir = satir.strip()
    if not satir or satir.startswith("#") or "=" not in satir:
        continue
    k, v = satir.split("=", 1)
    os.environ.setdefault(k.strip(), v.split("#")[0].strip().strip('"'))

import pi_brain as p  # noqa: E402  (.env yuklendikten SONRA import sart)

args = p._build_pi_args("candan", "ayhan", model="llama-proxy/gemma-4-12B-it-qat-q4_0")

# --mode rpc -> -p (tek atislik). Session dizinini gecici olana cevir.
args = [a for a in args]
i = args.index("--mode")
del args[i:i + 2]
i = args.index("--session-dir")
args[i + 1] = str(SCRATCH / "gecici-sessions")
args[args.index("--session-id") + 1] = "yakala-test"
args += ["-p", "Merhaba"]

print("ARGS:", " ".join(args), "\n")
r = subprocess.run(args, cwd=str(REPO), capture_output=True, text=True, timeout=300)
print("rc =", r.returncode)
print("stdout:", r.stdout[:800])
print("stderr:", r.stderr[-800:])
