import httpx
import pandas as pd
import streamlit as st

from common import API_URL, me, query
from queries import CONTRIBUTORS

# Reputation comes from GET /me, one call per account, so the page ranks only the most active accounts.
CANDIDATES = 50

st.title("Reputation")
st.caption(
    "Writers gain reputation when their notes become helpful and lose it when they become not helpful."
    " Raters gain it when a rating cast before a note was decided agrees with the outcome."
)

contributors = query(CONTRIBUTORS, {"limit": CANDIDATES})
if contributors.empty:
    st.info("No signed-in account has written or rated a note yet.")
    st.stop()

rows = []
for c in contributors.itertuples():
    try:
        profile = me(c.user_id)
    except httpx.HTTPError as error:
        st.error(f"Could not reach the API at {API_URL}: {error}")
        st.stop()
    statuses = pd.Series([note.get("status") for note in profile.get("notes", [])], dtype="object")
    ratings = profile.get("ratings") or {}
    rows.append(
        {
            "account": f"account-{c.account_id[:8]}",
            "reputation": profile.get("reputation"),
            "notes": c.notes,
            "helpful notes": int((statuses == "helpful").sum()),
            "not helpful notes": int((statuses == "not_helpful").sum()),
            "ratings": c.ratings,
            "agreed": ratings.get("agreed", 0),
        }
    )

board = pd.DataFrame(rows).sort_values(["reputation", "notes", "ratings"], ascending=False)
st.dataframe(
    board,
    hide_index=True,
    column_config={
        "account": "Account",
        "reputation": st.column_config.NumberColumn("Reputation", format="%.2f"),
        "notes": "Notes",
        "helpful notes": "Helpful",
        "not helpful notes": "Not helpful",
        "ratings": "Ratings",
        "agreed": st.column_config.NumberColumn("Agreed", help="Early ratings that matched the outcome"),
    },
)
