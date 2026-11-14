import json

import pandas as pd
import streamlit as st

from common import query
from queries import (
    NARRATIVE,
    NARRATIVE_LINEAGE,
    NARRATIVE_OBSERVATIONS_PER_DAY,
    NARRATIVE_PAGES,
    NARRATIVES,
)

LINEAGE_EDGES = 300


def lineage_dot(edges: pd.DataFrame) -> str:
    """A DOT digraph from each parent fingerprint to the fingerprints derived from it."""
    lines = ["digraph {", "rankdir=LR;", "node [shape=box, fontsize=10];"]
    nodes = dict(zip(edges["parent"], edges["parent_label"], strict=True))
    nodes.update(zip(edges["child"], edges["child_label"], strict=True))
    lines += [f"{node} [label={json.dumps(label, ensure_ascii=False)}];" for node, label in nodes.items()]
    lines += [f"{parent} -> {child};" for parent, child in zip(edges["parent"], edges["child"], strict=True)]
    return "\n".join(lines + ["}"])


narratives = query(NARRATIVES)
if narratives.empty:
    st.info("No narratives yet.")
    st.stop()

ids = narratives["id"].tolist()
titles = dict(zip(narratives["id"], narratives["title"], strict=True))
requested = st.query_params.get("id")
default = ids.index(int(requested)) if requested and requested.isdigit() and int(requested) in ids else 0
narrative_id = st.selectbox("Narrative", ids, index=default, format_func=lambda i: f"{titles[i]} (#{i})")
st.query_params["id"] = str(narrative_id)
params = {"id": narrative_id}

n = query(NARRATIVE, params).iloc[0]
st.title(n["title"])
st.caption(f"{n['source']} · {n['state']} · {n['verdict']} · created {n['created_at']:%Y-%m-%d}")
if n["source_url"]:
    st.markdown(f"[Source case]({n['source_url']})")
if n["claim"]:
    st.subheader("Claim")
    st.write(n["claim"])
if n["explanation"]:
    st.subheader("Explanation")
    st.write(n["explanation"])
if n["techniques"]:
    st.subheader("Techniques")
    st.write(", ".join(n["techniques"]))

pages = query(NARRATIVE_PAGES, params)
st.subheader(f"Pages ({len(pages)} on {pages['domain'].nunique() if len(pages) else 0} domains)")
st.dataframe(
    pages,
    hide_index=True,
    column_config={
        "url": st.column_config.LinkColumn("URL", width="large"),
        "domain": "Domain",
        "title": "Title",
        "observations": "Observations",
        "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD HH:mm"),
        "last_seen": st.column_config.DatetimeColumn("Last seen", format="YYYY-MM-DD HH:mm"),
    },
)

st.subheader("Observations per day")
per_day = query(NARRATIVE_OBSERVATIONS_PER_DAY, params)
if per_day.empty:
    st.caption("No observations.")
else:
    st.line_chart(per_day, x="day", y="observations")

st.subheader("Lineage")
lineage = query(NARRATIVE_LINEAGE, {**params, "limit": LINEAGE_EDGES})
if lineage.empty:
    st.caption("No DERIVED_FROM edges.")
else:
    st.graphviz_chart(lineage_dot(lineage))
