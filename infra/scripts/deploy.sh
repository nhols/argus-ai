#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy.sh --instance-id <id> --region <aws-region> --repo-url <git-url> --env-file <path> --config-file <path> --bucket <bucket> [options]

Required:
  --instance-id  EC2 instance ID managed by SSM
  --region       AWS region for SSM and S3 operations
  --repo-url     Git repository URL to clone on the instance
  --env-file     Local .env file to copy
  --config-file  Local run config JSON to upload to S3
  --bucket       S3 bucket name

Optional:
  --config-key   S3 config key (default: config/run_config.json)
  --git-ref      Git branch, tag, or commit to deploy (default: current HEAD)
  --app-dir      Remote app dir (default: /opt/eufy-client)
EOF
}

INSTANCE_ID=""
AWS_REGION=""
REPO_URL=""
ENV_FILE=""
CONFIG_FILE=""
BUCKET=""
CONFIG_KEY="config/run_config.json"
GIT_REF="$(git rev-parse HEAD)"
APP_DIR="/opt/eufy-client"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --instance-id) INSTANCE_ID="$2"; shift 2 ;;
    --region) AWS_REGION="$2"; shift 2 ;;
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --config-file) CONFIG_FILE="$2"; shift 2 ;;
    --bucket) BUCKET="$2"; shift 2 ;;
    --config-key) CONFIG_KEY="$2"; shift 2 ;;
    --git-ref) GIT_REF="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$INSTANCE_ID" || -z "$AWS_REGION" || -z "$REPO_URL" || -z "$ENV_FILE" || -z "$CONFIG_FILE" || -z "$BUCKET" ]]; then
  usage
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI is required" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

aws --region "$AWS_REGION" s3 cp "$CONFIG_FILE" "s3://${BUCKET}/${CONFIG_KEY}"

ENV_B64="$(base64 -w 0 "$ENV_FILE")"

read -r -d '' REMOTE_COMMANDS <<EOF || true
set -euo pipefail
mkdir -p '${APP_DIR}'
if [[ ! -d '${APP_DIR}/.git' ]]; then
  git clone '${REPO_URL}' '${APP_DIR}'
fi
cd '${APP_DIR}'
git fetch --all --tags --prune
git checkout '${GIT_REF}'
printf '%s' '${ENV_B64}' | base64 -d > .env
docker compose up -d --build
EOF

COMMAND_ID="$(
  COMMANDS_JSON="$(python3 -c 'import json, sys; print(json.dumps(sys.stdin.read().splitlines()))' <<<"$REMOTE_COMMANDS")"
  aws --region "$AWS_REGION" ssm send-command \
    --instance-ids "$INSTANCE_ID" \
    --document-name "AWS-RunShellScript" \
    --comment "Deploy eufy-client" \
    --parameters "commands=${COMMANDS_JSON}" \
    --query 'Command.CommandId' \
    --output text
)"

aws --region "$AWS_REGION" ssm wait command-executed \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID"

aws --region "$AWS_REGION" ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}'
