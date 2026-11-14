import streamlit as st

from common import query
from queries import NARRATIVES
from states import STATES, label

st.title("Narratives")

candidates = st.toggle("Include cluster candidates", help="Narratives the cluster job found, never shipped")
sources = ["note", "cluster"] if candidates else ["note"]
narratives = query(NARRATIVES, {"sources": sources})
if narratives.empty:
    st.info("No narratives yet. A narrative is born when a signed-in user adds a note to a passage.")
    st.stop()

counts = narratives["state"].value_counts()
for column, state in zip(st.columns(len(STATES)), STATES, strict=True):
    column.metric(label(state), int(counts.get(state, 0)))

table = narratives.assign(state=narratives["state"].map(label))
st.dataframe(
    table[
        [
            "id",
            "title",
            "state",
            "source",
            "visible_notes",
            "pending_notes",
            "sites",
            "pages",
            "variants",
            "first_seen",
        ]
    ],
    hide_index=True,
    column_config={
        "id": st.column_config.NumberColumn("#", format="%d"),
        "title": st.column_config.TextColumn("Title", width="large"),
        "state": "State",
        "source": "Source",
        "visible_notes": "Notes",
        "pending_notes": "In review",
        "sites": "Sites",
        "pages": "Pages",
        "variants": "Variants",
        "first_seen": st.column_config.DatetimeColumn("First seen", format="YYYY-MM-DD HH:mm"),
    },
)
st.caption("Open a narrative's notes, spread and lineage on the Narrative detail page.")
