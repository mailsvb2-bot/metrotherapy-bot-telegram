# Autodeploy smoke marker

Purpose: safe no-op repository change used to exercise the production GitHub webhook and hardened deploy pipeline.

Triggered at: 2026-06-15 20:05 UTC / 23:05 MSK / 22:05 Europe/Amsterdam.

Second trigger after readiness-aware deploy fix: 2026-06-15 18:35 UTC / 21:35 MSK / 20:35 Europe/Amsterdam.

Expected production behavior:
- GitHub sends a signed push event to `/github-deploy`.
- Nginx proxies it to `127.0.0.1:9001/github-deploy`.
- Deploy webhook returns `202 deploy queued` for `refs/heads/main`.
- `/root/metrotherapy/deploy.sh` fast-forwards, runs validation, restarts the service, waits for health readiness, checks public health, and runs post-deploy verification.

This file does not change application runtime behavior.
