"""The build log: every step and call in making a retreat, for the terminal on the
retreat page (live, a few rows at a time) and as a complete download."""

import re

from fastapi import APIRouter, Depends

from .. import pipeline
from ..access import readable_retreat
from ..auth import User, current_user
from ..storage import store

router = APIRouter()

PRIVATE_NOTES = re.compile(r"<about_the_person>.*?</about_the_person>", re.S)
PREVIEW_CHARS = 1200  # the live view shows this much of each prompt and response
LIVE_PAGE_ROWS = 200
FULL_PAGE_ROWS = 1000
ROW_FIELDS = ("id", "created_at", "day", "purpose", "provider", "model", "input_tokens", "output_tokens",
              "web_searches", "usd", "duration_ms", "status", "error")


@router.get("/api/retreats/{retreat_id}/log")
async def build_log(retreat: dict = Depends(readable_retreat), user: User = Depends(current_user), after: int = 0,
                    full: bool = False):
    """Every step and call in making this retreat, in order: the steps the app took,
    each model and search call (what was sent, what came back) and each recording.
    `after` returns only newer rows (for watching live); `full` is the complete record."""
    owner = retreat.get("user_id") == user.id and not retreat.get("read_only")
    rows = await _rows_after(retreat["id"], after, full)
    return {"rows": [log_row(r, full, owner) for r in rows], "busy": pipeline._busy(retreat)}


async def _rows_after(retreat_id: str, after: int, everything: bool) -> list[dict]:
    """One page of newer rows for the live view; every page for the full record."""
    limit = FULL_PAGE_ROWS if everything else LIVE_PAGE_ROWS
    rows = []
    while True:
        page = await store.call_log(retreat_id, after, limit)
        rows += page
        if not everything or len(page) < limit:
            return rows
        after = page[-1]["id"]


def log_row(row: dict, full: bool, owner: bool) -> dict:
    """One step or call for the build log. The live view gets short previews; the
    download gets everything. The owner's About me notes (in every prompt) are hidden
    from anyone else, e.g. people watching how an example was made."""
    def clean(text):
        return _preview(text if owner else _hide_private_notes(text), full)

    request = row.get("request") or {}
    system = request.get("system") or ""
    out = {k: row.get(k) for k in ROW_FIELDS}
    out.update(system=clean(system), prompt=clean(_prompt_text(request.get("messages") or [])),
               response=clean(row.get("response_text") or ""), details=row.get("response") if full else None)
    if not full:
        out["system_chars"] = len(system)
        out.pop("system")
    return out


def _hide_private_notes(text):
    if not isinstance(text, str):
        return text
    return PRIVATE_NOTES.sub("<about_the_person>[private]</about_the_person>", text)


def _preview(text, full: bool):
    if full or not isinstance(text, str) or len(text) <= PREVIEW_CHARS:
        return text
    return text[:PREVIEW_CHARS] + f"… [{len(text) - PREVIEW_CHARS:,} more characters]"


def _prompt_text(messages: list) -> str:
    """The messages sent, as plain text (images shown as [image])."""
    def content(m: dict) -> str:
        if isinstance(m.get("content"), str):
            return m["content"]
        return "\n".join(b.get("text", f"[{b.get('type')}]") for b in m.get("content", []) if isinstance(b, dict))

    return "\n\n".join(content(m) for m in messages if isinstance(m, dict))
