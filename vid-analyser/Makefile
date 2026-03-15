.PHONY: run eval-labeler eval-results

CONFIG_S3_BUCKET ?=
CONFIG_S3_KEY ?= config/run_config.json
PORT ?= 8000

run:
	VID_ANALYSER_CONFIG_S3_BUCKET=$(CONFIG_S3_BUCKET) VID_ANALYSER_CONFIG_S3_KEY=$(CONFIG_S3_KEY) PYTHONPATH=src uv run uvicorn vid_analyser.api.app:app --host 0.0.0.0 --port $(PORT) --log-level info

eval-labeler:
	PYTHONPATH=src uv run streamlit run src/vid_analyser/evals/ui/labeler/app.py

eval-results:
	PYTHONPATH=src uv run streamlit run src/vid_analyser/evals/ui/results/app.py
