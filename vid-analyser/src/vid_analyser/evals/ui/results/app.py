import logging
import os

import streamlit as st
from vid_analyser.evals.config import EvalConfigInput
from vid_analyser.evals.report_model import EvalCaseRow, EvalReport, EvalRunRecord, RunOverviewRow
from vid_analyser.evals.store.local import CONFIG_JSON, REPORT_JSON, LocalStore

logger = logging.getLogger(__name__)


def get_local_store() -> LocalStore:
    local_store_dir = os.getenv("LOCAL_STORE_DIR")
    if not local_store_dir:
        raise RuntimeError(
            "LOCAL_STORE_DIR not found in .env. Set LOCAL_STORE_DIR to a local directory containing eval runs and videos."
        )
    return LocalStore(root=local_store_dir)


def load_runs(store: LocalStore) -> list[EvalRunRecord]:
    loaded: list[EvalRunRecord] = []
    for run_dir in sorted(store.eval_runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir():
            continue
        report_path = run_dir / REPORT_JSON
        if not report_path.exists():
            continue
        report = EvalReport.model_validate_json(report_path.read_text(encoding="utf-8"))
        config_path = run_dir / CONFIG_JSON
        config: EvalConfigInput | None = None
        if config_path.exists():
            config = EvalConfigInput.model_validate_json(config_path.read_text(encoding="utf-8"))
        loaded.append(EvalRunRecord(run_id=run_dir.name, report=report, config=config))
    loaded.sort(key=lambda item: item.report.created_at, reverse=True)
    return loaded


def flatten_case_rows(runs: list[EvalRunRecord]) -> list[EvalCaseRow]:
    rows: list[EvalCaseRow] = []
    for run in runs:
        for case in run.report.cases:
            rows.append(
                EvalCaseRow(
                    run_id=run.run_id,
                    video_path=case.video_path,
                    video_hash=case.video_hash,
                    total_score=case.scores.total if case.scores else None,
                    ir_mode_score=case.scores.ir_mode if case.scores else None,
                    parking_spot_status_score=case.scores.parking_spot_status if case.scores else None,
                    send_notification_score=case.scores.send_notification if case.scores else None,
                    number_plate_score=case.scores.number_plate if case.scores else None,
                    events_description_score=case.scores.events_description if case.scores else None,
                    error=case.error,
                    case_result=case,
                )
            )
    return rows


def filter_case_rows(rows: list[EvalCaseRow], run_ids: list[str]) -> list[EvalCaseRow]:
    return [row for row in rows if row.run_id in run_ids]


def load_video_bytes(store: LocalStore, video_path: str) -> bytes | None:
    try:
        return store.get_video(video_path)
    except FileNotFoundError:
        logger.warning("Video file missing for results app: %s", video_path)
        return None


def build_case_comparison_rows(row: EvalCaseRow) -> list[dict[str, object]]:
    golden = row.case_result.golden
    prediction = row.case_result.prediction
    scores = row.case_result.scores

    return [
        {
            "field": "ir_mode",
            "golden": golden.ir_mode,
            "prediction": prediction.ir_mode if prediction is not None else None,
            "score": scores.ir_mode if scores is not None else None,
        },
        {
            "field": "parking_spot_status",
            "golden": golden.parking_spot_status,
            "prediction": prediction.parking_spot_status if prediction is not None else None,
            "score": scores.parking_spot_status if scores is not None else None,
        },
        {
            "field": "number_plate",
            "golden": golden.number_plate,
            "prediction": prediction.number_plate if prediction is not None else None,
            "score": scores.number_plate if scores is not None else None,
        },
        {
            "field": "send_notification",
            "golden": golden.send_notification,
            "prediction": prediction.send_notification if prediction is not None else None,
            "score": scores.send_notification if scores is not None else None,
        },
        {
            "field": "event_checklist",
            "golden": "\n".join(golden.event_checklist),
            "prediction": prediction.events_description if prediction is not None else None,
            "score": scores.events_description if scores is not None else None,
        },
        {
            "field": "people",
            "golden": golden.people,
            "prediction": None,
            "score": scores.people_score if scores is not None else None,
        },
    ]


def render_run_overview(runs: list[EvalRunRecord]) -> None:
    overview_rows: list[RunOverviewRow] = []
    for run in runs:
        overview_rows.append(
            RunOverviewRow(
                run_id=run.run_id,
                created_at=run.report.created_at,
                average_total_score=run.report.average_total_score,
                successful_cases=run.report.successful_cases,
                failed_cases=run.report.failed_cases,
                analysis_model=run.config.run_config.provider.model if run.config is not None else None,
                judge_model=run.config.judge.provider.model if run.config is not None else None,
            )
        )
    st.subheader("Run Overview")
    st.dataframe([row.model_dump(mode="json") for row in overview_rows], use_container_width=True, hide_index=True)


def render_case_table(rows: list[EvalCaseRow]) -> EvalCaseRow | None:
    display_rows = [
        row.model_dump(
            include={
                "run_id",
                "video_path",
                "total_score",
                "ir_mode_score",
                "parking_spot_status_score",
                "send_notification_score",
                "number_plate_score",
                "events_description_score",
                "error",
            }
        )
        for row in rows
    ]
    st.subheader("Run / Case Table")
    event = st.dataframe(
        display_rows,
        on_select="rerun",
        selection_mode="single-row",
    )
    selected_rows = event.selection.rows if event is not None else []
    if selected_rows:
        return rows[selected_rows[0]]

    return None


def render_case_detail(store: LocalStore, row: EvalCaseRow) -> None:
    st.subheader(f"Case Detail: {row.video_path}")
    title_col, score_col = st.columns([3, 1])
    with title_col:
        st.caption(f"Run: {row.run_id}")
    with score_col:
        if row.case_result.scores is not None:
            st.metric(
                "Total score",
                f"{row.case_result.scores.total:.3f}" if row.case_result.scores.total is not None else "-",
            )
        else:
            st.metric("Total score", "-")

    video_col, details_col = st.columns([1, 3])
    with video_col:
        video_bytes = load_video_bytes(store, row.video_path)
        if video_bytes is None:
            st.warning("Video file not found for this case.")
        else:
            st.video(video_bytes, autoplay=True, loop=True)

    with details_col:
        st.markdown("**Case comparison**")
        st.dataframe(
            build_case_comparison_rows(row),
            use_container_width=True,
            hide_index=True,
            column_config={
                "score": st.column_config.ProgressColumn("Score", max_value=1.0, min_value=0.0, format="%.1f"),
            },
        )

        if row.case_result.error:
            st.error(row.case_result.error)

        st.markdown("**Additional info**")
        st.dataframe(
            [
                {"field": "video_hash", "value": row.video_hash},
                {
                    "field": "people_status",
                    "value": row.case_result.scores.people_status if row.case_result.scores is not None else None,
                },
                {
                    "field": "covered_items",
                    "value": row.case_result.judge.covered_items if row.case_result.judge is not None else None,
                },
                {
                    "field": "contradicted_items",
                    "value": row.case_result.judge.contradicted_items if row.case_result.judge is not None else None,
                },
                {
                    "field": "judge_rationale",
                    "value": row.case_result.judge.rationale if row.case_result.judge is not None else None,
                },
            ],
            use_container_width=True,
            hide_index=True,
        )


def main() -> None:
    st.set_page_config(page_title="Eval Results", layout="wide")
    store = get_local_store()
    runs = load_runs(store)
    st.title("Eval Results")

    if not runs:
        st.info(f"No eval runs found under {store.eval_runs_dir}")
        return

    render_run_overview(runs)
    run_ids = [run.run_id for run in runs]
    with st.sidebar:
        selected_run_ids = st.multiselect("Runs", run_ids, default=run_ids)
    rows = flatten_case_rows(runs)
    filtered_rows = filter_case_rows(rows, selected_run_ids)
    selected_row = render_case_table(filtered_rows)

    if not filtered_rows or selected_row is None:
        st.info("No cases match the current run selection.")
        return

    st.caption(f"Selected case: {selected_row.video_path} ({selected_row.run_id})")
    render_case_detail(store, selected_row)


if __name__ == "__main__":
    main()
