"""Example source documents (samples/examples) to look at and build a retreat from."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import examples

router = APIRouter(prefix="/api/examples")
MEDIA_TYPES = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8"}


@router.get("")
def list_examples():
    """Example documents to build a retreat from (and to look at first)."""
    return {"examples": [examples.public(e) for e in examples.catalog().values()]}


@router.get("/{slug}.{kind}")
def example_file(slug: str, kind: str):
    path = examples.file_for(slug, kind if kind in MEDIA_TYPES else "")
    if not path:
        raise HTTPException(404, "No such example.")
    return FileResponse(path, media_type=MEDIA_TYPES[kind],
                        headers={"Content-Disposition": f'inline; filename="{path.name}"'})


@router.get("/{slug}/cover.jpg")
def example_cover(slug: str):
    path = examples.file_for(slug, "cover")
    if not path:
        raise HTTPException(404, "No such example.")
    return FileResponse(path, media_type="image/jpeg")
