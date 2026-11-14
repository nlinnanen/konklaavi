import streamlit as st

from common import query
from notes_view import CLASSIFICATION, STATUS
from queries import RECENT_NOTES

NOTES = 500

st.title("Notes")

notes = query(RECENT_NOTES, {"limit": NOTES})
if notes.empty:
    st.info("No notes yet.")
    st.stop()

counts = notes["status"].value_counts()
for column, (status, title) in zip(st.columns(len(STATUS)), STATUS.items(), strict=True):
    column.metric(title, int(counts.get(status, 0)))

st.subheader("Bridging space")
st.caption(
    "Each scored note by its intercept (helpfulness shared across viewpoints) and factor (how far its ratings"
    " split along the raters' viewpoint axis). A note becomes helpful at intercept 0.40 or more with |factor|"
    " under 0.50, not helpful at intercept at most -0.05 - 0.8 |factor|, and controversial at |factor| 0.50"
    " or more otherwise. The scoring job refits every 5 minutes."
)
scored = notes.dropna(subset=["intercept", "factor"])
if scored.empty:
    st.caption("No note has been scored yet.")
else:
    st.scatter_chart(
        scored.assign(status=scored["status"].map(STATUS)),
        x="factor",
        y="intercept",
        color="status",
        height=420,
    )

st.subheader(f"Latest {len(notes)} notes")
table = notes.assign(
    status=notes["status"].map(STATUS), classification=notes["classification"].map(CLASSIFICATION)
)
st.dataframe(
    table[
        [
            "id",
            "narrative",
            "classification",
            "status",
            "moderation",
            "counted",
            "ratings",
            "helpful",
            "somewhat",
            "not_helpful",
            "intercept",
            "factor",
            "body",
            "created_at",
        ]
    ],
    hide_index=True,
    column_config={
        "id": st.column_config.NumberColumn("#", format="%d"),
        "narrative": st.column_config.TextColumn("Narrative", width="medium"),
        "classification": "Says",
        "status": "Status",
        "moderation": "Moderation",
        "counted": st.column_config.NumberColumn(
            "Counted", help="Raters scoring counts: each account once, and each install without one"
        ),
        "ratings": "All ratings",
        "helpful": "Helpful",
        "somewhat": "Somewhat",
        "not_helpful": "Not helpful",
        "intercept": st.column_config.NumberColumn("Intercept", format="%.2f"),
        "factor": st.column_config.NumberColumn("Factor", format="%.2f"),
        "body": st.column_config.TextColumn("Note", width="large"),
        "created_at": st.column_config.DatetimeColumn("Written", format="YYYY-MM-DD HH:mm"),
    },
)
