"""Where retreats and their files live.

SupabaseStore keeps each retreat as a JSON row in Postgres and its images and MP3s
in a private Storage bucket, handing the browser short-lived signed URLs. It is
used whenever SUPABASE_URL and SUPABASE_SECRET_KEY are set.

LocalStore keeps everything under DATA_DIR and serves files from /api/files. It
is for tests and local development without a Supabase project.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import config

log = logging.getLogger(__name__)

SIGNED_URL_SECONDS = 24 * 3600


class StorageError(RuntimeError):
    """Saving or loading failed; the message is safe to show to the user."""


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def summary(retreat: dict) -> dict:
    days = retreat.get("days", {})
    prayed = [d.get("prayed_at") for d in days.values() if d.get("prayed_at")]
    plan_days = {str(d["day"]): d for d in (retreat.get("plan") or {}).get("days", [])}
    return {
        "id": retreat["id"],
        "title": (retreat.get("plan") or {}).get("title") or retreat["filename"],
        "filename": retreat["filename"],
        "created_at": retreat["created_at"],
        "status": retreat["status"],
        "days": len(days),
        "days_built": sum(1 for d in days.values() if d["status"] == "ready"),
        "days_prayed": len(prayed),
        "last_prayed_at": max(prayed) if prayed else None,
        "series": retreat.get("series", []),
        "start_date": retreat.get("start_date"),
        "progress": retreat.get("progress"),
        # One entry per day, for the library's day chips and the Continue card.
        "day_states": [
            {
                "day": int(n),
                "title": plan_days.get(n, {}).get("title", ""),
                "status": d["status"],
                "prayed_at": d.get("prayed_at"),
                "started": bool((d.get("listening") or {}).get("parts_played")),
                "finished": bool((d.get("listening") or {}).get("finished_at")),
            }
            for n, d in sorted(days.items(), key=lambda kv: int(kv[0]))
        ],
    }


def file_paths(retreat: dict) -> list[str]:
    paths = [img["path"] for img in retreat.get("images", [])]
    for day in retreat.get("days", {}).values():
        paths += [t["path"] for t in day.get("tracks", {}).values() if t.get("path")]
        paths += [t["research_path"] for t in day.get("tracks", {}).values() if t.get("research_path")]
    return paths


class LocalStore:
    def __init__(self, root: Path):
        self.rows = root / "retreats"
        self.files = root / "files"
        self.rows.mkdir(parents=True, exist_ok=True)
        self.files.mkdir(parents=True, exist_ok=True)

    async def setup(self) -> None:
        pass

    async def save(self, retreat: dict) -> None:
        (self.rows / f"{retreat['id']}.json").write_text(json.dumps(retreat))

    async def load(self, retreat_id: str) -> dict | None:
        path = self.rows / f"{retreat_id}.json"
        return json.loads(path.read_text()) if path.is_file() else None

    async def list_for(self, user_id: str) -> list[dict]:
        rows = [json.loads(p.read_text()) for p in self.rows.glob("*.json")]
        mine = [summary(r) for r in rows if r["user_id"] == user_id]
        return sorted(mine, key=lambda r: r["created_at"], reverse=True)

    async def delete(self, retreat: dict) -> None:
        for path in file_paths(retreat):
            self.local_path(path).unlink(missing_ok=True)
        (self.rows / f"{retreat['id']}.json").unlink(missing_ok=True)

    async def put_file(self, path: str, data: bytes, mime: str) -> None:
        target = self.local_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    async def get_file(self, path: str) -> bytes:
        try:
            return self.local_path(path).read_bytes()
        except OSError as exc:  # same contract as SupabaseStore: missing files raise StorageError
            raise StorageError("File not found.") from exc

    async def log_llm_call(self, row: dict) -> None:
        row = {"created_at": datetime.now(timezone.utc).isoformat(), **row}
        with open(self.rows.parent / "llm_calls.jsonl", "a") as f:
            f.write(json.dumps(row) + "\n")

    async def urls(self, paths: list[str]) -> dict[str, str]:
        return {p: f"/api/files/{p}?v={int(self.local_path(p).stat().st_mtime)}" for p in paths if self.local_path(p).exists()}

    def local_path(self, path: str) -> Path:
        target = (self.files / path).resolve()
        if not target.is_relative_to(self.files.resolve()):
            raise StorageError("Bad file path.")
        return target


class SupabaseStore:
    def __init__(self, url: str, secret_key: str, bucket: str):
        self.url = url.rstrip("/")
        self.bucket = bucket
        headers = {"apikey": secret_key}
        if secret_key.startswith("eyJ"):  # legacy service_role JWT keys also go in Authorization
            headers["Authorization"] = f"Bearer {secret_key}"
        self.http = httpx.AsyncClient(base_url=self.url, headers=headers, timeout=60)
        self._signed: dict[str, tuple[str, float]] = {}  # path -> (url, expires_at)

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise StorageError("Couldn't reach the database.") from exc
        if response.status_code >= 400:
            log.error("supabase %s %s -> %s %s", method, path, response.status_code, response.text[:300])
            raise StorageError("The database rejected a request.")
        return response

    async def setup(self) -> None:
        """Create the private bucket on first run."""
        response = await self.http.get(f"/storage/v1/bucket/{self.bucket}")
        if response.status_code == 200:
            return
        await self._request("POST", "/storage/v1/bucket", json={"id": self.bucket, "name": self.bucket, "public": False})
        log.info("created storage bucket %s", self.bucket)

    async def save(self, retreat: dict) -> None:
        row = {
            "id": retreat["id"],
            "user_id": retreat["user_id"],
            "title": summary(retreat)["title"],
            "created_at": _iso(retreat["created_at"]),
            "updated_at": _iso(time.time()),
            "data": retreat,
        }
        await self._request(
            "POST", "/rest/v1/retreats", json=row, headers={"Prefer": "resolution=merge-duplicates,return=minimal"}
        )

    async def load(self, retreat_id: str) -> dict | None:
        response = await self._request("GET", "/rest/v1/retreats", params={"id": f"eq.{retreat_id}", "select": "data"})
        rows = response.json()
        return rows[0]["data"] if rows else None

    async def list_for(self, user_id: str) -> list[dict]:
        response = await self._request(
            "GET",
            "/rest/v1/retreats",
            params={"user_id": f"eq.{user_id}", "select": "data", "order": "created_at.desc", "limit": "100"},
        )
        return [summary(row["data"]) for row in response.json()]

    async def delete(self, retreat: dict) -> None:
        paths = file_paths(retreat)
        if paths:
            await self._request("DELETE", f"/storage/v1/object/{self.bucket}", json={"prefixes": paths})
        await self._request("DELETE", "/rest/v1/retreats", params={"id": f"eq.{retreat['id']}"})

    async def put_file(self, path: str, data: bytes, mime: str) -> None:
        await self._request(
            "POST",
            f"/storage/v1/object/{self.bucket}/{path}",
            content=data,
            headers={"Content-Type": mime, "x-upsert": "true"},
        )
        self._signed.pop(path, None)

    async def get_file(self, path: str) -> bytes:
        try:
            response = await self.http.get(f"/storage/v1/object/{self.bucket}/{path}")
        except httpx.HTTPError as exc:
            raise StorageError("Couldn't reach file storage.") from exc
        if response.status_code >= 400:  # a missing file is normal (e.g. first run); not worth an error log
            raise StorageError("File not found.")
        return response.content

    async def log_llm_call(self, row: dict) -> None:
        await self._request("POST", "/rest/v1/llm_calls", json=row, headers={"Prefer": "return=minimal"})

    async def urls(self, paths: list[str]) -> dict[str, str]:
        now = time.time()
        missing = [p for p in paths if self._signed.get(p, ("", 0))[1] < now + 3600]
        if missing:
            response = await self._request(
                "POST",
                f"/storage/v1/object/sign/{self.bucket}",
                json={"expiresIn": SIGNED_URL_SECONDS, "paths": missing},
            )
            for item in response.json():
                if item.get("signedURL"):
                    self._signed[item["path"]] = (f"{self.url}/storage/v1{item['signedURL']}", now + SIGNED_URL_SECONDS)
        return {p: self._signed[p][0] for p in paths if p in self._signed}


def make_store():
    if config.SUPABASE_URL and config.SUPABASE_SECRET_KEY:
        return SupabaseStore(config.SUPABASE_URL, config.SUPABASE_SECRET_KEY, config.SUPABASE_BUCKET)
    return LocalStore(config.DATA_DIR)


store = make_store()
