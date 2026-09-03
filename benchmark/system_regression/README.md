# RAG-value system-regression questions

This directory preserves the 64 route-oriented pending questions that were previously placed in
`benchmark/rag_value_ablation/questions_template.jsonl`. They are software-regression material,
not scientific benchmark questions and not trusted Gold.

The frozen file `rag_value_route_questions_v1.jsonl` covers controlled-English parser behavior,
route selection, exact identifier handling, filters, release scoping, and fail-closed refusals. It
intentionally retains its original synthetic fixture identifiers, including the all-`a` locus key,
because changing those bytes would no longer preserve the regression fixture.

The experiment-only `SystemRegressionQuestion` loader validates the frozen file independently of
the trusted `EvaluationQuestion` contract. Its deterministic audit checks all 64 routes and sends
the 32 structured/Hybrid clauses through the current controlled-English planner with an in-memory
resolver. It does not call a database, retriever, provider, or model.

Frozen identity:

- rows: 64;
- SHA-256: `9763b6bda2074fbc73aaf2347e9bf2d4153e3a13a5952ba8edfe623d912ebd34`;
- review state: all `pending`;
- approval and Gold: all `null`.

Do not promote this file into the scientific question manifest. The natural scientific templates
live under `benchmark/rag_value_ablation/scientific_questions_template.jsonl` and use a separate
authoring-only contract.
