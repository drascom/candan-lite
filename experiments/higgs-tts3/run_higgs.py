"""Higgs TTS 3 (4B) — Türkçe bench, SUNUCUDA (RTX 3090) düz `transformers` ile.

Ağırlıklar `bosonai/higgs-tts-3-4b` ile BİREBİR aynı; yalnız `trust_remote_code`
paketlemesi (`multimodalart/higgs-audio-v3-tts-4b-transformers`) kullanılıyor —
`higgs_multimodal_qwen3` mimarisi hiçbir transformers sürümünde YOK, o yüzden
resmi repo düz transformers ile yüklenemiyor. Ayrıntı: README.md.

Üç set:
  • higgs-default      : referans YOK → modelin kendi (zero-shot) sesi
  • higgs-clone        : /opt/omnivoice/default-ref.wav + transkripti → Candan'ın sesi
                         (canlı OmniVoice ile AYNI referans; kullanıcı iki modeli aynı
                         sesle kıyaslasın diye). Metin HAM gider.
  • higgs-clone-trnorm : aynı klon, ama metin önce trnorm'dan geçer. Canlı köprü de
                         num2words normalizasyonu uyguluyor — karşılaştırma bu setle adil.

Referans BİR KEZ kodlanır (`refs/default-ref.codes.pt`), 29 cümlenin hepsi aynı
kodları kullanır. Kodlama maliyeti cümle ölçümlerinin DIŞINDA, ayrıca raporlanır.

    /opt/higgs-venv/bin/python run_higgs.py                 # üç set
    /opt/higgs-venv/bin/python run_higgs.py clone           # tek set
    /opt/higgs-venv/bin/python run_higgs.py --only 01-simdi 09-uzun-akici

ÖNKOŞUL: omnivoice-bridge.service DURDURULMUŞ olmalı (VRAM). setup_server.sh'a bak.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

from bench_common import OUT, PeakMonitor, bench, gpu_used_mib, ram_used_mib, read_wav

# trnorm tek kaynaktan gelir: experiments/tts-local-bench/trnorm.py. Repoda oradan
# import edilir; sunucuya sync_to_server.sh yanına kopyalar (orada tts-local-bench yok).
_SIBLING = Path(__file__).resolve().parent.parent / "tts-local-bench"
if _SIBLING.is_dir():
    sys.path.insert(0, str(_SIBLING))
from trnorm import normalize_tr  # noqa: E402

MODEL_ID = os.environ.get(
    "HIGGS_MODEL", "multimodalart/higgs-audio-v3-tts-4b-transformers")
REF_WAV = Path(os.environ.get("HIGGS_REF", "/opt/omnivoice/default-ref.wav"))
REF_TEXT = (
    "Merhaba, bu bir Türkçe seslendirme testidir. VoxCPM 2 ile uzun kitapları "
    "sesli kitaba dönüştürebilirsiniz."
)
REF_CODES = Path(__file__).resolve().parent / "refs" / "default-ref.codes.pt"

DEVICE = "cuda (RTX 3090)"
# clone-trnorm: klon setiyle AYNI, ama metin önce trnorm'dan geçiyor. Canlı
# omnivoice-bridge de num2words normalizasyonu uyguluyor — Higgs'e ham metin verip
# OmniVoice'a normalize metin vermek adil olmazdı. Bu set "Higgs'i canlıya alsak
# trnorm'a hâlâ ihtiyaç var mı?" sorusunun cevabı.
SETS = ("default", "clone", "clone-trnorm")

# AGENTS.md'nin ses klonlama için önerdiği örnekleme. 2048 kare = 25 fps'te ~82 sn,
# en uzun cümle için fazlasıyla yeter.
GEN = {"temperature": 0.8, "top_k": 50, "max_new_tokens": 2048}


def load_model():
    """Modeli yükle; yükleme sırasındaki VRAM/RAM TEPE değerlerini de döndür."""
    from transformers import AutoModelForCausalLM, AutoTokenizer

    print(f"[yükleme] {MODEL_ID}", flush=True)
    print(f"[yükleme] önce: GPU {gpu_used_mib()} MiB · RAM {ram_used_mib()} MiB", flush=True)
    t0 = time.perf_counter()
    with PeakMonitor(interval=0.2) as mon:
        # trust_remote_code tokenizer'da da şart: yoksa etkileşimli onay sorar ve
        # arka planda (nohup, tty yok) koşum orada takılır.
        tok = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_ID,
            trust_remote_code=True,
            dtype=torch.bfloat16,
            low_cpu_mem_usage=True,
            device_map={"": 0},
        ).eval()
        # Kodek (fp32) tembel yükleniyor — tepe ölçümü içine girsin diye burada zorla.
        model.get_audio_codec()
        torch.cuda.synchronize()
    load_s = time.perf_counter() - t0
    stats = {
        "load_s": round(load_s, 2),
        "gpu_peak_mib": mon.gpu_peak,
        "ram_peak_mib": mon.ram_peak,
        "gpu_after_mib": gpu_used_mib(),
        "ram_after_mib": ram_used_mib(),
        "torch_allocated_mib": round(torch.cuda.memory_allocated() / 2**20),
        "torch_reserved_mib": round(torch.cuda.memory_reserved() / 2**20),
    }
    print(f"[yükleme] {load_s:.1f}s · GPU tepe {stats['gpu_peak_mib']} MiB · "
          f"RAM tepe {stats['ram_peak_mib']} MiB · "
          f"torch ayrılan {stats['torch_allocated_mib']} MiB", flush=True)
    return model, tok, stats


def reference_codes(model) -> tuple[torch.Tensor, dict]:
    """Referans sesi bir kez kodla, diske yaz. (kodlar, ölçüm) döner."""
    if REF_CODES.exists():
        codes = torch.load(REF_CODES, map_location="cpu")
        # encode_s=None: bu koşumda kodlama YAPILMADI. 0.0 yazmak "bedava" gibi
        # görünürdü — gerçek maliyet ilk koşumda ölçüldü (~0.27 s).
        return codes, {"cached": True, "encode_s": None,
                       "frames": int(codes.shape[0]), "path": str(REF_CODES)}
    audio, sr = read_wav(REF_WAV)
    wav = torch.from_numpy(audio)
    t0 = time.perf_counter()
    codes = model._encode_reference(wav, sr).cpu()
    encode_s = time.perf_counter() - t0
    REF_CODES.parent.mkdir(parents=True, exist_ok=True)
    torch.save(codes, REF_CODES)
    print(f"[referans] {REF_WAV.name} {len(audio) / sr:.2f}s @ {sr} Hz → "
          f"{codes.shape[0]} kare × {codes.shape[1]} codebook, {encode_s:.2f}s "
          f"(bu maliyet cümle ölçümlerinin DIŞINDA)", flush=True)
    return codes, {"cached": False, "encode_s": round(encode_s, 3),
                   "frames": int(codes.shape[0]), "ref_wav": str(REF_WAV),
                   "ref_dur_s": round(len(audio) / sr, 2), "ref_sr": sr,
                   "path": str(REF_CODES)}


def first_audio_latency(model, tok, text: str, codes: torch.Tensor | None) -> dict | None:
    """Streaming OLSAYDI ilk sese kadar ne kadar beklerdik?

    Prefill + ilk çözülebilir kare bloğu (delay pattern yüzünden N=8 kare) süresi.
    Model iç metodlarını kullanır; sürüm değişirse sessizce None döner.
    """
    try:
        import sys

        n = model.num_codebooks
        mod = sys.modules[type(model).__module__]  # trust_remote_code modülü
        delayed_ref = mod.apply_delay_pattern(codes.to(torch.long)) if codes is not None else None
        prompt_ids = model._build_prompt_ids(
            tok, text,
            num_ref_tokens=0 if delayed_ref is None else delayed_ref.shape[0],
            reference_text=REF_TEXT if delayed_ref is not None else None,
        )
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        embeds = model._prefill_embeds(prompt_ids, delayed_ref)
        out = model.model(inputs_embeds=embeds, use_cache=True)
        torch.cuda.synchronize()
        prefill_s = time.perf_counter() - t0

        past, hidden = out.past_key_values, out.last_hidden_state[:, -1, :]
        position = embeds.shape[1]
        state = mod._SamplerState(num_codebooks=n)
        for _ in range(n):  # N kare = ilk çözülebilir blok
            logits = model.audio_head(hidden).to(torch.float32)[0]
            c = mod._sampler_step(logits, state, temperature=GEN["temperature"],
                                  top_p=None, top_k=GEN["top_k"])
            step = model.audio_embedding(c.unsqueeze(0)).unsqueeze(1).to(embeds.dtype)
            out = model.model(inputs_embeds=step, past_key_values=past, use_cache=True,
                              cache_position=torch.tensor([position], device=model.device))
            past, hidden = out.past_key_values, out.last_hidden_state[:, -1, :]
            position += 1
        torch.cuda.synchronize()
        total_s = time.perf_counter() - t0
        return {"prompt_tokens": len(prompt_ids), "prefill_s": round(prefill_s, 3),
                "first_block_s": round(total_s, 3), "block_frames": n,
                "block_audio_ms": n * 40}
    except Exception as exc:  # noqa: BLE001 — ölçüm başarısızsa bench durmasın
        print(f"[gecikme] ölçülemedi: {type(exc).__name__}: {exc}", flush=True)
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="*", default=None,
                    help=f"koşulacak setler ({'|'.join(SETS)}); boş = hepsi")
    ap.add_argument("--only", nargs="*", default=None, help="yalnız bu cümle id'leri")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    selected = args.sets or list(SETS)
    bad = [s for s in selected if s not in SETS]
    if bad:
        raise SystemExit(f"bilinmeyen set: {bad} (geçerli: {', '.join(SETS)})")

    torch.manual_seed(args.seed)
    model, tok, load_stats = load_model()
    codes, ref_stats = reference_codes(model)

    report: dict = {"model_id": MODEL_ID, "device": DEVICE, "seed": args.seed,
                    "gen": GEN, "yukleme": load_stats, "referans": ref_stats,
                    "setler": {}}

    for name in selected:
        use_ref = name.startswith("clone")
        kw = {"reference_codes": codes, "reference_text": REF_TEXT} if use_ref else {}
        pre = normalize_tr if name.endswith("trnorm") else (lambda t: t)

        def synth(text: str, kw=kw, pre=pre):
            wav = model.generate_speech(pre(text), tok, **kw, **GEN)
            return wav.numpy().astype(np.float32), model.config.sample_rate

        # Isıtma: ilk çağrı CUDA çekirdek derlemesi içerir, RTF'i bozar.
        t0 = time.perf_counter()
        synth("Merhaba, bu bir ısıtma cümlesidir.")
        print(f"[ısıtma/{name}] {time.perf_counter() - t0:.2f}s (ölçüm DIŞI)", flush=True)

        torch.cuda.reset_peak_memory_stats()
        lat = {
            "kisa": first_audio_latency(model, tok, "Bunu sen mi yaptın?",
                                        codes if use_ref else None),
            "orta": first_audio_latency(model, tok,
                                        "Toplantı saat 14:30'da başlıyor, lütfen geç kalma.",
                                        codes if use_ref else None),
        }
        summary = bench(f"higgs-{name}", synth, device=DEVICE, only=args.only,
                        extra={"gen": GEN, "referans": ref_stats if use_ref else None,
                               "ilk_ses_gecikmesi": lat})
        report["setler"][f"higgs-{name}"] = {
            k: summary[k] for k in
            ("count_ok", "count_fail", "total_wall_s", "total_audio_s", "mean_rtf",
             "uretim", "ilk_ses_gecikmesi")
        }

    # Rapor BİRLEŞTİRİLİR, üzerine yazılmaz: tek set koşulduğunda (örn. sadece
    # clone-trnorm) önceki setlerin ölçümleri kaybolmasın.
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "higgs_report.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        report["setler"] = (old.get("setler") or {}) | report["setler"]
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nyazıldı: {path}  ({', '.join(report['setler'])})")


if __name__ == "__main__":
    main()
