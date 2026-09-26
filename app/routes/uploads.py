"""Reading an uploaded file safely: never more than the size limit into memory."""

from fastapi import HTTPException, UploadFile

from .. import config


def too_big(size: int) -> bool:
    return size > config.MAX_UPLOAD_MB * 1024 * 1024


async def read_upload(file: UploadFile) -> bytes:
    # One byte over the limit is enough to know it's too big.
    data = await file.read(config.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    if too_big(len(data)):
        raise HTTPException(413, f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    return data
