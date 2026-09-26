"""Example source documents anyone can build a retreat from (samples/examples), so a
first-time user can see what a good source looks like and watch one being made."""

import json
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent.parent / "samples" / "examples"


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    try:
        items = json.loads((DIR / "examples.json").read_text())
    except (OSError, ValueError):
        return {}
    return {e["slug"]: e for e in (items.get("examples", items) if isinstance(items, dict) else items)}


def public(e: dict) -> dict:
    """What the New retreat page shows for an example."""
    slug = e["slug"]
    out = {k: e.get(k) for k in ("slug", "title", "subtitle", "description", "days", "credits")}
    out["pdf_url"] = f"/api/examples/{slug}.pdf"
    out["cover_url"] = f"/api/examples/{slug}/cover.jpg" if e.get("cover_image") else None
    out["txt_url"] = f"/api/examples/{slug}.txt" if file_for(slug, "txt") else None
    return out


def file_for(slug: str, kind: str) -> Path | None:
    """The example's pdf, txt or cover image on disk, if it has one."""
    e = catalog().get(slug)
    if not e:
        return None
    rel = {"pdf": e.get("file"), "cover": e.get("cover_image"), "txt": e.get("txt") or f"{slug}.txt"}[kind]
    path = (DIR / rel).resolve() if rel else None
    return path if path and path.is_file() and DIR in path.parents else None
