import httpx
import pandas as pd
import streamlit as st

from common import API_URL
from states import label, legend, mark

st.title("Check text")
st.caption(
    f"Fingerprints the text or page through the API at {API_URL} and lists the passages it recognises."
)

with st.form("check"):
    text = st.text_area("Text", height=200, help="Paragraphs separated by blank lines")
    url = st.text_input("or a page URL")
    submitted = st.form_submit_button("Check")

if submitted:
    body = {"text": text} if text.strip() else {"url": url.strip()} if url.strip() else None
    if body is None:
        st.warning("Enter text or a URL.")
        st.stop()
    try:
        response = httpx.post(f"{API_URL}/check", json=body, timeout=120)
    except httpx.HTTPError as error:
        st.error(f"Could not reach the API: {error}")
        st.stop()
    if response.status_code != 200:
        st.error(f"The API answered {response.status_code}: {response.text}")
        st.stop()
    rows = []
    for match in response.json()["matches"]:
        narrative = match["result"].get("narrative")
        if not narrative:
            continue
        notes = narrative.get("notes") or []
        spread = narrative.get("spread") or {}
        rows.append(
            {
                "state": narrative.get("state"),
                "passage": match["text"],
                "match": narrative.get("matchLevel"),
                "similarity": narrative.get("similarity"),
                "narrative": narrative.get("title"),
                "sites": spread.get("sites"),
                "first seen": spread.get("firstSeen"),
                "note": notes[0].get("body") if notes else None,
            }
        )
    st.subheader(f"{len(rows)} recognised passages")
    if rows:
        legend()
        st.markdown(
            "<br>".join(mark(row["passage"], row["state"], row["match"]) for row in rows),
            unsafe_allow_html=True,
        )
        frame = pd.DataFrame(rows)
        frame["state"] = frame["state"].map(label)
        st.dataframe(
            frame,
            hide_index=True,
            column_config={
                "state": "State",
                "passage": st.column_config.TextColumn("Passage", width="large"),
                "match": "Match",
                "similarity": st.column_config.NumberColumn(format="%.3f"),
                "narrative": "Narrative",
                "sites": "Sites",
                "first seen": "First seen",
                "note": st.column_config.TextColumn("Top note", width="large"),
            },
        )
