import streamlit as st

from common import query
from queries import NARRATIVES

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
