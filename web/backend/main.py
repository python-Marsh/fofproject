"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from web.backend.state import reload_funds
from web.backend.routers import system, funds, charts, tables, mvo, overwrite, birthday

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load all fund data on startup
    reload_funds()
    yield


app = FastAPI(
    title="FOF Fund Workstation",
    description="Internal fund analysis API",
    lifespan=lifespan,
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all handler so unhandled exceptions return a meaningful message
    instead of a generic 'Internal Server Error'."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Unexpected error: {exc}"},
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(funds.router)
app.include_router(charts.router)
app.include_router(tables.router)
app.include_router(mvo.router)
app.include_router(overwrite.router)
app.include_router(birthday.router)
