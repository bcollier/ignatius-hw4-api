"""Retreats: the library, making one, reading one, its research notes, its printable
script, renaming or re-dating it, and deleting it."""

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import config, demos, examples, google_docs, llm_log, pipeline, prompts, script_pdf
from ..access import my_retreat, readable_retreat, save_retreat, view_of
from ..auth import User, current_user
from ..checks import BuildRequest, check_date, check_model, check_prompt, check_series, check_title, resolve_build
from ..extract import ExtractError, extract
from ..storage import StorageError, store, summary
from .uploads import read_upload, too_big

router = APIRouter(prefix="/api/retreats")
PDF_SLUG_CHARS = 60


# ---------------------------------------------------------------- the library


@router.get("")
async def list_retreats(user: User = Depends(current_user)):
    """Your retreats, and the example retreats (with your own progress in them).
    Examples are always listed as examples, even for the account that built them."""
    registered = await demos.registry()
    mine = [r for r in await store.list_for(user.id) if r["id"] not in registered]
    examples_list = await _example_summaries(user, registered)
    await _add_cover_urls(mine + examples_list)
    shown = [r for r in examples_list if not r.pop("hidden")]
    hidden = [{"id": r["id"], "title": r["title"]} for r in examples_list if r not in shown]
    return {"retreats": mine, "examples": shown, "hidden_examples": hidden}


async def _example_summaries(user: User, registered: dict) -> list[dict]:
    state = await demos.load_state(user.id) if registered else {}
    out = []
    for rid, meta in registered.items():
        retreat = await pipeline.get(rid)
        if retreat and retreat.get("status") == "ready":
            out.append({**summary(demos.personal(retreat, state.get(rid), meta)), "demo": meta, "read_only": True,
                        "hidden": bool((state.get(rid) or {}).get("hidden"))})
    return out


async def _add_cover_urls(summaries: list[dict]) -> None:
    """Each summary's cover image path becomes a URL (one batch of signed URLs)."""
    urls = await store.urls([p for r in summaries if (p := r.get("cover_path"))])
    for r in summaries:
        r["cover"] = urls.get(r.pop("cover_path", None))


# ---------------------------------------------------------------- making a retreat


@router.post("", status_code=202)
async def create_retreat(
    file: UploadFile | None = File(None),
    example: str = Form(""),
    google_doc: str = Form(""),
    plan_prompt: str = Form(""),
    model: str = Form(""),
    series_ids: str = Form("", alias="series"),
    options: str = Form(""),
    start_date: str = Form(""),
    user: User = Depends(current_user),
):
    """Upload and make a retreat. With `options` (the build settings as JSON, the same
    fields as a day build), every day is made right after planning: one request, and
    the retreat comes back ready to pray. Without it, only the plan is made.
    Instead of a file, `example` names one of the example documents (/api/examples),
    ("be-still", or "be-still.txt" for its plain-text version), or `google_doc` is the
    link to a Google Doc shared as "Anyone with the link can view"."""
    # Everything is checked before the upload is read, so a bad option fails fast.
    plan_prompt = check_prompt(plan_prompt, prompts.PLAN_INSTRUCTIONS, "planning")
    model = check_model(model, user)
    build_options = _build_options(options, user)
    start = check_date(start_date)
    filename, data = await _source_bytes(file, example, google_doc)
    try:
        source = extract(filename, data)
    except ExtractError as exc:
        raise HTTPException(400, str(exc)) from exc
    llm_log.tag(email=user.log_email)  # inherited by the planning job
    ids = await check_series(series_ids, user)
    retreat = await pipeline.create_retreat(
        user.id, filename, source, plan_prompt, model,
        email=user.log_email, series_ids=ids, build_options=build_options, start_date=start,
    )
    return await pipeline.public_view(retreat)


def _build_options(raw: str, user: User) -> dict | None:
    if not raw.strip():
        return None
    try:
        body = BuildRequest.model_validate_json(raw)
    except ValueError as exc:
        raise HTTPException(400, "The build options couldn't be read.") from exc
    return resolve_build(body, user)


async def _source_bytes(file: UploadFile | None, example: str, google_doc: str = "") -> tuple[str, bytes]:
    """The document to plan from: an example on the server, a shared Google Doc, or the uploaded file."""
    if google_doc.strip():
        try:
            return await google_docs.fetch(google_doc)
        except google_docs.GoogleDocError as exc:
            raise HTTPException(400, str(exc)) from exc
    if example:
        kind = "txt" if example.endswith(".txt") else "pdf"
        path = examples.file_for(example.removesuffix(".txt"), kind)
        if not path:
            raise HTTPException(404, "No such example.")
        filename, data = path.name, path.read_bytes()
    elif file is None:
        raise HTTPException(400, "Choose a file to upload, a Google Doc, or an example.")
    else:
        filename, data = file.filename or "upload", await read_upload(file)
    if too_big(len(data)):
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    if not data:
        raise HTTPException(400, "The uploaded file is empty.")
    return filename, data


# ---------------------------------------------------------------- reading a retreat


@router.get("/{retreat_id}")
async def read_retreat(retreat: dict = Depends(readable_retreat)):
    return await view_of(retreat)


@router.get("/{retreat_id}/research")
async def research(retreat: dict = Depends(readable_retreat)):
    """Research done for this retreat: for each day, the passage and notes it was made
    from, the searches run for the deep dive, every result, and which were cited."""
    plan = retreat.get("plan") or {}
    days = [await _research_for_day(retreat, d) for d in plan.get("days", [])]
    return {"id": retreat["id"], "title": plan.get("title"), "model": retreat.get("model"),
            "source_filename": retreat.get("filename"), "days": days}


async def _research_for_day(retreat: dict, d: dict) -> dict:
    state = retreat["days"].get(str(d["day"]), {})
    deep = state.get("tracks", {}).get("deep", {})
    return {
        "day": d["day"], "title": d.get("title"), "source_ref": d.get("source_ref"),
        "passage_text": d.get("passage_text"),
        "notes": {k: d.get(k) for k in ("theme", "grace", "image_description") if d.get(k)},
        "status": state.get("status"), "web_search": deep.get("web_search"), "research_service": deep.get("research"),
        "cited": deep.get("sources", []), "research": await _saved_research(deep.get("research_path")),
    }


async def _saved_research(path: str | None) -> dict | None:
    """The day's research record, if one was saved (days made before it existed have none)."""
    if not path:
        return None
    try:
        return json.loads(await store.get_file(path))
    except (StorageError, ValueError):
        return None


@router.get("/{retreat_id}/script.pdf")
async def script(
    retreat: dict = Depends(readable_retreat),
    day: int | None = None,
    order: str = "lectio",
    grace_silence: int = 15,
    pause: int = 30,
):
    """The printable script: one day (?day=n) or the whole retreat, in the same order
    and with the same silences the player uses."""
    if retreat["status"] != "ready":
        raise HTTPException(409, "The retreat plan isn't ready yet.")
    if order not in ("lectio", "simple"):
        raise HTTPException(400, "order must be 'lectio' or 'simple'.")
    days = _days_to_print(retreat, day)
    sections = [] if day is not None else [script_pdf.cover_html(retreat, days, await _series_titles(retreat))]
    images: dict[str, bytes] = {}
    for d in days:
        name = await _load_day_image(retreat, d, images)
        state = retreat["days"].get(str(d["day"]))
        sections.append(script_pdf.day_html(retreat, d, state, order, grace_silence, pause, name))
    pdf = script_pdf.render(sections, images)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{_pdf_filename(retreat, day)}"'})


def _days_to_print(retreat: dict, day: int | None) -> list[dict]:
    days = retreat["plan"]["days"]
    if day is None:
        return days
    days = [d for d in days if d["day"] == day]
    if not days:
        raise HTTPException(404, f"This retreat has no day {day}.")
    return days


async def _series_titles(retreat: dict) -> list[str]:
    """Titles of the earlier weeks, for the cover of a whole-retreat PDF."""
    titles = []
    for rid in retreat.get("series", []):
        earlier = await pipeline.get(rid)
        if earlier and earlier["user_id"] == retreat["user_id"] and earlier.get("plan"):
            titles.append(earlier["plan"]["title"])
    return titles


async def _load_day_image(retreat: dict, d: dict, images: dict[str, bytes]) -> str | None:
    """Add the day's image to `images` (once) and return its name in the PDF."""
    if d["image_index"] < 0:
        return None
    name = f"image{d['image_index']}.jpg"
    if name not in images:
        images[name] = await store.get_file(retreat["images"][d["image_index"]]["path"])
    return name


def _pdf_filename(retreat: dict, day: int | None) -> str:
    slug = "".join(c if c.isalnum() else "-" for c in retreat["plan"]["title"].lower()).strip("-")[:PDF_SLUG_CHARS]
    slug = slug or "retreat"
    return f"{slug}-day-{day}.pdf" if day is not None else f"{slug}.pdf"


# ---------------------------------------------------------------- changing and deleting


class RetreatPatch(BaseModel):
    start_date: str | None = None
    title: str | None = None


@router.patch("/{retreat_id}")
async def update_retreat(body: RetreatPatch, retreat: dict = Depends(readable_retreat)):
    """Change the start date (which day is 'today') or the title."""
    if body.start_date is not None:
        retreat["start_date"] = check_date(body.start_date)
    if body.title is not None and retreat.get("read_only"):
        raise HTTPException(403, "Example retreats can't be renamed.")
    if body.title is not None and retreat.get("plan"):
        retreat["plan"]["title"] = check_title(body.title)
    await save_retreat(retreat)
    return await view_of(retreat)


@router.delete("/{retreat_id}")
async def delete_retreat(retreat: dict = Depends(my_retreat)):
    if retreat["id"] in await demos.registry():
        raise HTTPException(409, "This is an example retreat that everyone sees. Unregister it before deleting it.")
    if retreat["id"] in pipeline.active:
        raise HTTPException(409, "Wait for the current job to finish before deleting this retreat.")
    await store.delete(retreat)
    return {"deleted": retreat["id"]}


class HideRequest(BaseModel):
    hidden: bool = True


@router.post("/{retreat_id}/hidden")
async def hide_example(body: HideRequest, retreat: dict = Depends(readable_retreat), user: User = Depends(current_user)):
    """Take an example off this person's home page (or put it back). Only for examples;
    it's their own choice, kept with their progress in it."""
    if retreat["id"] not in await demos.registry():
        raise HTTPException(400, "Only example retreats can be hidden.")
    state = await demos.load_state(user.id)
    mine = state.setdefault(retreat["id"], {})
    mine["hidden"] = body.hidden
    await demos.save_state(user.id, state)
    return {"id": retreat["id"], "hidden": body.hidden}
