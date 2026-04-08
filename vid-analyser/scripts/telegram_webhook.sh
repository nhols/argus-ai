#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  telegram_webhook.sh info
  telegram_webhook.sh delete
  telegram_webhook.sh set
  telegram_webhook.sh sync

Required env for set/sync:
  TELEGRAM_BOT_TOKEN
  PUBLIC_BASE_URL
  TELEGRAM_WEBHOOK_PATH_SECRET
  TELEGRAM_WEBHOOK_HEADER_SECRET
EOF
}

load_env_file() {
  local env_file="${ENV_FILE:-}"
  if [[ -z "$env_file" || ! -f "$env_file" ]]; then
    return
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" || "${line#\#}" != "$line" ]]; then
      continue
    fi
    if [[ "$line" != *=* ]]; then
      continue
    fi
    export "$line"
  done < "$env_file"
}

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "Missing required env var: $name" >&2
    exit 1
  fi
}

action="${1:-}"
if [[ -z "$action" ]]; then
  usage
  exit 1
fi

load_env_file
require_env TELEGRAM_BOT_TOKEN
api_url="https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}"

case "$action" in
  info)
    curl --fail --silent --show-error "${api_url}/getWebhookInfo"
    ;;
  delete)
    curl --fail --silent --show-error -X POST \
      "${api_url}/deleteWebhook?drop_pending_updates=true"
    ;;
  set|sync)
    require_env PUBLIC_BASE_URL
    require_env TELEGRAM_WEBHOOK_PATH_SECRET
    require_env TELEGRAM_WEBHOOK_HEADER_SECRET
    webhook_url="${PUBLIC_BASE_URL%/}/webhooks/telegram/${TELEGRAM_WEBHOOK_PATH_SECRET}"
    payload="$(printf '{"url":"%s","secret_token":"%s","allowed_updates":["message"],"drop_pending_updates":true}' \
      "$webhook_url" \
      "$TELEGRAM_WEBHOOK_HEADER_SECRET")"
    curl --fail --silent --show-error -X POST \
      -H 'Content-Type: application/json' \
      -d "$payload" \
      "${api_url}/setWebhook"
    ;;
  *)
    usage
    exit 1
    ;;
esac
