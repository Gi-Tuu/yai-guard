# Changelog

## 2026-11-05 — v0.1.10 lease/requeue/controller stream fixes

- `recover_expired` UPDATE re-checks the expired lease and version, so a heartbeat renewal between SELECT and UPDATE cannot be overwritten as interrupted.
- Requeue reuses the existing `TASKS` state object, preserving the API's `cancel_event` across SQLite/memory re-dispatch.
- Memory mode no longer full-reaps a shared remote controller at startup without authoritative task ownership.
- Controller upload reads `request.stream()` with an incremental size cap instead of buffering an unbounded body.
- Tests: 254 (7 skipped); coverage 73%.

## 2026-11-05 — v0.1.9 startup and deployment boundary fixes

- Reaper aborts on DB read error; explicit SQLite failure fails closed.
- Runtime lock is acquired only after creating the artifacts directory, and before task recovery.
- Memory/fake/sqlite single modes now start the requeue loop; fixed `_REQUEUE_STARTED` scope error in memory+remote startup.
- Remote HTTP deadlines follow command deadlines; queue count+insert is atomic.
- Quiesce clears dispatch state and memory tasks are re-dispatched after the marker is removed.
- Signing-key load failure rejects task creation and is exposed in health; UI shows verification level.
- Claim payload now uses `claims_bound` plus `claim_semantics=binding-only`; old `claims_verified` traces remain readable by the verifier.
- Deployment entrypoints pass `SANDBOX_REQUIRE_DIGEST`/`SANDBOX_REQUIRE_RUNSC`; Docker tests are opt-in and no longer probe Docker on import.
- Tests: 252 (7 skipped); coverage 73%.

## 2026-11-05 — v0.1.8 safety remediation (previous)

- Reaper now aborts the whole pass when a task record read fails; a DB error is no longer treated as an unknown task that can delete an active container.
- Single-process runtime lock is acquired before task recovery; a second process cannot mark the first process's active tasks interrupted.
- Explicit SQLite failures fail closed in single mode; POST returns 503 instead of falling back to accepted memory tasks.
- RemoteSandbox HTTP deadlines now follow the command deadline (with transport margin); create/destroy have longer independent deadlines.
- Multi-worker queue limit check and insert share one SQLite `BEGIN IMMEDIATE` transaction.
- Quiesce clears `_DISPATCHED_QUEUED` and memory runtime has a post-quiesce requeue path.
- Invalid Ed25519 key configuration makes `/healthz` non-ok and exposes the error; UI distinguishes VERIFIED/CONTENT_VERIFIED/PENDING/INCOMPLETE.
- Claims record `claim_semantics: binding-only` and cited exit code; README documents that binding is not semantic proof.
- Docker tests require explicit `REQUIRE_DOCKER_TESTS=1`; production config guard supports `SANDBOX_REQUIRE_DIGEST/RUNSC`.
- Tests: 249 (7 skipped).
- Released: tag `v0.1.8` at `310c348`; app/controller deployed; schema v5.

## 2026-11-05 — v0.1.7 verification/create_task readability (previous)

- Split the remaining `verify_bundle_dict` blocks into terminal, sandbox/cleanup, policy/approvals, and claims/execution helpers; identity/anchor-signature helpers were already separate.
- Split `routes.create_task` into `_create_multi_task` / `_create_single_task`; validation, quiesce and rate-limit ordering unchanged.
- No route, API, DB schema, trace judgment or status-string change.
- Tests: 241 (1 skipped).
- Released: tag `v0.1.7` at `08345a5`; app deployed; controller unchanged at `486a1f1`.

## 2026-11-05 — v0.1.6 docs, coverage, external evidence and structural split (previous)

- README now matches schema v5 and documents `TASK_DATA_KEY`, `/metrics`, `--quiesce`, and `rotate_data_key.py`.
- CI unit job runs `coverage run --source=backend/app` and prints a per-file report (local ~75%, CI ~73%, no arbitrary threshold).
- Added `benchmarks/external/` with 12 real PRs from GitHub Copilot and OpenHands agent accounts plus check-run evidence; synthetic set remains a separate regression.
- Split task schema/migrations into `tasks/schema.py` and global quotas into `tasks/quotas.py`; split bundle identity/anchor helpers and trace status mapping helpers. No route/API/behavior change.
- Tests: 241 (1 skipped).

## 2026-11-05 — v0.1.5 responsive UI (previous)

- Fixed overlapping panels at narrow widths and short viewports by switching the grid to document flow below 980px or 720px height.
- Added a single-column mobile layout, wrapping header/panel headings, full-width token input, and bounded grow-panel heights.
- Verified at 1600x900, 1200x600, 1024x768, 900x700, 768x900, 700x700, 390x844, and 320x568 with no horizontal overflow.
- Released: tag `v0.1.5` at `1cc804a`; app deployed; controller unchanged at `486a1f1`.

## 2026-11-05 — v0.1.4 hardening boundaries (previous)

- Windows trace writers now use a lock file + msvcrt byte lock in addition to stale seq/hash checks.
- Controller `/healthz` is public liveness only; detailed runtime/image status moved to token-protected `/v1/status` and cached.
- Added authenticated Prometheus-text `/metrics`.
- Added key rotation CLI and offline key backup procedure; encrypted task data can be re-encrypted without schema changes.
- Added operator quiesce protocol: marker file blocks new tasks/claims/retention, backup `--quiesce` waits for active tasks and removes the marker.
- Added `scripts/soak_test.py` and measured a 20-task soak (P95 23.96s).
- Tests: 240 (1 skipped).
- Released: annotated tag `v0.1.4` at `486a1f1`; app/controller deployed; schema v5; health split, metrics, quiesce and Windows trace lock enabled.

## 2026-11-05 — v0.1.3 second-pass audit remediation (previous)

- Recovery now reconciles a `task.completed + sandbox.destroy.unverified` trace as failed, not completed.
- Controller `/healthz` has a short readiness cache to avoid public-probe Docker amplification; controller create is serialized per task_id.
- Task prompt/summary/error can be encrypted at rest with `TASK_DATA_KEY`; legacy plaintext remains readable.
- `TaskService` degraded recovery probes a write transaction, not only a read.
- `reconcile_terminal` uses CAS; state pruner runs in the background; schema v5 adds rate/SSE lease indexes.
- SSE and backup consistency fixes retained; redaction handles exact `PASSWORD`/`PASSWD`/`PWD`/`CREDENTIAL` forms and quoted values.
- Tests: 235 (1 skipped). Docker 6/6; controller 5/5.
- Released: annotated tag `v0.1.3` at `3ecff2b`; app/controller deployed;
  schema v5; `TASK_DATA_KEY` configured; encrypted prompt verified in SQLite.

## 2026-11-05 — v0.1.2 audit remediation (previous)

- Task creation now fails with HTTP 503 instead of returning a fake `queued`
  task when the durable store rejects the write.
- Cleanup-unverified completions are treated as failed evidence: bundle status
  is `failed`, verification is `INCOMPLETE`, and `/trace/verify` completeness
  no longer rejects the completed event that preceded the failed cleanup.
- Trace writers are fenced with a file lock plus stale seq/hash detection; a
  recovery writer and a paused owner can no longer interleave two writers into
  one trace.
- Controller `/healthz` re-checks Docker/runtime/image on every probe instead of
  returning a boot-time snapshot.
- `TaskService` clears a sticky degraded flag after a successful store probe.
- Global rate limiting falls back to the in-process limiter when the SQLite
  quota call fails, and expired `rate_hits` rows are removed globally.
- Controller orphan reaping only reports verified removals; its put-file body
  is bounded.
- Task transitions now pass CAS `expected_version` on waiting/resume/finish;
  lease values use `TASK_LEASE_SECONDS` on claim and resume.
- Backup snapshots now reject DB/trace changes during the copy window; restore
  removes stale traces when the archive has none. `--allow-active` keeps the
  old permissive behavior for an intentional online snapshot.
- SSE terminates instead of spinning when the task row has been pruned.
- Interrupted evidence preserves `lease_expired` instead of relabeling every
  interruption as `app_restart`.
- Tests: 229 (1 skipped); Docker 6/6; controller integration 5/5.
- Released: annotated tag `v0.1.2` at `a479fec`; app and sandbox controller
  deployed at `a479fec`; `/healthz` reports `0.1.2`; SQLite schema v4.

## 2026-11-05 — internal split (no behavior change)

- Split the 1270-line `backend/app/main.py` into `settings.py`, `security.py`,
  `task_state.py`, `task_runner.py`, `maintenance.py`, `routes.py`, and a thin
  app assembly entrypoint.
- `main.py` is now 22 lines; new module sizes: settings 107, security 114,
  task_state 121, task_runner 337, maintenance 340, routes 374.
- `run_task` is a 8-line orchestrator over prepare/start/trace/execute/failure/
  finalize steps.
- Route paths, API/status strings, DB schema, health payload, trace/evidence
  verification and security semantics are unchanged. Route-table comparison
  before/after is empty.
- Tests: 216 (1 skipped); Docker 6/6; controller integration 5/5.

## 2026-11-04 — v0.1.1

### Fixed

- Evidence bundle: optional empty/missing claims (`claims_required=false`) are
  now "not applicable" instead of `claim_evidence_mismatch`; required empty
  claims still fail. Code commit: `89f80c5`.
- Trace verification: `waiting_approval` is reported as `PENDING` like
  `queued`/`running`, instead of `FAILED` for a missing anchor.
- Docker cleanup: `destroy()` retries the `rm -f`/`inspect` race for a bounded
  short interval and only reports `verified=true` after inspect confirms the
  container is absent; a container still visible to inspect is never verified.
- Trace verification without HMAC/Ed25519 now returns `CONTENT_VERIFIED`
  (content checked, no signature) instead of `VERIFIED`; `/trace/verify`,
  `/evidence` and `verify_bundle` agree. Signed verification still returns
  `VERIFIED`.
- Tests: 216 (1 skipped by design); Docker layer 6/6; controller integration
  20/20 consecutive runs; true two-process multi-worker and worker-kill recovery
  tests added.
- CI: added a Linux `gvisor runsc integration` job; containment assertions are
  runtime-aware for runsc (pids-limit termination and `socket.if_nameindex`).
- Online evidence: `scripts/online_smoke.py` (Caddy TLS, 20-task short soak,
  SSE) and a v0.1.1 worker-kill chaos drill (interrupted/lease_expired, trace
  VERIFIED, container cleaned).
- External-control corpus: `benchmarks/claim_evidence_cases.json` and
  `scripts/claim_evidence_benchmark.py`; 8 rejection cases, claim-evidence
  recall 1.0, summary-only recall 0.0. This is a fixed control corpus, not a
  third-party model benchmark.
- Fix commit: `89f80c5`; merged on main as `809ba3c`.
- Deployed: app and sandbox controller both at `ecb34fa`; production `/healthz`
  reports `0.1.1`, `runtime=runsc`, task store schema v4.
- Tag: annotated `v0.1.1` at `ecb34fa`.

### Documentation

- `docs/13-测试与证据报告.md` records the fix commit, local-reproduction gaps
  (gVisor, Vultr model, true multi-process, Caddy, chaos/load) and a fixed
  synthetic claim–evidence contrast set.
- True two-process multi-worker test (`test_multi_worker_process.py`).
- README status table clarifies `PENDING` covers `waiting_approval`, and adds
  the GitHub Issues entry.

### Release

- Version: `0.1.1` (API `/healthz` reports `0.1.1`).




