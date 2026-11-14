import os

import pandas as pd
import psycopg
import streamlit as st

from queries import NARRATIVES

DATABASE_URL = os.environ.get("DATABASE_URL") or (
    f"postgresql://postgres:postgres@localhost:{os.environ.get('DB_PORT') or '5432'}/postgres"
)


@st.cache_data(ttl=10)
def query(sql: str) -> pd.DataFrame:
    with psycopg.connect(DATABASE_URL) as conn:
        cursor = conn.execute(sql)
        return pd.DataFrame(cursor.fetchall(), columns=[c.name for c in cursor.description])


st.set_page_config(page_title="Narratives", layout="wide")
st.title("Narratives")

narratives = query(NARRATIVES)
st.caption(f"{len(narratives)} narratives")
st.dataframe(
    narratives.drop(columns="id"),
    hide_index=True,
    column_config={
        "title": st.column_config.TextColumn("Title", width="large"),
        "source": "Source",
        "state": "State",
        "variants": "Variants",
        "pages": "Pages",
        "domains": "Domains",
        "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD HH:mm"),
    },
)
