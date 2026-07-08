#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only maintenance helper. It does not install packages and does not reboot.
# Use this before agreeing a maintenance window.

echo "=== security update plan: $(date -Is) ==="
echo "=== host ==="
hostnamectl || true

echo "=== uptime ==="
uptime || true

echo "=== service health before planning ==="
curl -fsS http://127.0.0.1:8082/healthz || true
echo

echo "=== apt update metadata ==="
apt-get update -qq

echo "=== upgradable packages ==="
apt list --upgradable 2>/dev/null || true

echo "=== security-related upgradable packages ==="
apt list --upgradable 2>/dev/null | grep -Ei 'security|ubuntu[0-9.]+-[a-z-]+security' || true

echo "=== reboot-required marker ==="
if [ -f /var/run/reboot-required ]; then
  cat /var/run/reboot-required
  if [ -f /var/run/reboot-required.pkgs ]; then
    echo "=== packages requiring reboot ==="
    cat /var/run/reboot-required.pkgs
  fi
else
  echo "reboot not currently required"
fi

echo "=== plan complete: no packages were installed, no services were restarted ==="
