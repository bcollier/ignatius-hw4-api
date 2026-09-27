"""Ignatius at Home: turn an uploaded PDF, Word or text document into a guided audio
retreat. This file sets up the app (CORS, error format, storage at start-up) and puts
the routes together; each area's routes live in app/routes/."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import config
from .routes import (about_me, agents, build_log, client_errors, conversation, handoff, cost_report, days, example_documents, local_files, meta,
                     my_examen, practice_journal, retreats)
from .storage import LocalStore, StorageError, store

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await store.setup()
    yield


app = FastAPI(title="Ignatius at Home API", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


# Every error comes back as {"error": {"status": ..., "message": ...}}.
def _error(status: int, message) -> JSONResponse:
    return JSONResponse({"error": {"status": status, "message": message}}, status_code=status)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, exc.detail)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    first = exc.errors()[0]
    field = ".".join(str(p) for p in first["loc"][1:]) or "request"
    return _error(422, f"Invalid {field}: {first['msg']}")


@app.exception_handler(StorageError)
async def storage_error(_: Request, exc: StorageError):
    return _error(503, str(exc))


for area in (meta, client_errors, handoff, conversation, about_me, agents, retreats, example_documents, build_log, cost_report, days, practice_journal, my_examen):
    app.include_router(area.router)
if isinstance(store, LocalStore):
    app.include_router(local_files.router)
