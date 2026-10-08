"""FastAPI application."""

import logging

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import routes_core, routes_work
from app.api.deps import csrf_guard
from app.config import get_settings
from app.core.runtime import record_error
from app.db.session import scoped_session
from app.tools.base import load_tools

log = logging.getLogger("agency.api")

settings = get_settings()
app = FastAPI(title="Agency OS", version="0.1.0", dependencies=[Depends(csrf_guard)],
              docs_url=None if settings.is_production else "/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["content-type", "x-requested-with"],
)
app.include_router(routes_core.router)
app.include_router(routes_work.router)
load_tools()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled API error")
    try:
        with scoped_session(owner=True) as s:
            record_error(s, "api", f"{type(exc).__name__}: {exc}", detail={"path": request.url.path})
    except Exception:
        log.exception("could not record API error")
    return JSONResponse({"detail": "Internal error - it has been logged on the Errors page."}, status_code=500)
