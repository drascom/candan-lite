# Repository Guidelines

## Codebase memory — first stop for code work

Before any significant code change or code-scanning task, query the codebase-memory MCP
tools FIRST: `search_graph` (find functions/classes/routes), `trace_path` (call chains),
`get_code_snippet` (exact symbol source), `get_architecture`, `search_code`. Fall back to
Grep/Glob/Read only after that. If the repo is not indexed yet, run `index_repository` first.

## Layout rules

Production logic lives in `worker/`; `worker/tools/` is for operational utilities only.
`experiments/` is for isolated benchmarks and evaluations — never couple production code to
it, and give each experiment a README describing inputs, invocation, and expected result.
`sessions/`, `logs/`, `asset/`, `assets/`, and `handoff/` are operational artifacts, not
application source.

## Commands that are not guessable

- `./check.sh` — the static-analysis gate: Ruff over `worker/`, `tsc --strict` over
  `pi/extensions/family-memory`. There is **no CI and no pre-commit hook** — this is run by
  hand and it is the only gate. Run it before submitting any change.
- `cd worker && ./go.sh` — voice smoke-test flow. `./go.sh worker` starts only the worker and
  requires `worker/.venv` plus `worker/.env`.
- `python3 tools/dashboard.py` — read-only local operations dashboard on port 8765.

Frontend commands are the standard pnpm scripts in `web/package.json`.

## Gotchas

- Keep async task ownership explicit — a dropped `asyncio` task reference (Ruff `RUF006`) has
  already caused a live bug. `ruff.toml` documents the `worker/agent.py` `UnboundLocalError`
  that this gate exists to prevent.
- Style is enforced mechanically by Ruff (`ruff.toml`) and Prettier (`web/.prettierrc`) — do
  not hand-maintain style rules in this file. Note `E501` is deliberately ignored, so there is
  no enforced line-length limit in Python.

## Testing

There is no automated test suite. Run `./check.sh` for every change; for `web/` changes also
run `pnpm build` plus a manual browser check. Do not commit generated logs, recordings, model
files, or session dumps unless explicitly needed.

## Commits & PRs

Commit subjects are Turkish, imperative, and scope-prefixed (`worker: handle Wyoming
reconnect`, `vurgu: kelime sonrasi bekleme kesildi`). PRs should explain the behavior change,
list the validation commands run, link related handoff notes, and include screenshots for
visible `web/` changes. Call out configuration, credential, LiveKit, or model-download
implications explicitly. **Never commit `.env` values or secrets.**
