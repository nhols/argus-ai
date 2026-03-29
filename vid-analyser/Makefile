.PHONY: run rm-db

STORAGE_PROVIDER ?= local
STORAGE_ROOT ?= local_files/storage
SQLITE_PATH ?= local_files/vid_analyser.db
PORT ?= 8000

run:
	VID_ANALYSER_STORAGE_PROVIDER=$(STORAGE_PROVIDER) VID_ANALYSER_STORAGE_ROOT=$(STORAGE_ROOT) VID_ANALYSER_SQLITE_PATH=$(SQLITE_PATH) PYTHONPATH=src uv run uvicorn vid_analyser.api.app:app --host 0.0.0.0 --port $(PORT) --log-level info

rm-db:
	rm -f "$(SQLITE_PATH)"
