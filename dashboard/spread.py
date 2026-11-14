import streamlit as st

from common import query
from queries import HAS_OBSERVATION_DAILY, NARRATIVES, spread_domains, spread_per_day

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

aggregate = bool(query(HAS_OBSERVATION_DAILY).iloc[0, 0])
st.caption(
    "Daily counts from the observation_daily continuous aggregate."
    if aggregate
    else "Daily counts from the observation table; the observation_daily continuous aggregate is absent."
)

per_day = query(spread_per_day(aggregate), params)
if per_day.empty:
    st.caption("No observations.")
    st.stop()

cols = st.columns(3)
cols[0].metric("Domains", int(per_day["domains_so_far"].iloc[-1]))
cols[1].metric("Days seen", len(per_day))
cols[2].metric("Observations", int(per_day["observations"].sum()))

st.subheader("Domains reached")
st.line_chart(per_day, x="day", y="domains_so_far")
st.subheader("Pages and domains per day")
st.bar_chart(per_day, x="day", y=["pages", "domains"], stack=False)

st.subheader("Domains")
st.dataframe(
    query(spread_domains(aggregate), params),
    hide_index=True,
    column_config={
        "domain": "Domain",
        "first_day": st.column_config.DateColumn("First seen"),
        "last_day": st.column_config.DateColumn("Last seen"),
        "pages": "Pages",
        "observations": "Observations",
    },
)
