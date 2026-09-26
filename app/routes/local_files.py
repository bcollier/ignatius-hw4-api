"""Local development only: serves stored files. With Supabase, files are served by
signed Storage URLs instead and this router isn't added."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..storage import StorageError, store

router = APIRouter()


@router.get("/api/files/{path:path}")
def read_file(path: str):
    try:
        target = store.local_path(path)
    except StorageError:
        raise HTTPException(404, "File not found.")
    if not target.is_file():
        raise HTTPException(404, "File not found.")
    media = "audio/mpeg" if target.suffix == ".mp3" else "image/jpeg"
    return FileResponse(target, media_type=media)
