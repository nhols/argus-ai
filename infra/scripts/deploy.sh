#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  deploy.sh --host <host> --user <user> [options]

Required:
  --host         Remote host or IP
  --user         SSH user

Optional:
  --env-file     Local env file to copy to remote .env (default: .env.prod)
  --app-dir      Remote app dir (default: /opt/argusai)
  --identity     SSH identity file
  --repo         Git repo URL (default: local origin remote)
  --ref          Git ref to deploy (default: local HEAD commit)
EOF
}

HOST=""
USER_NAME=""
ENV_FILE=".env.prod"
APP_DIR="/opt/argusai"
IDENTITY_FILE=""
REPO_URL=""
GIT_REF=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --env-file) ENV_FILE="$2"; shift 2 ;;
    --app-dir) APP_DIR="$2"; shift 2 ;;
    --identity) IDENTITY_FILE="$2"; shift 2 ;;
    --repo) REPO_URL="$2"; shift 2 ;;
    --ref) GIT_REF="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 1 ;;
  esac
done

if [[ -z "$HOST" || -z "$USER_NAME" ]]; then
  usage
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Env file not found: $ENV_FILE" >&2
  exit 1
fi

if [[ -z "$REPO_URL" ]]; then
  REPO_URL="$(git config --get remote.origin.url || true)"
fi
if [[ -z "$GIT_REF" ]]; then
  GIT_REF="$(git rev-parse HEAD)"
fi

if [[ -z "$REPO_URL" || -z "$GIT_REF" ]]; then
  echo "Could not determine git repo/ref. Pass --repo and --ref explicitly." >&2
  exit 1
fi

SSH_OPTS=()
if [[ -n "$IDENTITY_FILE" ]]; then
  SSH_OPTS+=(-i "$IDENTITY_FILE")
fi

REMOTE="${USER_NAME}@${HOST}"

ssh "${SSH_OPTS[@]}" "$REMOTE" "
  set -euo pipefail
  if command -v cloud-init >/dev/null 2>&1; then
    cloud-init status --wait
  fi
  mkdir -p '${APP_DIR}'
  cd '${APP_DIR}'
  if [[ ! -d .git ]]; then
    git init
  fi
  if ! git remote get-url origin >/dev/null 2>&1; then
    git remote add origin '${REPO_URL}'
  else
    git remote set-url origin '${REPO_URL}'
  fi
  git fetch --tags origin
  git checkout '${GIT_REF}'
  mkdir -p '${APP_DIR}/local_files/vid-analyser'
"

scp "${SSH_OPTS[@]}" "$ENV_FILE" "$REMOTE:${APP_DIR}/.env"
ssh "${SSH_OPTS[@]}" "$REMOTE" "
  set -euo pipefail
  cd '${APP_DIR}'
  if docker compose version >/dev/null 2>&1; then
    docker compose up -d --build
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose up -d --build
  else
    echo 'Docker Compose is not installed on the remote host.' >&2
    exit 1
  fi
"
