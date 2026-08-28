# V0 benchmark report

> Deterministic projection of `benchmark/v0_benchmark_report.json`.

## Qualification boundary

- Engineering benchmarks passed: `true`
- Real hybrid activation qualified: `false`
- Human semantic-support review: `not_run` (blocking)

Mechanical citation, quote, and identifier checks do not establish semantic entailment or biological truth.

## Frozen suites

| Suite | Tier | Cases | Result | Canonical identity |
|---|---|---:|---|---|
| `m2-structured-gold` | `tests_only_synthetic` | 31 | passed | `defined in tests` |
| `m3-deterministic-literature` | `tests_only_synthetic` | 5 | passed | `defined in tests` |
| `m3-pinned-model-pilot` | `approved_real_corpus_local_model` | 13 | passed | `894dc74002c27e3f2cdf6a47970041d88cb91a8625ec8fad8f00f6c87d7c2565` |
| `m4-router` | `tests_only_mechanical` | 30 | passed | `ad4142226ec986efec6dc26ee8125e679b12489d5322ec797e0acfd7fd66e356` |
| `m4-generation` | `tests_only_mechanical` | 14 | passed | `538294e55050d9f1d2a56949849878d94cf5383e1c1049785f219c49c8e20cfa` |

## M3 pinned-model pilot

Exact corpus: `corpus:endoviho-rag:v0:20260828:001`. Recall@5 was `0.846153846154` against `>=0.800000000000`; Recall@10 was `1.000000000000` against `>=0.900000000000`. Citation-ID and locator validity were both `1.0`.

These metrics describe one fixed approved pilot corpus and pinned local embedding model, not the full virology literature.

## Tracked benchmark sources

- `tests/benchmark/gold_cases.py` — file SHA-256 `ccbc4261801ef912e11d3456548307098b4bea8263145c601df9ffac55a9cc83`
- `tests/fixtures/literature/synthetic_benchmark.json` — file SHA-256 `3e6b226925e6396e738ea5b3ef61909051a36e25123eada82a36696ce0ab7b4b`
- `tests/fixtures/m4/router_cases.json` — file SHA-256 `dd3607316129f7e52596f2d9152edb04a20bc34ee07f5617e2682631b638209c`
- `tests/fixtures/m4/generation_cases.json` — file SHA-256 `3b81ba6d06872d400a0b457f0554f2d2739212e6a58bc9d9536b0c031cf560ab`

## Local M5 verification

Status: `passed` on `2026-08-28`.

- Full suite: `724 passed`, `1 warning`.
- Frozen benchmark selection: `72 passed`.
- Static gates: Ruff passed; strict mypy passed over `84` source files; `114` packages are locked.
- Migration gates: head `0010_m3_lock_hardening`, no model drift, and clean-history replay passed.
- Distribution gates: wheel `89` members; sdist `129` members; package audit passed.
- Container gates: fresh-volume startup, Demo-to-API wiring, fail-closed responses, and isolated cleanup passed.

## Limitations

- M2 and M4 successes use tests-only synthetic capabilities.
- The M3 metrics describe one fixed approved pilot corpus and pinned local model.
- Mechanical citation and identifier checks do not establish semantic entailment.
- No production LLM, real hybrid binding, or human semantic-support benchmark is approved.

Report SHA-256: `a6c10d862f7b2b364eec74572143fca1c99ad307c6856399d2a2cbdd3a8be144`
