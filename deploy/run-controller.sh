#!/usr/bin/env bash
# Reproducible sandbox-controller launch (no compose env file required).
#
# Required:
#   SANDBOX_TOKEN=...
# Optional:
#   BIND_IP=127.0.0.1
#   SANDBOX_RUNTIME=runc
#   SANDBOX_IMAGE=yaiguard/sandbox-py:1.0
#   CONTROLLER_NAME=deploy-controller-1
#
# Run on the sandbox host from any directory:
#   SANDBOX_TOKEN=... bash deploy/run-controller.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${SANDBOX_TOKEN:?SANDBOX_TOKEN is required}"

BIND_IP="${BIND_IP:-127.0.0.1}"
SANDBOX_RUNTIME="${SANDBOX_RUNTIME:-runc}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-yaiguard/sandbox-py:1.0}"
CONTROLLER_NAME="${CONTROLLER_NAME:-deploy-controller-1}"
DOCKER_SOCK="${DOCKER_SOCK:-/var/run/docker.sock}"
DOCKER_GID="$(stat -c '%g' "${DOCKER_SOCK}")"

docker build -f "${ROOT}/backend/Dockerfile.controller" -t yaiguard/controller:1.0 "${ROOT}"
docker rm -f "${CONTROLLER_NAME}" >/dev/null 2>&1 || true
docker run -d \
  --name "${CONTROLLER_NAME}" \
  --restart unless-stopped \
  --group-add "${DOCKER_GID}" \
  -v "${DOCKER_SOCK}:/var/run/docker.sock" \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  -p "${BIND_IP}:9000:9000" \
  -e "SANDBOX_TOKEN=${SANDBOX_TOKEN}" \
  -e "SANDBOX_RUNTIME=${SANDBOX_RUNTIME}" \
  -e "SANDBOX_IMAGE=${SANDBOX_IMAGE}" \
  yaiguard/controller:1.0

for _ in $(seq 1 30); do
  if curl -fsS -m 2 http://127.0.0.1:9000/healthz >/dev/null 2>&1; then
    echo "controller healthy: ${CONTROLLER_NAME} on ${BIND_IP}:9000"
    exit 0
  fi
  sleep 1
done
echo "controller did not become healthy" >&2
docker logs --tail 40 "${CONTROLLER_NAME}" >&2 || true
exit 1
