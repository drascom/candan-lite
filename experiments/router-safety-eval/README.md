# Router safety evaluation

Independent evaluation of the production low-tier router boundary.  Nothing in
`experiments/router-bench/`, `worker/`, or the running service is modified.

The main comparison is a 2×2 design:

| | Production catalogue | Negative-scoped catalogue |
|---|---|---|
| Existing schema | `baseline` | `negscope` |
| Unsupported flag | `flag2` | `combo` |

`prompt_only` is a diagnostic control that separates the stronger boundary
instruction from the new boolean field.  `flag2_short` is available for pilot
comparisons but is not included in the default full run.

Adaptive follow-up conditions (`ordered`, `orthogonal`, and `reference_flag2`,
with their negative-scope combinations) are retained to explain primary-run
failures.  They are diagnostics, not part of the default 2×2 run.  See
`FINAL_REPORT.md` for the completed evaluation and decision.

Design properties:

- Gold calls are validated against the 23 tools actually visible to the router.
- High-tier actions are fallback cases, never false tool-recall failures.
- Named semantic neighbours and unseen holdout neighbours are scored separately.
- Chat, knowledge, and ambiguous context must fall back without claiming the
  action itself is unsupported.
- Supported multi-intent and mixed supported/unsupported multi-intent are both
  covered.
- Critical trap and multi cases receive an extra deterministic run; headline
  metrics use trial 0 to avoid weighting them twice.
- The production catalogue hash, prompt sizes, llama timings, raw outputs, and
  contradictory `tool + unsupported=true` outputs are retained.

Run against the already-running llama-server (requests only):

```bash
python3 experiments/router-safety-eval/bench.py \
  --base-url http://192.168.0.25:8080 \
  --critical-repeats 1
```

Optional prompt pilot on just semantic-neighbour cases:

```bash
python3 experiments/router-safety-eval/bench.py \
  --conditions baseline,flag2_short,flag2 \
  --languages en,tr \
  --only-categories trap_named,trap_holdout,supported \
  --critical-repeats 1 \
  --out experiments/router-safety-eval/results/pilot.jsonl
```

Analyse a completed run:

```bash
python3 experiments/router-safety-eval/analyze.py \
  experiments/router-safety-eval/results/run-TIMESTAMP.jsonl
```

The runner uses the production-critical settings `temperature=0`,
`repeat_penalty=1.1`, `repeat_last_n=64`, grammar-constrained JSON, and only the
low-tier catalogue.  It does not restart or reconfigure the server.
