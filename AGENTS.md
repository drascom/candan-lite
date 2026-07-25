# Repository Guidelines

## Project Structure & Module Organization

`worker/` contains the Python LiveKit voice agent, STT/TTS adapters, speaker tools, and runtime helpers. Keep production logic here; `worker/tools/` is for operational utilities. `web/` is the Next.js 15 client: routes live in `web/app/`, reusable UI in `web/components/`, hooks in `web/hooks/`, and shared helpers in `web/lib/`. `pi/` holds pi.dev personas, settings, and TypeScript extensions. Use `experiments/` only for isolated benchmarks or evaluations; do not couple production code to them. Session dumps, audio assets, logs, and handoff notes are operational artifacts, not application source.

## Build, Test, and Development Commands

- `cd web && pnpm install && pnpm dev` — install frontend dependencies and start the local Next.js server.
- `cd web && pnpm build` — create a production frontend build; `pnpm lint` runs Next.js linting.
- `cd web && pnpm format:check` — verify Prettier formatting; use `pnpm format` to apply it.
- `./check.sh` — run the repository static-analysis gate: Ruff for Python and strict TypeScript checking for the family-memory extension.
- `cd worker && ./go.sh` — run the voice smoke-test flow; `./go.sh worker` starts only the worker and requires `worker/.venv` plus `worker/.env`.
- `python3 tools/dashboard.py` — serve the read-only local operations dashboard on port 8765.

## Coding Style & Naming Conventions

Python targets 3.12 and is checked with Ruff (120-character lines). Use four spaces, `snake_case` for functions/modules, `PascalCase` for classes, and type annotations at interfaces. Keep async task ownership explicit. TypeScript and TSX use two spaces, single quotes, semicolons, and a 100-character print width; Prettier also sorts imports and Tailwind classes. Name React components in `PascalCase` files and hooks as `use-*.ts`/`use*.ts` consistently with their directory.

## Testing Guidelines

There is no dedicated automated test suite yet. Before submitting a change, run `./check.sh`; for frontend changes also run `pnpm build` and relevant manual browser checks. Keep new experiments self-contained with a README describing inputs, invocation, and expected result. Do not add generated logs, recordings, model files, or session dumps unless explicitly needed.

## Commit & Pull Request Guidelines

Git history is not present in this checkout, so no repository-specific convention can be derived. Use concise imperative commit subjects, preferably scoped (for example, `worker: handle Wyoming reconnect`). PRs should explain the behavior change, list validation commands, link related issues or handoff notes, and include screenshots for visible `web/` changes. Call out configuration, credential, LiveKit, or model-download implications explicitly; never commit `.env` values or secrets.
