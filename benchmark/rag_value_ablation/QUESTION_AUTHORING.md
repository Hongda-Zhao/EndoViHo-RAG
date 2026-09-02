# RAG-value candidate-question authoring

This directory contains authoring inputs, not benchmark results. The candidate set is deliberately
unapproved: every row has `review_status="pending"`, `approval=null`, and `gold=null`. No model,
retriever, lexical heuristic, or Codex output has supplied a gold label or oracle selection.

## Candidate set

`questions_template.jsonl` is both the 64-question candidate worksheet and the blank Gold
annotation template. It contains 16 questions in each required family:

| Family | Candidates | Route checked | Controlled-English parser checked |
|---|---:|---:|---:|
| structured | 16 | 16 | 16 |
| literature | 16 | 16 | not applicable |
| hybrid | 16 | 16 | 16 structured clauses |
| unsupported | 16 | 16 refusal routes | not applicable |

Candidate wording was derived from `README.md`, `docs/data_semantics.md`, the controlled-English
parser and router tests, the scope policy, and supported `QueryPlan` intents. Each JSONL row records
its wording sources, intended route, intended structured intent where applicable, evaluation focus,
and data-semantics boundary codes. `candidate_question_audit.json` records the deterministic
balance, duplicate/focus, route, semantic-boundary, parser, and annotation-emptiness checks.

The parser audit uses a synthetic resolver profile solely to prove grammar acceptance. Questions
marked `uses_fixture_entities=true` contain a synthetic assembly, locus, or lineage resolver entry.
Those entities must be replaced with values from the approved DatasetRelease and the questions
must be parsed again before a human can approve them. Parser acceptance is not scientific approval.

## Blank annotation templates

- `questions_template.jsonl` leaves each discriminated Gold payload empty (`gold=null`). A human
  annotator must supply the correct family-specific Gold object, approval identity, timestamp, and
  attestation before changing a row to `approved`.
- `oracle_annotations_template.jsonl` contains one checksum-bound S6 row per candidate. Every row
  is pending and contains no evidence disposition, structured facts, literature chunks, release
  identity, source attestation, or approval.
- `question_schema.json` and `oracle_annotation_schema.json` define the exact machine contracts for
  completing those templates.

Do not populate Gold from current retrieval output, lexical overlap, or model answers. Oracle
evidence must be selected manually and reviewed independently. Only fully reviewed questions may
enter a trusted benchmark manifest; the existing admission gate still requires 60–80 approved
questions and 15–20 approved questions per family.
