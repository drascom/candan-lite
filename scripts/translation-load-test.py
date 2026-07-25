#!/usr/bin/env python3
"""Load-test Candan's local translation endpoint with simultaneous users.

Every virtual user starts at the same barrier and asks the local LLM to translate
the same English sentence into all 20 target languages. The report distinguishes
accepted concurrent clients from actual latency and throughput.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any


TRANSLATOR_PATH = Path(__file__).with_name("translate-20.py")


def load_translator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("candan_translate_20", TRANSLATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Çeviri modülü yüklenemedi: {TRANSLATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def run_level(
    concurrency: int,
    *,
    sentence: str,
    translator: ModuleType,
    model: str,
    base_url: str,
    timeout: float,
    max_tokens: int,
) -> dict[str, Any]:
    barrier = threading.Barrier(concurrency + 1)

    def virtual_user(user_id: int) -> dict[str, Any]:
        barrier.wait()
        started = time.perf_counter()
        try:
            rows = translator.translate_batch(
                [sentence],
                languages=translator.LANGUAGES,
                base_url=base_url,
                model=model,
                timeout=timeout,
                api_key="",
                max_tokens=max_tokens,
            )
            elapsed = time.perf_counter() - started
            values = rows[0]["values"]
            if len(values) != len(translator.LANGUAGES):
                raise RuntimeError(f"20 yerine {len(values)} dil döndü")
            return {
                "user": user_id,
                "ok": True,
                "elapsed_seconds": round(elapsed, 3),
                "turkish": values["tr"],
            }
        except Exception as exc:  # noqa: BLE001 - each load-test failure belongs in the report
            return {
                "user": user_id,
                "ok": False,
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "error": str(exc),
            }

    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="translation-user") as executor:
        futures = [executor.submit(virtual_user, user_id) for user_id in range(1, concurrency + 1)]
        barrier.wait()
        results = [future.result() for future in as_completed(futures)]
    wall_seconds = time.perf_counter() - wall_started

    results.sort(key=lambda item: item["user"])
    successful = [item for item in results if item["ok"]]
    latencies = [float(item["elapsed_seconds"]) for item in successful]
    return {
        "concurrency": concurrency,
        "successful": len(successful),
        "failed": concurrency - len(successful),
        "wall_seconds": round(wall_seconds, 3),
        "latency_seconds": {
            "minimum": round(min(latencies), 3) if latencies else 0.0,
            "average": round(statistics.fmean(latencies), 3) if latencies else 0.0,
            "p50": round(statistics.median(latencies), 3) if latencies else 0.0,
            "p95": round(percentile(latencies, 0.95), 3),
            "maximum": round(max(latencies), 3) if latencies else 0.0,
        },
        "requests_per_second": round(len(successful) / wall_seconds, 3) if wall_seconds else 0.0,
        "translations_per_second": round(
            len(successful) * len(translator.LANGUAGES) / wall_seconds,
            3,
        ) if wall_seconds else 0.0,
        "users": results,
    }


def parse_levels(raw: str) -> list[int]:
    levels: list[int] = []
    for item in raw.split(","):
        try:
            level = int(item.strip())
        except ValueError as exc:
            raise RuntimeError(f"Geçersiz eşzamanlılık seviyesi: {item!r}") from exc
        if level < 1:
            raise RuntimeError("Eşzamanlılık seviyeleri en az 1 olmalı")
        if level not in levels:
            levels.append(level)
    if not levels:
        raise RuntimeError("En az bir eşzamanlılık seviyesi gerekli")
    return levels


def print_level(result: dict[str, Any]) -> None:
    latency = result["latency_seconds"]
    print(
        f"users={result['concurrency']:>2}  ok={result['successful']:>2}  "
        f"failed={result['failed']:>2}  wall={result['wall_seconds']:>7.3f}s  "
        f"avg={latency['average']:>7.3f}s  p50={latency['p50']:>7.3f}s  "
        f"p95={latency['p95']:>7.3f}s  req/s={result['requests_per_second']:.3f}  "
        f"translations/s={result['translations_per_second']:.3f}",
        flush=True,
    )
    for user in result["users"]:
        status = "ok" if user["ok"] else f"FAIL {user['error']}"
        print(f"  user-{user['user']:02d}: {user['elapsed_seconds']:>7.3f}s  {status}", flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sentence", default="Good morning", help="Her kullanıcının çevireceği İngilizce cümle")
    parser.add_argument(
        "--levels",
        default="1,5",
        help="Sırayla test edilecek eşzamanlı kullanıcı sayıları (varsayılan: 1,5)",
    )
    parser.add_argument("--cooldown", type=float, default=2.0, help="Seviyeler arası bekleme süresi")
    parser.add_argument("--timeout", type=float, default=300.0, help="Her kullanıcı isteği için zaman aşımı")
    parser.add_argument("--max-tokens", type=int, default=1024, help="Her kullanıcı için azami çıktı tokenı")
    parser.add_argument("--base-url", default="http://192.168.0.25:8082/v1")
    parser.add_argument("--model", default="", help="Boşsa /v1/models üzerinden keşfedilir")
    parser.add_argument("--output", type=Path, help="Ayrıntılı JSON rapor yolu")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now().astimezone()
    started_counter = time.perf_counter()
    try:
        levels = parse_levels(args.levels)
        translator = load_translator()
        model = args.model or translator.discover_model(args.base_url, timeout=args.timeout, api_key="")
        print(f"model={model}")
        print(f"sentence={args.sentence!r}")
        print(f"levels={levels}")
        level_results = []
        for index, level in enumerate(levels):
            if index and args.cooldown > 0:
                time.sleep(args.cooldown)
            result = run_level(
                level,
                sentence=args.sentence,
                translator=translator,
                model=model,
                base_url=args.base_url,
                timeout=args.timeout,
                max_tokens=args.max_tokens,
            )
            level_results.append(result)
            print_level(result)

        elapsed_seconds = time.perf_counter() - started_counter
        report = {
            "model": model,
            "sentence": args.sentence,
            "target_language_count": len(translator.LANGUAGES),
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "levels": level_results,
        }
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"report={args.output}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
