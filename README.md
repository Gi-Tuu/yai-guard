# YAI Guard — Prove-It Code Runner

**Verifiable execution for coding agents — plan, gate, execute, prove.**

YAI Guard turns a coding task into real terminal/file operations, but every execution is confined to a one-shot, hardened container on a separate sandbox host. The application process never holds a Docker socket; a deterministic policy engine gates every tool call; and every event is written to a SHA-256 hash-chained trace that can be verified offline.

**Live demo:** https://167-179-65-59.sslip.io

---

## Use case: prove what a coding agent actually ran

A coding agent says “fixed” or “tests pass”. YAI Guard makes that claim checkable instead of trusting the agent's summary or a vendor dashboard:

1. run the task in a disposable container;
2. record the real argv, exit code, output hashes, policy decisions, approval and cleanup;
3. export an evidence bundle that a reviewer can re-verify offline.

The reviewer does not need the original API, the model, or the agent process to check the result.

---

## Why

Autonomous agents are useful only when they can *do* things. Doing things safely requires more than prompt instructions:

- execution must never happen in the application process;
- the model must never receive host credentials or a network path to the host;
- every decision and every side effect must be auditable;
- a task must leave no reusable environment behind.

YAI Guard is built around those constraints.

---

## Architecture

```
Browser
  │ HTTPS
  ▼
App host ── FastAPI / policy / trace / approval UI
  │ encrypted channel
  ▼
Sandbox host ── controller ── Docker
                              └── one-shot sandbox container
                                  network=none
                                  read-only rootfs
                                  non-root uid 65534
                                  cap-drop ALL
                                  pids / memory / CPU / wall-clock limits
```

- **App host:** serves the web UI and API, holds the inference key, runs the agent loop, policy engine and trace store. No Docker, no containers.
- **Sandbox host:** runs the controller and the Docker daemon. The controller only listens on loopback; the app reaches it through an encrypted channel.
- **Sandbox container:** created per task, destroyed after the task, with a read-only root filesystem, no network and resource caps.

---

## Security properties

| Property | Implementation |
|---|---|
| No host execution in the app process | Execution is only reachable through the sandbox controller |
| No Docker socket in the app | Controller and daemon live on the sandbox host |
| Deterministic policy | Rules return allow / deny / require-approval with a stable rule id |
| Human approval | Approval is a strict JSON boolean; duplicates and cross-owner access are rejected |
| Bounded execution | Per-stream and total output limits; timeout/output overflow kills the process group and container |
| Verified cleanup | Destroy is confirmed with `docker inspect`; failed cleanup is recorded as unverified, never successful |
| Tamper-evident audit | SHA-256 hash chain + anchor; strict verification refuses to report VERIFIED when evidence is missing |
| Offline evidence | A downloadable evidence bundle contains the trace and anchor; an independent CLI re-verifies it |

---

## Quickstart

```bash
# tests (Python 3.12+)
pip install -r backend/requirements-dev.txt
python -m unittest discover -s backend/tests -t backend -v

# offline end-to-end demo: mock model + fake sandbox + real policy + real trace
python backend/demo_offline.py

# static check: no host execution primitives outside the sandbox layer
python scripts/check_no_host_exec.py

# real Docker containment (optional)
docker build -f deploy/sandbox/Dockerfile -t yaiguard/sandbox-py:1.0 .
python -m unittest discover -s backend/tests -t backend -p "test_docker_backend.py" -v

# local web UI (mock model, anonymous mode for development)
cd backend && ALLOW_ANONYMOUS=1 MOCK_MODEL=1 SANDBOX_DRIVER=fake uvicorn app.main:app --port 8000
```

---

## Repository layout

```
backend/        FastAPI app, agent loop, policy engine, trace, sandbox clients, controller
deploy/         Dockerfiles, compose files, Caddy config
web/            dependency-free HTML/CSS/JS UI
backend/tests/  unit, API-security, Docker and controller integration tests
docs/           engineering notes and evidence
```

---

## Documentation

- [Dual-host deployment record](docs/17-双VM部署记录.md)
- [Security hardening report](docs/12-安全加固修复报告.md)
- [Test and evidence report](docs/13-测试与证据报告.md)
- [Evidence samples](docs/evidence/)

Verify a downloaded evidence bundle:

```bash
# content consistency (chain, metadata, execution, cleanup, anchor)
python -m app.trace.verify_bundle path/to/evidence.json

# additionally require the anchor HMAC signature
python -m app.trace.verify_bundle path/to/evidence.json --require-signature
```

Status meanings:

| Status | Meaning |
|---|---|
| `VERIFIED` | Content checks pass and the anchor signature is verified |
| `CONTENT_VERIFIED` | Content checks pass; signature was not verified |
| `INCOMPLETE` | Required evidence (anchor, cleanup, sandbox or execution metadata) is missing |
| `FAILED` | Hash chain, metadata, final hash, anchor or signature mismatch |
| `PENDING` | Task is still running |

The verifier does not trust bundle summary fields. It recomputes the hash
chain, verifies that `container_id`, `image_id`, `runtime`, execution records,
cleanup state and final hash match the raw events, and checks the anchor HMAC
when a key is supplied.

## Containment Lab

`POST /api/containment/run` executes four direct sandbox probes:

| Probe | Expected result |
|---|---|
| `read_only_root` | write to `/etc/hostname` is denied |
| `network_none` | outbound connection is unreachable |
| `no_docker_socket` | `/var/run/docker.sock` is absent |
| `no_secrets` | no inference, sandbox or demo token in the container env |

The Lab reports per-probe exit codes and confirmed cleanup, without involving
the policy engine or model.

---

## License

MIT. See [LICENSE](LICENSE).
