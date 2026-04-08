`ArgusAI` is named after Argus Panoptes, the many-eyed watchman of Greek mythology.

# ArgusAI

ArgusAI ingests Eufy doorbell events, downloads the corresponding recordings from the HomeBase, analyses each clip with an LLM-backed pipeline, stores execution history, and optionally sends a Telegram notification with the video.

## High-Level Architecture

```text
┌────────────────────┐
│ Eufy Cloud/HomeBase│
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐
│ eufy-ws            │  Community websocket bridge to the Eufy ecosystem
│ (Docker, port 3000)│
└─────────┬──────────┘
          │ internal websocket
          ▼
┌────────────────────┐
│ eufy-bridge        │  Node service that listens for events, polls for
│ (Docker, Node.js)  │  recordings, downloads audio/video, muxes to MP4,
│                    │  and forwards clips to the analyser API
└──────┬───────┬─────┘
       │       │
       │       └──────────────► local captcha UI on `:8080`
       │
       ▼
┌────────────────────┐
│ vid-analyser-api   │  FastAPI service that validates uploads, enriches
│ (Docker, port 8000)│  prompts, runs Google GenAI analysis, stores execution
│                    │  state, and sends notifications
└──────┬───────┬─────┘
       │       │
       │       └──────────────► Telegram notifications
       │
       ├──────────────────────► SQLite (`executions`, `config_versions`)
       └──────────────────────► Local storage or S3 for retained videos
```

### Runtime flow

1. `eufy-ws` maintains the connection to the Eufy ecosystem and exposes a websocket API inside Docker.
2. `eufy-bridge` listens for motion, person, and ring events, then uses `station.database_query_by_date` with exponential backoff to wait for the recording to appear.
3. Once a clip is available, the bridge streams raw video and audio chunks to disk, muxes them with `ffmpeg`, and forwards the finished MP4 to the analyser either via `POST /analyse-video` or the shared-path handoff at `POST /analyse-video/shared`.
4. `vid-analyser-api` creates an execution record in SQLite, loads the latest stored run config, and builds the final system and user prompts.
5. The analysis pipeline can burn overlay zones onto the clip with `ffmpeg`, optionally attempt person identification, and then sends the video plus prompts to Google's Gemini models.
6. The API stores the analysis result, stores the clip via the configured storage provider, optionally sends a Telegram notification, and exposes the history through the built-in UI.

### Main components

| Path | Role |
|---|---|
| `eufy-ws/` | Docker build for the websocket server that talks to Eufy. The image is built from upstream `develop` branches in `eufy-ws/Dockerfile`. |
| `bridge/` | Node.js bridge service. `bridge/index.js` wires together the websocket client, polling, download manager, and captcha server. |
| `vid-analyser/src/vid_analyser/api/` | FastAPI ingestion API and admin UI. The main entry point is `vid_analyser/api/app.py`. |
| `vid-analyser/src/vid_analyser/pipeline/` | Video analysis pipeline orchestration. `pipeline/run.py` controls overlay enrichment, optional person ID, and LLM dispatch. |
| `vid-analyser/src/vid_analyser/prompting.py` | Prompt templating, including `{{time}}`, `{{bookings}}`, and `{{previous_messages}}` expansion. |
| `vid-analyser/src/vid_analyser/db/` | SQLite schema and repositories for executions and config versions. |
| `vid-analyser/src/vid_analyser/storage/` | Local or S3-backed clip retention after analysis. |
| `vid-analyser/src/vid_analyser/ui/` | Built-in admin pages for executions and config management. |
| `vid-analyser/config/` | Example persisted run configs. `run_config_v3.json` is the most complete current example. |
| `scripts/` | Prompt text used by local scripts and evals, including `scripts/sys_prompt.md` and `scripts/sys_prompt_n8n.md`. |
| `infra/` | Terraform files and helper scripts for the DigitalOcean deployment path. |

### Configuration model

The analyser has two layers of configuration:

- Runtime environment controls service wiring, secrets, storage backends, auth, and API behaviour.
- Persisted run config controls the actual analysis behaviour: provider model, overlay zones, prompts, Telegram chat ID, and optional analysis features.

The persisted run config lives in SQLite in the `config_versions` table and is loaded on startup from `vid-analyser/src/vid_analyser/api/app.py`. Example JSON files live in `vid-analyser/config/`, and the built-in UI exposes `/ui/config` for editing the active config.

## Local Development

### Prerequisites

- Docker with Docker Compose v2
- `make`
- `uv` and Python 3.13 if you want to run the analyser or eval tooling outside Docker
- `terraform` if you want to work on the DigitalOcean deployment

### First-time setup

```sh
cp .env.example .env
docker compose build
make start
```

The stack can start before the analyser has an active run config, but `/analyse-video` will return `503` until a config is loaded. Start from `vid-analyser/config/run_config_v3.json` and either:

- load it through the UI at `http://localhost:8000/ui/config`, or
- send it to `PUT /config` inside the API's expected wrapper payload, `{ "config": ... }`.

The UI and `GET/PUT /config` are protected with `UI_BASIC_AUTH_USER` and `UI_BASIC_AUTH_PASSWORD`.

Useful local endpoints:

- `http://localhost:8000/ui/executions`
- `http://localhost:8000/ui/config`
- `http://localhost:8000/docs` when `ENABLE_API_DOCS=true`
- `http://localhost:8080/captcha`

### Key commands

| Command | Where | What it does |
|---|---|---|
| `make start` | repo root | Starts the Docker Compose stack in the background. |
| `make logs` | repo root | Follows `eufy-bridge` logs. |
| `docker compose logs -f vid-analyser-api` | repo root | Follows the FastAPI analyser logs. |
| `make captcha code=ABCD` | repo root | Submits a pending Eufy captcha to the bridge's local captcha server. |
| `make rebuild` | repo root | Rebuilds and restarts only `eufy-bridge`. |
| `make stop` | repo root | Stops the Docker Compose stack. |

When running the analyser directly, the `vid-analyser/Makefile` also lets you override make variables such as `STORAGE_PROVIDER`, `STORAGE_ROOT`, `SQLITE_PATH`, and `PORT`.

## Environment Variables

Use the repo-root `.env.example` as the canonical template for the Docker Compose stack. Docker Compose reads the repo-root `.env`, while direct `uv` runs inside `vid-analyser/` can also read `vid-analyser/.env` through `python-dotenv`.

### Core stack variables

| Variable | Purpose | Where to find it |
|---|---|---|
| `EUFY_USERNAME` | Eufy account email for the websocket bridge container. | `.env.example`, `docker-compose.yml` under `eufy-ws.environment` |
| `EUFY_PASSWORD` | Eufy account password for the websocket bridge container. | `.env.example`, `docker-compose.yml` under `eufy-ws.environment` |
| `EUFY_COUNTRY` | Eufy account country code passed to `eufy-ws`. | `.env.example`, `docker-compose.yml` under `eufy-ws.environment` |
| `DOORBELL_SN` | Device serial number that `eufy-bridge` filters on when deciding which events to process. | `.env.example`, `docker-compose.yml`, `bridge/src/config.js`, `bridge/src/event-handlers.js`, `bridge/src/query-poller.js` |
| `HOMEBASE_SN` | HomeBase serial number used for database queries and metadata sent upstream. | `.env.example`, `docker-compose.yml`, `bridge/src/config.js`, `bridge/src/query-poller.js`, `bridge/src/download-manager.js` |
| `VID_ANALYSER_API_URL` | URL that the bridge posts completed MP4 clips to. In Compose it should point at `/analyse-video` on the analyser service. | `.env.example`, `docker-compose.yml`, `bridge/src/config.js`, `bridge/src/download-manager.js` |
| `VID_ANALYSER_API_KEY` | Shared secret between `eufy-bridge` and `vid-analyser-api` for `X-API-Key` authentication. | `.env.example`, `docker-compose.yml`, `bridge/src/config.js`, `vid-analyser/src/vid_analyser/auth.py` |
| `GOOGLE_API_KEY` | Google GenAI credential consumed by the analyser pipeline through the Google provider. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/agent/retry.py`, `vid-analyser/.env` for local direct runs |
| `VID_ANALYSER_STORAGE_PROVIDER` | Storage backend for retained videos. Supported values today are `local` and `s3`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/storage/__init__.py` |
| `VID_ANALYSER_STORAGE_ROOT` | Root directory for retained videos when `VID_ANALYSER_STORAGE_PROVIDER=local`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/storage/__init__.py` |
| `VID_ANALYSER_SQLITE_PATH` | SQLite file path used for executions and config versions. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py`, `vid-analyser/Makefile` |
| `ENABLE_API_DOCS` | Enables or disables `/docs`, `/redoc`, and `/openapi.json`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token used when notifications are enabled in the persisted run config. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py`, `vid-analyser/src/vid_analyser/notifications/telegram.py` |
| `UI_BASIC_AUTH_USER` | Username for the admin UI and `GET/PUT /config`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/auth.py` |
| `UI_BASIC_AUTH_PASSWORD` | Password for the admin UI and `GET/PUT /config`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/auth.py` |
| `AWS_ACCESS_KEY_ID` | AWS credential used by `boto3` when prompt templates fetch `{{bookings}}` from S3 and when video retention uses S3 storage. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py`, `vid-analyser/src/vid_analyser/prompting.py`, `vid-analyser/src/vid_analyser/storage/s3.py` |
| `AWS_SECRET_ACCESS_KEY` | AWS secret paired with `AWS_ACCESS_KEY_ID` for the same S3 access paths. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py`, `vid-analyser/src/vid_analyser/prompting.py`, `vid-analyser/src/vid_analyser/storage/s3.py` |
| `AWS_DEFAULT_REGION` | Default AWS region used by `boto3` when S3-backed prompt data or S3 video storage is enabled. | `.env.example`, `docker-compose.yml`, used implicitly by `boto3` in `vid-analyser/src/vid_analyser/api/app.py` and `vid-analyser/src/vid_analyser/storage/s3.py` |
| `LOGFIRE_TOKEN` | Logfire write token for FastAPI and Pydantic AI instrumentation emitted by the analyser service. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/api/app.py` |

### Optional or non-default variables

| Variable | Purpose | Where to find it |
|---|---|---|
| `EUFY_WS_URL` | Manual override for the websocket endpoint that the bridge connects to. In Docker Compose it is fixed to `ws://eufy-ws:3000`. | `bridge/src/config.js`, `docker-compose.yml` |
| `OUTPUT_DIR` | Manual override for where `eufy-bridge` writes temporary raw streams and MP4 files before upload. | `bridge/src/config.js` |
| `VID_ANALYSER_SHARED_INPUT_ROOT` | Optional shared filesystem root used when the bridge and analyser can hand off clips by path instead of multipart upload. The API only accepts shared paths under this root. | `docker-compose.yml`, `bridge/src/config.js`, `vid-analyser/src/vid_analyser/api/app.py` |
| `CAPTCHA_PORT` | Port for the bridge's local captcha UI and health check. Compose sets it to `8080`. | `bridge/src/captcha-server.js`, `docker-compose.yml`, `Makefile` |
| `VID_ANALYSER_VIDEO_S3_BUCKET` | Required when `VID_ANALYSER_STORAGE_PROVIDER=s3`; names the bucket used for retained analysed videos. | `vid-analyser/src/vid_analyser/storage/__init__.py`, `vid-analyser/src/vid_analyser/storage/s3.py` |
| `BOOKINGS_S3_BUCKET` | Required when persisted config enables `get_bookings`; names the S3 bucket containing the bookings JSON document used by notifier context. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/bookings.py` |
| `BOOKINGS_S3_KEY` | Required when persisted config enables `get_bookings`; key for the bookings JSON document in `BOOKINGS_S3_BUCKET`. | `.env.example`, `docker-compose.yml`, `vid-analyser/src/vid_analyser/bookings.py` |
| `LOCAL_STORE_DIR` | Local eval-data root used by the Streamlit eval tools, not by the production API path. | `vid-analyser/.env`, `vid-analyser/src/vid_analyser/evals/ui/labeler/app.py`, `vid-analyser/src/vid_analyser/evals/ui/results/app.py` |

## Terraform And DigitalOcean

The Terraform under `infra/` is intentionally small. It provisions one single-tenant DigitalOcean droplet, attaches a firewall, bootstraps Docker on first boot, and then relies on a shell deploy script to pull the repo and run Docker Compose on the box.

### What the Terraform creates

- one DigitalOcean droplet
- one DigitalOcean firewall
- public SSH access restricted by the configured CIDR
- public access to the analyser app port
- bootstrap-time installation of Docker, the Compose plugin, Git, the expected app directories, and an optional swapfile

It does not currently manage DNS, object storage, backups, a load balancer, or managed databases.

### Terraform file map

| File | Role |
|---|---|
| `infra/environments/example/versions.tf` | Pins Terraform and the DigitalOcean provider, and configures the provider with `var.do_token`. |
| `infra/environments/example/variables.tf` | Defines environment-level inputs such as region, droplet size, image, SSH CIDR, app directory, DigitalOcean project name, and swap size. |
| `infra/environments/example/terraform.tfvars.example` | Example values for a concrete deployment. Copy this to `terraform.tfvars` and fill in real values, including the target DigitalOcean project and optional swap sizing. |
| `infra/environments/example/main.tf` | Wires the environment variables into the reusable `modules/droplet` module. |
| `infra/environments/example/outputs.tf` | Exposes the droplet ID and public IPv4 after `terraform apply`. |
| `infra/modules/droplet/main.tf` | Creates the droplet and firewall, and injects the bootstrap template as cloud-init user data. |
| `infra/modules/droplet/variables.tf` | Defines the module interface consumed by the example environment. |
| `infra/modules/droplet/outputs.tf` | Returns the created droplet ID and IPv4. |
| `infra/scripts/bootstrap.sh.tftpl` | Cloud-init shell template that installs Docker and creates `/opt/argusai` plus the expected `local_files` directories. |
| `infra/scripts/deploy.sh` | Post-provision deploy helper that waits for cloud-init, checks out a git ref on the droplet, copies `.env`, and runs `docker compose up -d --build`. |

### Typical DigitalOcean deploy flow

```sh
cd infra/environments/example
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform apply
```

After the droplet exists, deploy the app from your local checkout:

```sh
infra/scripts/deploy.sh \
  --host <droplet-ip> \
  --user root \
  --env-file .env \
  --identity ~/.ssh/<your-key>
```

By default the deploy script uses the current repo's `origin` remote and the current local `HEAD` commit. You can override both with `--repo` and `--ref`.
