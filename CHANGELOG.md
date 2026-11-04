# Changelog

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

