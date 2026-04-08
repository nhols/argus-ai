ENV_FILE ?= .env

.PHONY: start stop logs captcha rebuild telegram-webhook-info telegram-webhook-set telegram-webhook-delete telegram-webhook-sync

start:
	docker compose up -d
	@echo "✅ Services started. Run 'make logs' to watch output."
	@echo "If a captcha is needed, use: make captcha code=ABCD"

stop:
	docker compose down

rebuild:
	docker compose build eufy-bridge
	docker compose up -d eufy-bridge

logs:
	docker compose logs -f eufy-bridge

captcha:
	@if [ -z "$(code)" ]; then echo "❌ Usage: make captcha code=ABCD"; exit 1; fi
	curl -s -X POST "http://localhost:8080/captcha?code=$(code)" | python3 -m json.tool || true

telegram-webhook-info:
	@ENV_FILE="$(ENV_FILE)" ./vid-analyser/scripts/telegram_webhook.sh info

telegram-webhook-delete:
	@ENV_FILE="$(ENV_FILE)" ./vid-analyser/scripts/telegram_webhook.sh delete

telegram-webhook-set:
	@ENV_FILE="$(ENV_FILE)" ./vid-analyser/scripts/telegram_webhook.sh set

telegram-webhook-sync:
	@ENV_FILE="$(ENV_FILE)" ./vid-analyser/scripts/telegram_webhook.sh sync
