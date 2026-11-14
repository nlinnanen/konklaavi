import httpx
import pandas as pd
import streamlit as st

from common import API_URL

st.title("Check text")
st.caption(f"Fingerprints the text or page through the API at {API_URL} and lists the sentences it knows.")

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
    matches = response.json()["matches"]
    flagged = sum(m["result"]["flagged"] for m in matches)
    st.subheader(f"{flagged} flagged of {len(matches)} known sentences")
    if matches:
        rows = []
        for match in matches:
            result = match["result"]
            signature = result.get("signature") or {}
            rows.append(
                {
                    "sentence": match["text"],
                    "flagged": result["flagged"],
                    "verdict": result["topVerdict"],
                    "votes": result["total"],
                    "narrative": signature.get("title"),
                    "match": signature.get("matchLevel"),
                    "source": signature.get("sourceUrl"),
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            hide_index=True,
            column_config={
                "sentence": st.column_config.TextColumn("Sentence", width="large"),
                "source": st.column_config.LinkColumn("Source"),
            },
        )
