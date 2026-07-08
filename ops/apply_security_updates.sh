#!/usr/bin/env bash
set -Eeuo pipefail

# Applies OS package updates only when explicitly confirmed.
# Does not reboot automatically. Reboot is a separate approved-window action.

if [ "${CONFIRM_SECURITY_MAINTENANCE:-}" != "apply-updates" ]; then
  echo "REFUSED: set CONFIRM_SECURITY_MAINTENANCE=apply-updates to install updates."
  echo "Run ops/security_update_plan.sh first and use an agreed maintenance window."
  exit 2
fi

echo "=== security updates apply started: $(date -Is) ==="
echo "=== pre-update health ==="
curl -fsS http://127.0.0.1:8082/healthz || true
echo

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get upgrade -y

echo "=== post-update reboot marker ==="
if [ -f /var/run/reboot-required ]; then
  cat /var/run/reboot-required
  [ -f /var/run/reboot-required.pkgs ] && cat /var/run/reboot-required.pkgs
else
  echo "reboot not currently required"
fi

echo "=== post-update health ==="
curl -fsS http://127.0.0.1:8082/healthz || true
echo

echo "=== security updates apply finished: $(date -Is) ==="
echo "No reboot was performed by this script."
