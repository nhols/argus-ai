.PHONY: run rm-db eval-label-ui

STORAGE_PROVIDER ?= local
STORAGE_ROOT ?= local_files/storage
SQLITE_PATH ?= local_files/vid_analyser.db
PORT ?= 8000
EVAL_LABEL_PORT ?= 8501

run:
	VID_ANALYSER_STORAGE_PROVIDER=$(STORAGE_PROVIDER) VID_ANALYSER_STORAGE_ROOT=$(STORAGE_ROOT) VID_ANALYSER_SQLITE_PATH=$(SQLITE_PATH) PYTHONPATH=src uv run uvicorn vid_analyser.api.app:app --host 0.0.0.0 --port $(PORT) --log-level info

eval-label-ui:
	PYTHONPATH=src uv run --group eval --group ui streamlit run src/vid_analyser/evals/label_cases.py --server.port $(EVAL_LABEL_PORT) --server.address 127.0.0.1

rm-db:
	rm -f "$(SQLITE_PATH)"
