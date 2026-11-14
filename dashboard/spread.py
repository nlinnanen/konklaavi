import streamlit as st

from common import query
from queries import NARRATIVES, SPREAD_DOMAINS, SPREAD_HITS_PER_DAY, SPREAD_PER_DAY

st.title("Spread")

narratives = query(NARRATIVES)
narratives = narratives[narratives["pages"] > 0]
if narratives.empty:
    st.info("No narrative has been seen on a page yet.")
    st.stop()

ids = narratives["id"].tolist()
titles = dict(zip(narratives["id"], narratives["title"], strict=True))
requested = st.query_params.get("id")
default = ids.index(int(requested)) if requested and requested.isdigit() and int(requested) in ids else 0
domains = dict(zip(narratives["id"], narratives["domains"], strict=True))
narrative_id = st.selectbox(
    "Narrative", ids, index=default, format_func=lambda i: f"{titles[i]} (#{i}, {domains[i]} domains)"
)
st.query_params["id"] = str(narrative_id)
params = {"id": narrative_id}

per_day = query(SPREAD_PER_DAY, params)
if per_day.empty:
    st.caption("No observations.")
    st.stop()

cols = st.columns(3)
cols[0].metric("Domains", int(per_day["domains_so_far"].iloc[-1]))
cols[1].metric("Days seen", len(per_day))
cols[2].metric("Observations", int(per_day["observations"].sum()))

st.subheader("Domains reached")
st.line_chart(per_day, x="day", y="domains_so_far")
st.subheader("Domains per day")
st.bar_chart(per_day, x="day", y="domains")
st.subheader("Observations per day")
st.caption("fetch: pages the pipeline fetched; detection: matches reported by extensions.")
st.bar_chart(query(SPREAD_HITS_PER_DAY, params), x="day", y="hits", color="source")

st.subheader("Domains")
st.dataframe(
    query(SPREAD_DOMAINS, params),
    hide_index=True,
    column_config={
        "domain": "Domain",
        "first_day": st.column_config.DateColumn("First seen"),
        "last_day": st.column_config.DateColumn("Last seen"),
        "observations": "Observations",
    },
)
