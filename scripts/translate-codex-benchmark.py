#!/usr/bin/env python3
"""Benchmark translations through Codex CLI using the saved ChatGPT login.

This does not read or copy Codex authentication tokens. ``codex exec`` reuses
the login maintained by the official CLI. Runs are ephemeral and read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gpt-5.6-terra"
LANGUAGES = (
    ("es", "Spanish"),
    ("zh-CN", "Mandarin Chinese (Simplified)"),
    ("hi", "Hindi"),
    ("ar", "Arabic"),
    ("pt", "Portuguese"),
    ("fr", "French"),
    ("de", "German"),
    ("ja", "Japanese"),
    ("ru", "Russian"),
    ("ko", "Korean"),
    ("id", "Indonesian"),
    ("tr", "Turkish"),
    ("it", "Italian"),
    ("nl", "Dutch"),
    ("pl", "Polish"),
    ("vi", "Vietnamese"),
    ("th", "Thai"),
    ("fa", "Persian"),
    ("ur", "Urdu"),
    ("bn", "Bengali"),
)


def translation_schema(languages: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    value_properties = {code: {"type": "string"} for code, _ in languages}
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "values": {
                            "type": "object",
                            "properties": value_properties,
                            "required": list(value_properties),
                            "additionalProperties": False,
                        },
                    },
                    "required": ["source", "values"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    }


def collect_terms(args: argparse.Namespace) -> list[str]:
    terms = list(args.terms)
    if args.file:
        terms.extend(args.file.read_text(encoding="utf-8").splitlines())
    if args.stdin:
        terms.extend(sys.stdin.read().splitlines())

    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        clean = term.strip()
        if clean and clean not in seen:
            result.append(clean)
            seen.add(clean)
    if not result:
        raise RuntimeError("Çevrilecek metin yok. Argüman, --file veya --stdin kullanın.")
    return result


def check_codex_login(codex_bin: str) -> str:
    result = subprocess.run(
        [codex_bin, "login", "status"],
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    status = (result.stdout or result.stderr).strip()
    if result.returncode != 0 or not status:
        raise RuntimeError(f"Codex oturumu doğrulanamadı: {status or 'bilinmeyen hata'}")
    return status


def build_prompt(terms: list[str], languages: tuple[tuple[str, str], ...]) -> str:
    language_list = "\n".join(f"- {code}: {name}" for code, name in languages)
    return (
        "This is a pure translation benchmark. Do not use tools, shell commands, web search, files, "
        "or external sources. Translate every supplied English source into every target language. "
        "Preserve meaning, tone, placeholders, punctuation, product names, and formatting. Use natural, "
        "commonly understood language. Do not explain, omit, censor, or add information. The `source` "
        "value in each result must exactly match its input and results must remain in input order.\n\n"
        f"Target languages:\n{language_list}\n\n"
        f"English sources:\n{json.dumps(terms, ensure_ascii=False)}"
    )


def parse_usage(events: str) -> dict[str, int]:
    usage: dict[str, int] = {}
    for line in events.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        for key, value in event["usage"].items():
            if isinstance(value, int):
                usage[key] = value
    return usage


def run_codex_batch(
    terms: list[str],
    *,
    languages: tuple[tuple[str, str], ...],
    codex_bin: str,
    model: str,
    reasoning: str,
    timeout: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    started_at = datetime.now().astimezone()
    started_counter = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="candan-codex-translate-") as temporary:
        temp_dir = Path(temporary)
        schema_path = temp_dir / "schema.json"
        result_path = temp_dir / "result.json"
        schema_path.write_text(
            json.dumps(translation_schema(languages), ensure_ascii=False),
            encoding="utf-8",
        )
        command = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
            "--model",
            model,
            "--config",
            f'model_reasoning_effort="{reasoning}"',
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(result_path),
            "-",
        ]
        try:
            process = subprocess.run(
                command,
                input=build_prompt(terms, languages),
                text=True,
                capture_output=True,
                cwd=temp_dir,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"Codex isteği {timeout:.0f} saniyede tamamlanmadı") from exc

        if process.returncode != 0:
            details = process.stderr.strip() or process.stdout.strip() or "bilinmeyen hata"
            raise RuntimeError(f"codex exec başarısız (kod {process.returncode}): {details}")
        if not result_path.is_file():
            raise RuntimeError("codex exec sonuç dosyası oluşturmadı")
        try:
            response = json.loads(result_path.read_text(encoding="utf-8"))
            rows = response["translations"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError(f"Codex JSON yanıtı çözülemedi: {result_path.read_text(encoding='utf-8')}") from exc

    if len(rows) != len(terms):
        raise RuntimeError(f"Codex {len(terms)} kaynak için {len(rows)} sonuç döndürdü")
    required_codes = {code for code, _ in languages}
    for expected_source, row in zip(terms, rows):
        if row.get("source") != expected_source:
            raise RuntimeError(
                f"Kaynak sırası/değeri değişti: beklenen={expected_source!r}, dönen={row.get('source')!r}"
            )
        missing = required_codes - set(row.get("values") or {})
        if missing:
            raise RuntimeError(f"{expected_source!r} için eksik diller: {', '.join(sorted(missing))}")

    finished_at = datetime.now().astimezone()
    elapsed_seconds = time.perf_counter() - started_counter
    return rows, {
        "started_at": started_at.isoformat(timespec="seconds"),
        "finished_at": finished_at.isoformat(timespec="seconds"),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "usage": parse_usage(process.stdout),
    }


def render_text(rows: list[dict[str, Any]], languages: tuple[tuple[str, str], ...]) -> str:
    blocks = []
    for row in rows:
        lines = [f"Source: {row['source']}"]
        lines.extend(
            f"  {name} ({code}): {row['values'][code]}"
            for code, name in languages
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def format_elapsed(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def sum_usage(batch_metrics: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for metric in batch_metrics:
        for key, value in metric.get("usage", {}).items():
            totals[key] = totals.get(key, 0) + int(value)
    return totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="*", help="İngilizce kelime veya ifadeler")
    parser.add_argument("--file", type=Path, help="Her satırda bir İngilizce kaynak bulunan UTF-8 dosya")
    parser.add_argument("--stdin", action="store_true", help="Kaynakları standart girdiden satır satır oku")
    parser.add_argument("--model", default=os.getenv("CANDAN_CODEX_MODEL", DEFAULT_MODEL))
    parser.add_argument("--reasoning", choices=("minimal", "low", "medium", "high"), default="low")
    parser.add_argument("--batch-size", type=int, default=1, help="Codex isteği başına kaynak sayısı")
    parser.add_argument("--timeout", type=float, default=300.0, help="Her Codex isteği için zaman aşımı")
    parser.add_argument("--turkish-only", action="store_true", help="Yalnızca Türkçeye çevir")
    parser.add_argument("--live", action="store_true", help="Tamamlanan çevirileri anında göster")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument("--output", type=Path, help="Toplu sonucu bu dosyaya yaz")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now().astimezone()
    started_counter = time.perf_counter()
    try:
        if args.batch_size < 1:
            raise RuntimeError("--batch-size en az 1 olmalı")
        codex_bin = shutil.which("codex")
        if not codex_bin:
            raise RuntimeError("codex CLI bulunamadı")
        auth_status = check_codex_login(codex_bin)
        terms = collect_terms(args)
        languages = (("tr", "Turkish"),) if args.turkish_only else LANGUAGES
        rows: list[dict[str, Any]] = []
        batch_metrics: list[dict[str, Any]] = []

        print(f"[codex] {auth_status}", file=sys.stderr)
        print(f"[codex] model={args.model}, reasoning={args.reasoning}", file=sys.stderr)
        for offset in range(0, len(terms), args.batch_size):
            batch = terms[offset : offset + args.batch_size]
            print(f"[codex] {offset + 1}-{offset + len(batch)}/{len(terms)}", file=sys.stderr, flush=True)
            batch_rows, metric = run_codex_batch(
                batch,
                languages=languages,
                codex_bin=codex_bin,
                model=args.model,
                reasoning=args.reasoning,
                timeout=args.timeout,
            )
            rows.extend(batch_rows)
            batch_metrics.append(metric)
            if args.live:
                print(render_text(batch_rows, languages), file=sys.stderr, end="", flush=True)
                print(f"  batch süresi: {format_elapsed(metric['elapsed_seconds'])}\n", file=sys.stderr, flush=True)

        finished_at = datetime.now().astimezone()
        elapsed_seconds = time.perf_counter() - started_counter
        timing = {
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "elapsed": format_elapsed(elapsed_seconds),
            "batches": batch_metrics,
        }
        result = {
            "provider": "Codex CLI with saved ChatGPT authentication",
            "authentication": auth_status,
            "model": args.model,
            "reasoning_effort": args.reasoning,
            "source_language": "English",
            "target_languages": [{"code": code, "name": name} for code, name in languages],
            "translations": rows,
            "timing": timing,
            "usage": sum_usage(batch_metrics),
        }
        output = render_text(rows, languages) if args.format == "text" else json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(f"[codex] çıktı: {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(output)

        print("[timer]", file=sys.stderr)
        print(f"  başlangıç : {timing['started_at']}", file=sys.stderr)
        print(f"  bitiş     : {timing['finished_at']}", file=sys.stderr)
        print(f"  toplam    : {timing['elapsed']}", file=sys.stderr)
        print(f"  cümle     : {len(terms)}", file=sys.stderr)
        print(f"  çeviri    : {len(terms) * len(languages)}", file=sys.stderr)
        if result["usage"]:
            print(f"  token     : {json.dumps(result['usage'], ensure_ascii=False)}", file=sys.stderr)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
