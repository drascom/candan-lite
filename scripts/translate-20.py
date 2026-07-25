#!/usr/bin/env python3
"""Translate English words or phrases into 20 languages with Candan's local LLM.

Examples:
    python3 scripts/translate-20.py hello "voice assistant"
    python3 scripts/translate-20.py --file english-terms.txt --format text
    printf 'good morning\nthank you\n' | python3 scripts/translate-20.py --stdin

The default endpoint is Candan's OpenAI-compatible llama-server. Override it with
``--base-url`` or ``CANDAN_LLM_BASE_URL`` when running elsewhere.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_BASE_URL = "http://192.168.0.25:8082/v1"
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


def api_url(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return f"{base}/{path.lstrip('/')}"


def request_json(
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float,
    api_key: str,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM bağlantısı kurulamadı ({url}): {exc.reason}") from exc


def discover_model(base_url: str, *, timeout: float, api_key: str) -> str:
    response = request_json(
        api_url(base_url, "models"),
        timeout=timeout,
        api_key=api_key,
    )
    models = response.get("data") or response.get("models") or []
    if not models:
        raise RuntimeError("LLM /v1/models yanıtında model bulunamadı")
    first = models[0]
    if isinstance(first, str):
        return first
    model = first.get("id") or first.get("model") or first.get("name")
    if not model:
        raise RuntimeError("LLM model adı belirlenemedi")
    return str(model)


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


def parse_json_content(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise RuntimeError(f"Beklenmeyen LLM içerik tipi: {type(content).__name__}")

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def translate_batch(
    terms: list[str],
    *,
    languages: tuple[tuple[str, str], ...],
    base_url: str,
    model: str,
    timeout: float,
    api_key: str,
    max_tokens: int,
) -> list[dict[str, Any]]:
    language_list = "\n".join(f"- {code}: {name}" for code, name in languages)
    system_prompt = (
        "You are a professional multilingual translator. Translate every supplied English source "
        "into every requested target language. Preserve meaning, tone, placeholders, punctuation, "
        "product names and formatting. Use natural, commonly understood language. Do not explain, "
        "omit, censor or add information. Return only the requested JSON."
    )
    user_prompt = (
        f"Translate each English source below into all {len(languages)} target language(s). "
        "The `source` value in each "
        "result must exactly match its input. Each `values` object must contain all listed language codes.\n\n"
        f"Target languages:\n{language_list}\n\n"
        f"English sources:\n{json.dumps(terms, ensure_ascii=False)}"
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "translations_20_languages",
                "strict": True,
                "schema": translation_schema(languages),
            },
        },
    }
    response = request_json(
        api_url(base_url, "chat/completions"),
        payload=payload,
        timeout=timeout,
        api_key=api_key,
    )
    try:
        content = response["choices"][0]["message"]["content"]
        rows = parse_json_content(content)["translations"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LLM yanıtı çözülemedi: {response}") from exc

    if len(rows) != len(terms):
        raise RuntimeError(f"LLM {len(terms)} kaynak için {len(rows)} sonuç döndürdü")
    required_codes = {code for code, _ in languages}
    for expected_source, row in zip(terms, rows):
        if row.get("source") != expected_source:
            raise RuntimeError(
                f"Kaynak sırası/değeri değişti: beklenen={expected_source!r}, dönen={row.get('source')!r}"
            )
        values = row.get("values") or {}
        missing = required_codes - set(values)
        if missing:
            raise RuntimeError(f"{expected_source!r} için eksik diller: {', '.join(sorted(missing))}")
    return rows


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


def render_text(rows: list[dict[str, Any]], languages: tuple[tuple[str, str], ...]) -> str:
    blocks = []
    for row in rows:
        lines = [f"Source: {row['source']}"]
        values = row["values"]
        lines.extend(f"  {name} ({code}): {values[code]}" for code, name in languages)
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def format_elapsed(seconds: float) -> str:
    total_milliseconds = round(seconds * 1000)
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{milliseconds:03d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("terms", nargs="*", help="İngilizce kelime veya ifadeler")
    parser.add_argument("--file", type=Path, help="Her satırda bir İngilizce kaynak bulunan UTF-8 dosya")
    parser.add_argument("--stdin", action="store_true", help="Kaynakları standart girdiden satır satır oku")
    parser.add_argument(
        "--base-url",
        default=os.getenv("CANDAN_LLM_BASE_URL", DEFAULT_BASE_URL),
        help=f"OpenAI-uyumlu LLM adresi (varsayılan: {DEFAULT_BASE_URL})",
    )
    parser.add_argument("--model", default=os.getenv("CANDAN_LLM_MODEL", ""), help="Model adı; boşsa keşfedilir")
    parser.add_argument("--api-key", default=os.getenv("CANDAN_LLM_API_KEY", ""), help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int, default=10, help="İstek başına kaynak sayısı (varsayılan: 10)")
    parser.add_argument("--max-tokens", type=int, default=8192, help="Her istek için azami çıktı tokenı")
    parser.add_argument("--timeout", type=float, default=300.0, help="HTTP zaman aşımı, saniye")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.add_argument(
        "--turkish-only",
        action="store_true",
        help="Diğer dilleri atla ve yalnızca Türkçeye çevir",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Her tamamlanan kaynağın 20 çevirisini anında terminalde göster",
    )
    parser.add_argument("--output", type=Path, help="Çıktıyı bu dosyaya yaz; yoksa terminale bas")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    started_at = datetime.now().astimezone()
    started_counter = time.perf_counter()
    try:
        if args.batch_size < 1:
            raise RuntimeError("--batch-size en az 1 olmalı")
        terms = collect_terms(args)
        languages = (("tr", "Turkish"),) if args.turkish_only else LANGUAGES
        model = args.model or discover_model(args.base_url, timeout=args.timeout, api_key=args.api_key)
        rows: list[dict[str, Any]] = []
        for offset in range(0, len(terms), args.batch_size):
            batch = terms[offset : offset + args.batch_size]
            print(
                f"[translate] {offset + 1}-{offset + len(batch)}/{len(terms)} → {model}",
                file=sys.stderr,
            )
            batch_rows = translate_batch(
                batch,
                languages=languages,
                base_url=args.base_url,
                model=model,
                timeout=args.timeout,
                api_key=args.api_key,
                max_tokens=args.max_tokens,
            )
            rows.extend(batch_rows)
            if args.live:
                print(render_text(batch_rows, languages), file=sys.stderr, end="", flush=True)

        finished_at = datetime.now().astimezone()
        elapsed_seconds = time.perf_counter() - started_counter
        timing = {
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": finished_at.isoformat(timespec="seconds"),
            "elapsed_seconds": round(elapsed_seconds, 3),
            "elapsed": format_elapsed(elapsed_seconds),
        }

        if args.format == "text":
            output = render_text(rows, languages)
        else:
            output = json.dumps(
                {
                    "model": model,
                    "source_language": "English",
                    "target_languages": [{"code": code, "name": name} for code, name in languages],
                    "translations": rows,
                    "timing": timing,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n"

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(output, encoding="utf-8")
            print(f"[translate] çıktı: {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(output)
        print("[timer]", file=sys.stderr)
        print(f"  başlangıç : {timing['started_at']}", file=sys.stderr)
        print(f"  bitiş     : {timing['finished_at']}", file=sys.stderr)
        print(f"  toplam    : {timing['elapsed']}", file=sys.stderr)
        print(f"  cümle     : {len(terms)}", file=sys.stderr)
        print(f"  çeviri    : {len(terms) * len(languages)}", file=sys.stderr)
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"HATA: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
