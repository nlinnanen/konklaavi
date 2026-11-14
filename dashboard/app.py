import streamlit as st

st.set_page_config(page_title="Narratives", layout="wide")
st.navigation(
    [
        st.Page("narratives.py", title="Narratives", default=True),
        st.Page("narrative.py", title="Narrative detail"),
        st.Page("spread.py", title="Spread"),
        st.Page("check.py", title="Check text"),
        st.Page("evaluation.py", title="Evaluation"),
    ]
).run()
