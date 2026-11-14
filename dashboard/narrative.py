import json

import pandas as pd
import streamlit as st

from common import pick_narrative, query
from notes_view import show_note
from queries import (
    NARRATIVE_LINEAGE,
    NARRATIVE_NOTES,
    NARRATIVE_PAGES,
    NARRATIVES,
    SPREAD_HITS_PER_DAY,
    SPREAD_PER_DAY,
)
from states import label

LINEAGE_EDGES = 300


def lineage_dot(edges: pd.DataFrame) -> str:
    """A DOT digraph from each parent fingerprint to the fingerprints derived from it."""
    lines = ["digraph {", "rankdir=LR;", "node [shape=box, fontsize=10];"]
    nodes = dict(zip(edges["parent"], edges["parent_label"], strict=True))
    nodes.update(zip(edges["child"], edges["child_label"], strict=True))
    lines += [f"{node} [label={json.dumps(label, ensure_ascii=False)}];" for node, label in nodes.items()]
    lines += [f"{parent} -> {child};" for parent, child in zip(edges["parent"], edges["child"], strict=True)]
    return "\n".join(lines + ["}"])


narratives = query(NARRATIVES, {"sources": ["note", "cluster"]})
if narratives.empty:
    st.info("No narratives yet.")
    st.stop()

narrative_id = pick_narrative(narratives)
params = {"id": narrative_id}
n = narratives.set_index("id").loc[narrative_id]

st.title(n["title"])
st.caption(f"{label(n['state'])} · {n['source']} · created {n['created_at']:%Y-%m-%d}")
if n["source_url"]:
    st.markdown(f"[Page the narrative was first noted on]({n['source_url']})")
if n["claim"] and n["claim"] != n["title"]:
    st.write(n["claim"])

cols = st.columns(4)
cols[0].metric("Sites", int(n["sites"]))
cols[1].metric("Pages", int(n["pages"]))
cols[2].metric("Variants", int(n["variants"]))
cols[3].metric("First seen", f"{n['first_seen']:%Y-%m-%d}" if pd.notna(n["first_seen"]) else "-")

st.subheader("Notes")
notes = query(NARRATIVE_NOTES, params)
if notes.empty:
    st.caption("No notes.")
for note in notes.itertuples():
    show_note(note)

st.subheader("Spread")
per_day = query(SPREAD_PER_DAY, params)
if per_day.empty:
    st.caption("Not seen on any page yet.")
else:
    left, right = st.columns(2)
    left.caption("Sites reached")
    left.line_chart(per_day, x="day", y="domains_so_far")
    right.caption("Observations per day: fetch is pages the pipeline fetched, detection is extension matches")
    right.bar_chart(query(SPREAD_HITS_PER_DAY, params), x="day", y="hits", color="source")

pages = query(NARRATIVE_PAGES, params)
with st.expander(f"Pages ({len(pages)})"):
    st.dataframe(
        pages,
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("URL", width="large"),
            "domain": "Site",
            "title": "Title",
            "observations": "Observations",
            "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD HH:mm"),
            "last_seen": st.column_config.DatetimeColumn("Last seen", format="YYYY-MM-DD HH:mm"),
        },
    )

st.subheader("Lineage")
lineage = query(NARRATIVE_LINEAGE, {**params, "limit": LINEAGE_EDGES})
if lineage.empty:
    st.caption("No DERIVED_FROM edges: no block of this narrative was found copied from an earlier one.")
else:
    st.caption("Each arrow points from a block to a near-duplicate seen later.")
    st.graphviz_chart(lineage_dot(lineage))
