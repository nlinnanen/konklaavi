import streamlit as st

st.set_page_config(page_title="HashTrust", layout="wide")
st.navigation(
    [
        st.Page("narratives.py", title="Narratives", default=True),
        st.Page("narrative.py", title="Narrative detail"),
        st.Page("notes.py", title="Notes"),
        st.Page("moderation.py", title="Moderation"),
        st.Page("reputation.py", title="Reputation"),
        st.Page("check.py", title="Check text"),
    ]
).run()
