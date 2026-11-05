# External claim-evidence sample (12 real agent PRs)

This directory is deliberately separate from the synthetic regression set
`benchmarks/claim_evidence_cases.json`.

## What is recorded

For each case, `cases.json` stores:

- the public PR URL, repository, agent account and collection date;
- the PR title/body as the **claim** (what the agent said it did);
- the GitHub **check-run conclusions** on the PR head SHA as the **evidence**;
- `should_accept = true` only when at least one check was observed and no check
  failed or was cancelled.

The sample was collected on **2026-11-05** from two independent coding-agent
accounts:

| Source | Cases |
|---|---:|
| GitHub Copilot coding agent (`Copilot`) | 6 |
| OpenHands agent (`openhands-agent`) | 6 |

Ten cases have only successful/skipped/neutral checks; two have at least one
failure or cancellation. The two failure cases are real merged PRs, so they are
exactly the “summary says done, execution evidence says otherwise” pattern this
project is designed to expose.

## What this is not

- It is not a random or statistically representative sample.
- CI success is not semantic correctness; a passing check can still ship a bad
  fix, and a cancelled check is not necessarily a failed task.
- PR titles/bodies are treated as the agent summary, but the body may be edited
  by a human after the agent wrote it.
- The sample tests the **claim → evidence policy**, not model quality, sandbox
  isolation, or YAI Guard's own runtime trace.
- Results here must not be generalized into a claim of universal advantage.

## Reproduce

```bash
python scripts/claim_evidence_external.py \
  --cases benchmarks/external/cases.json
```

The synthetic regression set remains the place for exhaustive mechanism cases:

```bash
python scripts/claim_evidence_benchmark.py
```
