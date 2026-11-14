from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import account, core
from app.db import close_pool, open_pool
from app.migrate import migrate


@asynccontextmanager
async def lifespan(_: FastAPI):
    migrate()
    open_pool()
    yield
    close_pool()


app = FastAPI(title="HashTrust", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(core.router)
app.include_router(account.router)


@app.get("/health")
def health() -> dict:
    return {"ok": True}
