.PHONY: run eval-labeler eval-results

RUN_CONFIG_PATH ?= config/run_config.json
PORT ?= 8000

run:
	VID_ANALYSER_RUN_CONFIG_PATH=$(RUN_CONFIG_PATH) PYTHONPATH=src uv run uvicorn vid_analyser.api.app:app --host 0.0.0.0 --port $(PORT) --log-level info

eval-labeler:
	PYTHONPATH=src uv run streamlit run src/vid_analyser/evals/ui/labeler/app.py

eval-results:
	PYTHONPATH=src uv run streamlit run src/vid_analyser/evals/ui/results/app.py
