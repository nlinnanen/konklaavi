import os

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
