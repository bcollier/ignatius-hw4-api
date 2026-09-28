"""Bring in a Google Doc as a retreat's source: the document is exported as Word
(.docx, pictures included) through its public export link, so no Google sign-in is
needed. It must be shared as "Anyone with the link can view"."""

import re
from urllib.parse import unquote

import httpx

from . import config

DOC_ID = re.compile(r"(?:docs\.google\.com/document/(?:u/\d+/)?d/|^)([A-Za-z0-9_-]{25,})")
EXPORT_URL = "https://docs.google.com/document/d/{id}/export?format=docx"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TIMEOUT = 60
NOT_SHARED = ("That Google Doc isn't shared publicly, so it can't be read. In Google Docs choose Share, "
              "set General access to \"Anyone with the link\" (Viewer), then paste the link again.")


class GoogleDocError(Exception):
    """A message that can be shown to the person as it is."""


def doc_id(link: str) -> str:
    match = DOC_ID.search(link.strip())
    if not match:
        raise GoogleDocError("That doesn't look like a Google Docs link. It should look like "
                             "https://docs.google.com/document/d/…/edit")
    return match.group(1)


async def fetch(link: str) -> tuple[str, bytes]:
    """(filename, .docx bytes) for a shared Google Doc."""
    url = EXPORT_URL.format(id=doc_id(link))
    limit = config.MAX_UPLOAD_MB * 1024 * 1024
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as http:
            async with http.stream("GET", url) as response:
                if response.status_code in (401, 403, 404) or DOCX_TYPE not in response.headers.get("content-type", ""):
                    # A private document redirects to a sign-in page instead of the file.
                    raise GoogleDocError(NOT_SHARED)
                if response.status_code >= 400:
                    raise GoogleDocError(f"Google Docs returned an error ({response.status_code}).")
                data = bytearray()
                async for chunk in response.aiter_bytes():  # read no more than the limit
                    data += chunk
                    if len(data) > limit:
                        raise GoogleDocError(f"That document is larger than {config.MAX_UPLOAD_MB} MB.")
                disposition = response.headers.get("content-disposition", "")
    except httpx.HTTPError as exc:
        raise GoogleDocError("Google Docs didn't answer. Try again in a moment.") from exc
    return _filename(disposition), bytes(data)


def _filename(disposition: str) -> str:
    """The document's title, from the export's Content-Disposition header."""
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition) or re.search(r'filename="([^"]+)"', disposition)
    name = unquote(match.group(1)) if match else "Google Doc.docx"
    return name if name.lower().endswith(".docx") else name + ".docx"
