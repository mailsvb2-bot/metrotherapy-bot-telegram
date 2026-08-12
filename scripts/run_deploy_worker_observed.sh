#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/root/metrotherapy}"
INNER_WORKER="${DEPLOY_INNER_WORKER:-$APP_DIR/scripts/run_deploy_worker.sh}"
LIVE_PROOF_RUNNER="${LIVE_PROOF_RUNNER:-$APP_DIR/scripts/production_live_proofs.py}"
YOOKASSA_REFUND_DRILL="${YOOKASSA_REFUND_DRILL:-$APP_DIR/scripts/yookassa_refund_drill_guard.py}"
CURRENT_RELEASE_LINK="${METRO_CURRENT_RELEASE_LINK:-/var/lib/metrotherapy/runtime/current}"
YOOKASSA_REFUND_PYTHON="${YOOKASSA_REFUND_PYTHON:-$CURRENT_RELEASE_LINK/.venv/bin/python}"
YOOKASSA_GUARD_STATUS_FILE="${YOOKASSA_GUARD_STATUS_FILE:-/var/lib/metrotherapy/deploy-state/yookassa_refund_guard.status}"
LOG_FILE="${LOG_FILE:-/var/log/metrotherapy_deploy.log}"
TRIGGER_SHA="${DEPLOY_TRIGGER_SHA:-}"
TRIGGER_MESSAGE=""

if [ -n "$TRIGGER_SHA" ]; then
  git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
  TRIGGER_MESSAGE="$(git -C "$APP_DIR" show -s --format=%B "$TRIGGER_SHA" 2>/dev/null || true)"
fi
case "$TRIGGER_MESSAGE" in
  *"[ops-live-proof-result]"*|*"_ops-live-proof-result_"*|*"[deploy-failure-result]"*)
    printf '=== observed audit result trigger skipped=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
    exit 0
    ;;
esac

classify_production_gate_substage() {
  local substage="unknown"
  local line=""
  while IFS= read -r line; do
    case "$line" in
      "==> handler DB boundary audit") substage="handler_db_boundary" ;;
      "==> handler exception boundary audit") substage="handler_exception_boundary" ;;
      "==> runtime contract") substage="runtime_contract" ;;
      "==> prod validator") substage="prod_validator" ;;
      "==> smoke") substage="smoke" ;;
      "==> storage legacy audit") substage="storage_legacy_audit" ;;
      "==> disaster recovery status") substage="disaster_recovery_status" ;;
      "==> scheduler job probe") substage="scheduler_job_probe" ;;
      "==> auto-audio dry-run probe") substage="auto_audio_dry_run_probe" ;;
      "==> payment entitlement probe") substage="payment_entitlement_probe" ;;
      "==> user journey E2E probe") substage="user_journey_e2e_probe" ;;
      "==> Telegram live smoke") substage="telegram_live_smoke" ;;
      "==> postgres restore drill") substage="postgres_restore_drill" ;;
      "==> health") substage="health" ;;
      "==> ready") substage="ready" ;;
      "==> postgres job concurrency") substage="postgres_job_concurrency" ;;
      "==> postgres messenger outbox concurrency") substage="postgres_messenger_outbox_concurrency" ;;
      "==> auto-audio load dry-run") substage="auto_audio_load_dry_run" ;;
    esac
  done
  printf '%s\n' "$substage"
}

read_cached_yookassa_result() {
  local line=""
  [ -f "$YOOKASSA_GUARD_STATUS_FILE" ] || return 1
  IFS= read -r line < "$YOOKASSA_GUARD_STATUS_FILE" || return 1
  [ -n "$line" ] || return 1
  [ "${#line}" -le 900 ] || return 1
  case "$line" in
    "[ops-live-proof-result] trigger=${TRIGGER_SHA:0:12} status=blocked yookassa_refund=blocked reason="*) ;;
    "[ops-live-proof-result] trigger=${TRIGGER_SHA:0:12} status=ok "*) ;;
    *) return 1 ;;
  esac
  if ! printf '%s' "$line" | grep -Eq '^[A-Za-z0-9_.:=,/\[\] -]+$'; then
    return 1
  fi
  printf '%s\n' "$line"
}

publish_deploy_failure_result() {
  local inner_code="$1"
  local stage="unknown"
  local gate_substage="not_applicable"
  local bounded_code="$inner_code"
  local segment=""
  local matched=""
  local parent_sha=""
  local tree_sha=""
  local result_sha=""
  local message=""
  local attempt=""

  if [ -f "$LOG_FILE" ] && [ -n "$TRIGGER_SHA" ]; then
    segment="$(awk -v needle="=== deploy trigger sha: $TRIGGER_SHA ===" '
      $0 == needle { found=1; buf="" }
      found { print }
    ' "$LOG_FILE" | tail -n 800)"
  fi

  if printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=mandatory production backup, restore and readiness gate code='; then
    stage="production_gate_failed"
    gate_substage="$(printf '%s\n' "$segment" | classify_production_gate_substage)"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=mandatory production backup, restore and readiness gate code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=candidate strict validator and expand migrations code='; then
    stage="candidate_validator_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=candidate strict validator and expand migrations code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=previous release compatibility on expanded schema code='; then
    stage="schema_compat_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=previous release compatibility on expanded schema code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=build immutable release '; then
    stage="release_build_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=build immutable release ' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=restart metrotherapy service code='; then
    stage="runtime_restart_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=restart metrotherapy service code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=migrate authoritative privacy export environment code='; then
    stage="env_migration_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=migrate authoritative privacy export environment code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED command=validate production runtime contract after env migration code='; then
    stage="runtime_contract_failed"
    matched="$(printf '%s\n' "$segment" | grep -F 'IMMUTABLE_DEPLOY_FAILED command=validate production runtime contract after env migration code=' | tail -1)"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED health timeout:'; then
    stage="health_failed"
  elif printf '%s\n' "$segment" | grep -Fq 'IMMUTABLE_DEPLOY_FAILED dirty source worktree'; then
    stage="dirty_source_failed"
  fi

  if [ -n "$matched" ]; then
    case "$matched" in
      *' code='*)
        bounded_code="${matched##* code=}"
        bounded_code="${bounded_code%%[^0-9]*}"
        ;;
    esac
  fi
  case "$bounded_code" in ''|*[!0-9]*) bounded_code="$inner_code" ;; esac
  case "$inner_code" in ''|*[!0-9]*) inner_code="1" ;; esac

  if [ "$stage" = "production_gate_failed" ]; then
    message="[deploy-failure-result] trigger=${TRIGGER_SHA:0:12} status=error stage=$stage gate_substage=$gate_substage code=$bounded_code worker_code=$inner_code"
  else
    message="[deploy-failure-result] trigger=${TRIGGER_SHA:0:12} status=error stage=$stage code=$bounded_code worker_code=$inner_code"
  fi

  for attempt in 1 2 3; do
    git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
    parent_sha="$(git -C "$APP_DIR" rev-parse origin/main 2>/dev/null || true)"
    tree_sha="$(git -C "$APP_DIR" rev-parse "$parent_sha^{tree}" 2>/dev/null || true)"
    if [ -z "$parent_sha" ] || [ -z "$tree_sha" ]; then sleep "$attempt"; continue; fi
    result_sha="$(printf '%s\n' "$message" | git -C "$APP_DIR" -c user.name="Metrotherapy Deploy Failure Audit" -c user.email="deploy-failure-audit@metrotherapy.local" commit-tree "$tree_sha" -p "$parent_sha" -F - 2>/dev/null || true)"
    if [ -n "$result_sha" ] && git -C "$APP_DIR" push origin "$result_sha:refs/heads/main" >/dev/null 2>&1; then
      printf '=== %s ===\n' "$message" >> "$LOG_FILE"
      return 0
    fi
    sleep "$attempt"
  done
  printf 'ERROR: unable to publish secret-safe deploy failure evidence trigger=%s stage=%s code=%s\n' "$TRIGGER_SHA" "$stage" "$bounded_code" >> "$LOG_FILE"
  return 0
}

publish_post_deploy_failure_result() {
  local audit="$1"
  local audit_code="$2"
  local parent_sha=""
  local tree_sha=""
  local result_sha=""
  local latest_message=""
  local message=""
  local cached_message=""
  local attempt=""

  case "$audit" in live_proof|yookassa_refund) ;; *) audit="unknown" ;; esac
  case "$audit_code" in ''|*[!0-9]*) audit_code="1" ;; esac

  git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
  latest_message="$(git -C "$APP_DIR" show -s --format=%B origin/main 2>/dev/null || true)"
  case "$latest_message" in
    *"[ops-live-proof-result]"*"trigger=${TRIGGER_SHA:0:12}"*) return 0 ;;
  esac

  if [ "$audit" = "yookassa_refund" ]; then
    cached_message="$(read_cached_yookassa_result 2>/dev/null || true)"
  fi
  if [ -n "$cached_message" ]; then
    message="$cached_message"
  else
    message="[ops-live-proof-result] trigger=${TRIGGER_SHA:0:12} status=error audit=$audit code=$audit_code"
  fi

  for attempt in 1 2 3; do
    git -C "$APP_DIR" fetch origin main >/dev/null 2>&1 || true
    parent_sha="$(git -C "$APP_DIR" rev-parse origin/main 2>/dev/null || true)"
    tree_sha="$(git -C "$APP_DIR" rev-parse "$parent_sha^{tree}" 2>/dev/null || true)"
    if [ -z "$parent_sha" ] || [ -z "$tree_sha" ]; then sleep "$attempt"; continue; fi
    result_sha="$(printf '%s\n' "$message" | git -C "$APP_DIR" -c user.name="Metrotherapy Post Deploy Audit" -c user.email="post-deploy-audit@metrotherapy.local" commit-tree "$tree_sha" -p "$parent_sha" -F - 2>/dev/null || true)"
    if [ -n "$result_sha" ] && git -C "$APP_DIR" push origin "$result_sha:refs/heads/main" >/dev/null 2>&1; then
      printf '=== %s ===\n' "$message" >> "$LOG_FILE"
      return 0
    fi
    sleep "$attempt"
  done
  printf 'ERROR: unable to publish secret-safe post-deploy audit failure trigger=%s audit=%s code=%s\n' "$TRIGGER_SHA" "$audit" "$audit_code" >> "$LOG_FILE"
  return 0
}

run_post_deploy_audit() {
  local audit="$1"
  local runner="$2"
  local missing_code="$3"
  local audit_code="0"
  local python_bin="/usr/bin/python3"

  if [ ! -f "$runner" ]; then
    printf 'ERROR: post-deploy audit runner is missing audit=%s\n' "$audit" >> "$LOG_FILE"
    publish_post_deploy_failure_result "$audit" "$missing_code"
    return "$missing_code"
  fi

  if [ "$audit" = "yookassa_refund" ]; then
    python_bin="$YOOKASSA_REFUND_PYTHON"
    if [ ! -x "$python_bin" ]; then
      printf 'ERROR: deployed refund-drill Python is unavailable\n' >> "$LOG_FILE"
      publish_post_deploy_failure_result "$audit" 43
      return 43
    fi
    rm -f "$YOOKASSA_GUARD_STATUS_FILE"
    export YOOKASSA_GUARD_STATUS_FILE
  fi

  set +e
  "$python_bin" "$runner" >> "$LOG_FILE" 2>&1
  audit_code="$?"
  set -e
  if [ "$audit_code" -ne 0 ]; then
    publish_post_deploy_failure_result "$audit" "$audit_code"
    return "$audit_code"
  fi
  return 0
}

set +e
/usr/bin/bash "$INNER_WORKER"
INNER_CODE="$?"
set -e
if [ "$INNER_CODE" -ne 0 ]; then
  publish_deploy_failure_result "$INNER_CODE"
  exit "$INNER_CODE"
fi

case "$TRIGGER_MESSAGE" in
  *"[vk-confirmation-sync-request]"*|*"[rollback-live-proof-request]"*) run_post_deploy_audit "live_proof" "$LIVE_PROOF_RUNNER" 41 ;;
esac
case "$TRIGGER_MESSAGE" in
  *"[yookassa-refund-live-proof-request]"*) run_post_deploy_audit "yookassa_refund" "$YOOKASSA_REFUND_DRILL" 42 ;;
esac

printf '=== deploy worker completed trigger=%s: %s ===\n' "$TRIGGER_SHA" "$(date -Is)" >> "$LOG_FILE"
