import asyncio
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from vid_analyser.agent.notes import build_live_note_instructions  # noqa: E402
from vid_analyser.agent.notifier import get_live_operator_notes  # noqa: E402
from vid_analyser.api.app import app as _api_app  # noqa: E402, F401
from vid_analyser.agent.telegram_operator import (  # noqa: E402
    add_operator_note,
    inject_live_note_context,
)
from vid_analyser.db import init_database  # noqa: E402


def test_only_live_operator_notes_are_loaded_into_both_agents(tmp_path):
    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        now = datetime.now(UTC)
        await db.insert_operator_note(
            note_text="The side gate is being repaired.",
            expires_at=now + timedelta(hours=2),
            created_by="Neil",
        )
        await db.insert_operator_note(
            note_text="This old note must not be loaded.",
            expires_at=now - timedelta(minutes=1),
            created_by="Neil",
        )

        active_notes = await db.get_active_operator_notes(now=now)
        rendered = await build_live_note_instructions(db=db)
        notifier_context = await get_live_operator_notes(
            cast(Any, SimpleNamespace(deps=SimpleNamespace(db=db)))
        )
        operator_context = await inject_live_note_context(
            cast(Any, SimpleNamespace(deps=SimpleNamespace(db=db)))
        )
        return active_notes, rendered, notifier_context, operator_context

    active_notes, rendered, notifier_context, operator_context = asyncio.run(_run())

    assert [note.note_text for note in active_notes] == [
        "The side gate is being repaired."
    ]
    assert rendered is not None
    assert "The side gate is being repaired." in rendered
    assert "This old note must not be loaded." not in rendered
    assert "expires_at=" in rendered
    assert notifier_context == rendered
    assert operator_context == rendered


def test_telegram_operator_can_add_a_note_with_an_expiry(tmp_path):
    async def _run():
        db = await init_database(str(tmp_path / "vid-analyser.db"))
        ctx = SimpleNamespace(
            deps=SimpleNamespace(
                db=db,
                sender_display_name="Neil",
                sender_username=None,
                sender_user_id="123",
            )
        )
        result = await add_operator_note(
            cast(Any, ctx),
            note_text="  A parcel is expected today.  ",
            expires_at=datetime.now(UTC) + timedelta(hours=6),
        )
        return result, await db.get_active_operator_notes()

    result, notes = asyncio.run(_run())

    assert result["created"] is True
    assert len(notes) == 1
    assert notes[0].note_text == "A parcel is expected today."
    assert notes[0].created_by == "Neil"
