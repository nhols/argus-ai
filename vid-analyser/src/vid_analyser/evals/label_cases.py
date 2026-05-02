from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

SRC_DIR = Path(__file__).resolve().parents[2]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.vid_analyser import ParkingSpotStatus, VidAnalysis  # noqa: E402
from vid_analyser.evals import VideoEvalCase, load_video_case, make_video_case, save_video_case  # noqa: E402

PARKING_STATUSES: tuple[ParkingSpotStatus, ...] = (
    "occupied",
    "vacant",
    "car entering",
    "car leaving",
    "unknown",
)


@dataclass(frozen=True)
class EvalDataPaths:
    repo_root: Path
    eval_data_dir: Path
    videos_dir: Path
    cases_dir: Path


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "eval_data").is_dir():
            return parent
    raise FileNotFoundError("Could not find repo root containing eval_data")


def _eval_data_paths() -> EvalDataPaths:
    repo_root = _repo_root()
    eval_data_dir = repo_root / "eval_data"
    return EvalDataPaths(
        repo_root=repo_root,
        eval_data_dir=eval_data_dir,
        videos_dir=eval_data_dir / "videos",
        cases_dir=eval_data_dir / "cases",
    )


def _case_path(eval_data_dir: Path, video_path: Path) -> Path:
    return eval_data_dir / "cases" / f"{video_path.stem}.json"


@st.cache_data(show_spinner=False)
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@st.cache_data(show_spinner=False)
def _video_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _video_rows(eval_data_dir: Path, videos: list[Path]) -> list[dict[str, str | bool]]:
    return [
        {
            "labelled": _case_path(eval_data_dir, video).exists(),
            "filename": video.name,
        }
        for video in videos
    ]


def _load_existing_case(case_file: Path) -> VideoEvalCase | None:
    if not case_file.exists():
        return None
    return load_video_case(case_file, videos_dir=case_file.parents[1] / "videos")


@st.cache_data(show_spinner=False)
def _dataset_tags(cases_dir: Path, videos_dir: Path) -> list[str]:
    tags: set[str] = set()
    for case_file in sorted(cases_dir.glob("*.json")):
        case = load_video_case(case_file, videos_dir=videos_dir)
        if case.metadata is not None:
            tags.update(case.metadata.tags)
    return sorted(tags)


def _filter_videos(paths: EvalDataPaths, videos: list[Path]) -> list[Path]:
    st.sidebar.title("Videos")
    filter_mode = st.sidebar.segmented_control("Filter", ("all", "unlabelled", "labelled"), default="all")
    if filter_mode == "unlabelled":
        return [video for video in videos if not _case_path(paths.eval_data_dir, video).exists()]
    elif filter_mode == "labelled":
        return [video for video in videos if _case_path(paths.eval_data_dir, video).exists()]
    return videos


def _render_labelling_progress(paths: EvalDataPaths, video_count: int) -> None:
    labelled_count = len(list(paths.cases_dir.glob("*.json"))) if paths.cases_dir.exists() else 0
    if video_count == 0:
        return
    st.sidebar.progress(labelled_count / video_count, text=f"{labelled_count} labelled / {video_count} videos")


def _select_video(paths: EvalDataPaths, visible_videos: list[Path]) -> Path | None:
    if not visible_videos:
        st.info("No videos match the current filter.")
        return None

    selection = st.sidebar.dataframe(
        _video_rows(paths.eval_data_dir, visible_videos),
        height="content",
        hide_index=True,
        column_config={
            "labelled": st.column_config.CheckboxColumn("Labelled", disabled=True),
            "filename": st.column_config.TextColumn("Video"),
        },
        on_select="rerun",
        selection_mode="single-row",
        width="stretch",
    )
    selected_rows = selection.selection.rows  # type: ignore
    if selected_rows:
        selected_video = visible_videos[selected_rows[0]]
        st.session_state.selected_eval_video = selected_video.name
        return selected_video
    else:
        previous = st.session_state.get("selected_eval_video")
        return next((video for video in visible_videos if video.name == previous), visible_videos[0])


def _render_video(paths: EvalDataPaths, selected_video: Path) -> None:
    st.caption(str(selected_video.relative_to(paths.repo_root)))
    video_col, form_col = st.columns([3, 2], gap="large")
    with video_col:
        st.video(_video_bytes(selected_video), autoplay=True, loop=True)

    with form_col:
        _render_case_form(paths, selected_video)


def _render_case_form(paths: EvalDataPaths, selected_video: Path) -> None:
    case_file = _case_path(paths.eval_data_dir, selected_video)
    existing_case = _load_existing_case(case_file)
    existing_output = existing_case.expected_output if existing_case is not None else None
    existing_tags = existing_case.metadata.tags if existing_case is not None and existing_case.metadata else []
    tag_options = sorted(set(_dataset_tags(paths.cases_dir, paths.videos_dir)) | set(existing_tags))
    video_hash = _sha256(selected_video)

    st.subheader(selected_video.name)
    st.text_input("Video hash", value=video_hash, disabled=True)

    current_status = existing_output.parking_spot_status if existing_output is not None else "unknown"
    with st.form(key=f"case-form-{selected_video.stem}"):
        ir_mode = st.checkbox("IR / night vision", value=existing_output.ir_mode if existing_output else False)
        parking_spot_status = st.selectbox(
            "Parking spot status",
            PARKING_STATUSES,
            index=PARKING_STATUSES.index(current_status),
        )
        number_plate = st.text_input(
            "Number plate",
            value=(existing_output.number_plate or "") if existing_output else "",
        )
        events_description = st.text_area(
            "Events description",
            value=existing_output.events_description if existing_output else "",
            height=180,
        )
        tags = st.multiselect(
            "Tags",
            options=tag_options,
            default=existing_tags,
            accept_new_options=True,
        )
        submitted = st.form_submit_button("Save case", type="primary")

    if submitted:
        _save_case(
            paths=paths,
            case_file=case_file,
            selected_video=selected_video,
            video_hash=video_hash,
            ir_mode=ir_mode,
            parking_spot_status=parking_spot_status,
            number_plate=number_plate,
            events_description=events_description,
            tags=tags,
        )


def _save_case(
    *,
    paths: EvalDataPaths,
    case_file: Path,
    selected_video: Path,
    video_hash: str,
    ir_mode: bool,
    parking_spot_status: ParkingSpotStatus,
    number_plate: str,
    events_description: str,
    tags: list[str],
) -> None:
    expected_output = VidAnalysis(
        ir_mode=ir_mode,
        parking_spot_status=parking_spot_status,
        number_plate=number_plate.strip() or None,
        events_description=events_description.strip(),
    )
    case = make_video_case(
        name=selected_video.stem,
        filename=selected_video.name,
        expected_output=expected_output,
        video_hash=video_hash,
        tags=sorted({tag.strip() for tag in tags if tag.strip()}),
    )
    save_video_case(case_file, case)
    st.success(f"Saved {case_file.relative_to(paths.repo_root)}")
    _dataset_tags.clear()


def main() -> None:
    st.set_page_config(page_title="Eval Case Labeller", layout="wide")

    paths = _eval_data_paths()
    videos = sorted(paths.videos_dir.glob("*.mp4"))
    if not videos:
        st.error(f"No videos found in {paths.videos_dir}")
        return

    visible_videos = _filter_videos(paths, videos)
    _render_labelling_progress(paths, len(videos))

    selected_video = _select_video(paths, visible_videos)
    if selected_video is None:
        return

    _render_video(paths, selected_video)


if __name__ == "__main__":
    main()
