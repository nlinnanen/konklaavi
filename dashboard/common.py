import os

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


@st.cache_data(ttl=10)
def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as conn:
        cursor = conn.execute(sql, params)
        return pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])


@st.cache_data(ttl=10)
def me(user_id: str) -> dict:
    """GET /me for an install: its account's email, verification and reputation."""
    response = httpx.get(f"{API_URL}/me", params={"userId": user_id}, timeout=10)
    response.raise_for_status()
    return response.json()


def pick_narrative(narratives: pd.DataFrame) -> int:
    """A narrative chosen in a select box, kept in the page URL as ?id=."""
    ids = narratives["id"].tolist()
    titles = dict(zip(narratives["id"], narratives["title"], strict=True))
    requested = st.query_params.get("id")
    default = ids.index(int(requested)) if requested and requested.isdigit() and int(requested) in ids else 0
    narrative_id = st.selectbox("Narrative", ids, index=default, format_func=lambda i: f"{titles[i]} (#{i})")
    st.query_params["id"] = str(narrative_id)
    return narrative_id
