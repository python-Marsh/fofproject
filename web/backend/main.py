"""FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web.backend.state import reload_funds
from web.backend.routers import system, funds, charts, tables, mvo, overwrite


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
