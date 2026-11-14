import os
from pathlib import Path

import httpx
import pandas as pd
import psycopg
import streamlit as st

DATABASE_URL = os.environ.get("DATABASE_URL") or (
    f"postgresql://postgres:postgres@localhost:{os.environ.get('DB_PORT') or '5432'}/postgres"
)
API_URL = (os.environ.get("API_URL") or f"http://localhost:{os.environ.get('API_PORT') or '8000'}").rstrip(
    "/"
)

RESULTS = Path(
    os.environ.get("EVAL_RESULTS")
    or Path(__file__).resolve().parent.parent / "backend" / "app" / "eval" / "results" / "latest.json"
)
# The simulations in simulation/ write their runs next to the evaluation results.
SIMULATION = RESULTS.parent / "simulation.json"


@st.cache_data(ttl=10)
def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as conn:
        cursor = conn.execute(sql, params)
        return pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])


@st.cache_data(ttl=10)
def me(user_id: str) -> dict:
    """GET /me for an install: its voter's reputation, which is the weight of its votes."""
    response = httpx.get(f"{API_URL}/me", params={"userId": user_id}, timeout=10)
    response.raise_for_status()
    return response.json()
