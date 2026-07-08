# Production operations runbook

## Hard rule: Telegram stays on polling

Production Telegram transport must remain polling.

Required production state:

- `TELEGRAM_TRANSPORT=polling`
- `TELEGRAM_WEBHOOK_ENABLED=0`
- `TELEGRAM_LEGACY_TOKEN_WEBHOOK_ENABLED=0`

The deploy script and production acceptance checks enforce this contract.

## Do not run full regression on the live VPS

Do not run these on `/root/metrotherapy` during normal production operation:

```bash
python scripts/regression_gate.py
python -m pytest
python scripts/production_acceptance.py --include-pytest
```

Allowed lightweight checks on production:

```bash
python scripts/user_scenario_gate.py
curl -fsS http://127.0.0.1:8082/healthz
curl -fsS http://127.0.0.1:8082/readyz
python scripts/post_deploy_verify.py --skip-pytest
```

`regression_gate.py` refuses to run on the live production host by default. The emergency override is deliberately noisy:

```bash
ALLOW_FULL_REGRESSION_ON_PROD=1 python scripts/regression_gate.py
```

Use that only in an approved maintenance window.

## B404/B603 subprocess hardening

Do not bulk-ignore Bandit B404/B603. Inventory first:

```bash
python scripts/bandit_subprocess_inventory.py
python scripts/bandit_subprocess_inventory.py --markdown > /tmp/bandit_subprocess_inventory.md
```

Then classify each finding:

1. Runtime path: prefer existing command-runner boundary or refactor.
2. Operator-only script: allow narrow `# nosec` only when command is static, no shell, no user input.
3. Tests/load tools: document why it is non-runtime.

## Security update plan

Read-only planning:

```bash
bash ops/security_update_plan.sh
```

Apply updates only in a maintenance window:

```bash
CONFIRM_SECURITY_MAINTENANCE=apply-updates bash ops/apply_security_updates.sh
```

This does not reboot automatically.

## Approved reboot

Reboot only after an explicit window is agreed:

```bash
APPROVED_REBOOT_WINDOW="YYYY-MM-DD HH:MM TZ" bash ops/reboot_after_approval.sh
```

After the host returns:

```bash
cd /root/metrotherapy
systemctl status metrotherapy.service --no-pager -l | sed -n '1,80p'
systemctl status github-deploy-webhook.service --no-pager -l | sed -n '1,80p'
curl -fsS http://127.0.0.1:8082/healthz
curl -fsS http://127.0.0.1:8082/readyz
python scripts/user_scenario_gate.py
```
