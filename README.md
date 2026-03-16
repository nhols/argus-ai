# ArgusAI

Eufy doorbell processing stack. Monitors a Eufy doorbell for motion/ring events, downloads recordings from the homebase, converts them to MP4, and sends them to the FastAPI analyser service.

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Eufy Cloud  │◄────│    eufy-ws       │◄────│  eufy-bridge │────►│ vid-analyser-api │
│  + Homebase  │     │  (WS server)     │     │  (Node.js)   │     │    (FastAPI)     │
└──────────────┘     └──────────────────┘     └──────┬───────┘     └──────────────────┘
                       port 3000 (internal)          │ :8080
                                                     │ (captcha UI)
                                                ┌────┴────────┐
                                                │ local_files/│
                                                │ (tmp media) │
                                                └─────────────┘
```

### Services

| Service | Description |
|---|---|
| **eufy-ws** | [eufy-security-ws](https://github.com/bropat/eufy-security-ws) built from `develop` branches (both WS server and [eufy-security-client](https://github.com/bropat/eufy-security-client)). Exposes the Eufy API over WebSocket on port 3000 (internal only). |
| **eufy-bridge** | Node.js app that connects to eufy-ws, listens for doorbell events, downloads recordings with audio, converts to MP4 via ffmpeg, and POSTs them to the FastAPI analyser. Also runs a captcha HTTP server on port 8080. |
| **vid-analyser-api** | FastAPI app that analyses uploaded clips. |

### Bridge internals

The bridge (`bridge/`) is split into modules:

| Module | Purpose |
|---|---|
| `index.js` | Entry point — wires everything together |
| `src/config.js` | Environment variables and constants |
| `src/ws-client.js` | WebSocket client with automatic reconnection and exponential backoff |
| `src/query-poller.js` | Polls `database_query_by_date` with exponential backoff (5s → 10s → 20s → 40s → 80s) until new recordings appear |
| `src/download-manager.js` | Serial download queue, collects video + audio chunks per device, muxes with ffmpeg, sends them to the FastAPI API |
| `src/event-handlers.js` | Message dispatcher → named handler functions |
| `src/captcha-server.js` | HTTP server for captcha rendering and submission |

## Setup

### Prerequisites

- Docker and Docker Compose

### DigitalOcean droplet setup

```sh
# Update package index
sudo apt update

# Install required tools
# - make: build automation
# - docker.io: Docker engine
# - docker-compose-v2: Docker Compose (v2 plugin)
sudo apt install -y make docker.io docker-compose-v2

# Enable Docker to start on boot
sudo systemctl enable docker
sudo systemctl start docker

# Allow current user to run Docker without sudo
sudo usermod -aG docker $USER

# IMPORTANT:
# Log out and log back in (or reboot) for the docker group change to take effect
```

### Environment

Copy `.env.example` (or create `.env`) with:

```env
EUFY_USERNAME=your-eufy-email@example.com
EUFY_PASSWORD=your-eufy-password
EUFY_COUNTRY=GB

DOORBELL_SN=T8213PXXXXXXXXXX
HOMEBASE_SN=T8030TXXXXXXXXXX

VID_ANALYSER_API_URL=http://vid-analyser-api:8000/analyse-video
VID_ANALYSER_API_KEY=change-me
GEMINI_API_KEY=change-me
VID_ANALYSER_STORAGE_PROVIDER=local
VID_ANALYSER_STORAGE_ROOT=/app/data/storage
VID_ANALYSER_SQLITE_PATH=/app/data/vid_analyser.db
ENABLE_API_DOCS=false
TELEGRAM_BOT_TOKEN=change-me
UI_BASIC_AUTH_USER=admin
UI_BASIC_AUTH_PASSWORD=change-me

# Only needed if your prompt template uses {{bookings}}
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=eu-west-2
```

The analyser starts even if no config exists yet. Configure it by calling `PUT /config` after startup. `/config` is protected with the same HTTP basic auth credentials as the UI:

```sh
curl -X PUT http://localhost:8000/config \
  -u admin:change-me \
  -H 'Content-Type: application/json' \
  --data-binary @<(jq -n --argfile cfg vid-analyser/config/run_config_v3.json '{config: $cfg, source: "manual"}')
```

If the active prompt template includes `{{bookings}}`, the API will load `s3://jp-bookings/bookings.json` using standard AWS credentials from the environment. In Docker Compose that means setting `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and usually `AWS_DEFAULT_REGION` in `.env`.

### Admin UI

The API also exposes a minimal admin UI directly from FastAPI on port `8000`:

- `/ui/executions`
- `/ui/executions/<execution-id>`
- `/ui/config`

These routes are protected with HTTP basic auth using `UI_BASIC_AUTH_USER` and `UI_BASIC_AUTH_PASSWORD`.

Machine-to-machine clip ingestion is separate:

- `eufy-bridge` sends `X-API-Key: <VID_ANALYSER_API_KEY>`
- `POST /analyse-video` requires that API key

For production/public deployment, set `ENABLE_API_DOCS=false` to disable `/docs`, `/redoc`, and `/openapi.json`. For local development you can set it to `true`.

### Run

```sh
make start    # docker compose up -d
make logs     # tail eufy-bridge logs
make stop     # docker compose down
make rebuild  # rebuild & restart just the bridge
```

## Captcha handling

Eufy occasionally requires a captcha during login. When this happens:

1. The bridge logs a banner:
   ```
   🔐 ════════════════════════════════════════════
   🔐  CAPTCHA REQUIRED
   🔐  Open http://localhost:8080/captcha to view & solve
   🔐 ════════════════════════════════════════════
   ```

2. **Option A — Browser (recommended)**

   Open `http://localhost:8080/captcha` in your browser. The page renders the captcha image with a text input and submit button. If accessing remotely, use an SSH tunnel:
   ```sh
   ssh -L 8080:localhost:8080 your-vm
   ```
   Then open `http://localhost:8080/captcha` locally.

3. **Option B — Make target**
   ```sh
   make captcha code=ABCD
   ```

4. **Option C — curl**
   ```sh
   curl -X POST "http://localhost:8080/captcha?code=ABCD"
   ```

The `/health` endpoint returns whether a captcha is currently pending:
```sh
curl http://localhost:8080/health
# {"status":"ok","captchaPending":false}
```

## Cutting a release

This project runs from source via `docker compose build`. To deploy or update:

1. **Commit your changes:**
   ```sh
   git add -A && git commit -m "description of changes"
   ```

2. **Tag the release:**
   ```sh
   git tag v1.0.0
   git push origin master --tags
   ```

3. **Deploy from git using the helper script:**
   ```sh
   infra/scripts/deploy.sh \
     --host <server-ip> \
     --user root \
     --env-file .env \
     --identity ~/.ssh/<your-key>
   ```

   By default this deploys:
   - the current repo's `origin` remote
   - the current local `HEAD` commit

   You can override that with:
   ```sh
   infra/scripts/deploy.sh \
     --host <server-ip> \
     --user root \
     --env-file .env \
     --identity ~/.ssh/<your-key> \
     --repo <git-url> \
     --ref <branch-tag-or-commit>
   ```

4. **To update the upstream eufy-security-ws / eufy-security-client** (e.g. to pick up new develop commits):
   ```sh
   docker compose build --no-cache eufy-ws
   docker compose up -d eufy-ws
   ```
   This re-clones both repos at their latest `develop` HEAD.

### What gets versioned

| What | Where | Versioning |
|---|---|---|
| Bridge code | `bridge/` | Git tags on this repo |
| eufy-security-ws | `eufy-ws/Dockerfile` | Pinned to `develop` branch; rebuild with `--no-cache` to update |
| vid-analyser-api | `docker-compose.yml` | Built from `vid-analyser/Dockerfile.api` |

## TODO

- Add Telegram webhook support for inbound bot updates.
- If Telegram webhooks are added, expose a public HTTPS endpoint for FastAPI and point Telegram at a real domain-backed webhook URL.
- Add scheduled task support using APScheduler with schedules and run history stored in SQLite.
