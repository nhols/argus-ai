from vid_analyser.db import Database


async def build_live_note_instructions(*, db: Database | None) -> str | None:
    if db is None:
        return None
    notes = await db.get_active_operator_notes()
    if not notes:
        return None
    return "\n".join(
        [
            "Active notes set by the Telegram operator. Treat these as important user-provided context "
            "and apply them when relevant:",
        ]
        + [
            f"- expires_at={note.expires_at.isoformat()} created_by={note.created_by or 'unknown'}: "
            f"{note.note_text}"
            for note in notes
        ]
    )
