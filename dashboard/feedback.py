import pandas as pd
import streamlit as st

from common import me, query
from queries import DISPUTES, FEEDBACK_VOTES

DISPUTE_ROWS = 50
# Match levels of the likely and related tiers, the ones that can be wrong about meaning.
FUZZY_LEVELS = ("narrative", "related")


def per_level(votes: pd.DataFrame) -> pd.DataFrame:
    """Voters and their weighted share per narrative and match level."""
    votes = votes.assign(
        weight=votes["user_id"].map(lambda user: me(user)["weight"]),
        trustworthy=votes["verdict"] == "trustworthy",
    )
    votes["trustworthy_weight"] = votes["weight"].where(votes["trustworthy"], 0.0)
    grouped = votes.groupby(["narrative_id", "title", "source", "state", "match_level"], as_index=False).agg(
        misinformation=("trustworthy", lambda t: int((~t).sum())),
        not_misinformation=("trustworthy", "sum"),
        weight=("weight", "sum"),
        trustworthy_weight=("trustworthy_weight", "sum"),
    )
    grouped["not_misinformation_share"] = grouped["trustworthy_weight"] / grouped["weight"]
    return grouped.drop(columns=["weight", "trustworthy_weight"]).sort_values(
        ["not_misinformation_share", "not_misinformation"], ascending=False
    )


COLUMNS = {
    "narrative_id": None,
    "title": st.column_config.TextColumn("Narrative", width="large"),
    "match_level": "Match level",
    "misinformation": "Misinformation",
    "not_misinformation": "Not misinformation",
    "not_misinformation_share": st.column_config.ProgressColumn(
        "Not misinformation, weighted", format="percent", min_value=0, max_value=1
    ),
}

st.title("Feedback")
st.caption(
    "Votes from the extension's Misinformation and Not misinformation buttons, the latest per voter, narrative"
    " and match level. Each vote weighs its voter's reputation."
)

votes = query(FEEDBACK_VOTES)
if votes.empty:
    st.info("No feedback votes yet.")
else:
    levels = per_level(votes)
    st.header("False-positive candidates")
    candidates = levels[levels["match_level"].isin(FUZZY_LEVELS) & (levels["not_misinformation_share"] > 0.5)]
    st.caption("Narratives whose likely or related matches get mostly Not misinformation by weight.")
    if candidates.empty:
        st.caption("None.")
    else:
        st.dataframe(candidates, hide_index=True, column_config=COLUMNS)
    st.header("Per narrative and match level")
    st.dataframe(levels, hide_index=True, column_config=COLUMNS)

st.header("Recent disputes")
disputes = query(DISPUTES, {"limit": DISPUTE_ROWS})
if disputes.empty:
    st.caption("No narrative is disputed.")
else:
    st.dataframe(
        disputes.drop(columns="id"),
        hide_index=True,
        column_config={
            "title": st.column_config.TextColumn("Narrative", width="large"),
            "source": "Source",
            "updated_at": st.column_config.DatetimeColumn("Disputed", format="YYYY-MM-DD HH:mm"),
            "not_misinformation": "Not misinformation",
            "misinformation": "Misinformation",
        },
    )
