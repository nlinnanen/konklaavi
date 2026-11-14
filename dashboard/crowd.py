import json

import httpx
import pandas as pd
import streamlit as st

from common import API_URL, SIMULATION, query
from queries import REPORTERS, SETTLEMENTS

# Reputation comes from GET /me, one call per reporter, so the page ranks only the most active ones.
CANDIDATES = 25
SETTLEMENT_ROWS = 50


@st.cache_data(ttl=10)
def me(user_id: str) -> dict:
    response = httpx.get(f"{API_URL}/me", params={"userId": user_id}, timeout=10)
    response.raise_for_status()
    return response.json()


def name(profile: dict) -> str:
    """The email's local part for an account, a short install id otherwise."""
    email = profile["email"]
    return email.split("@")[0] if email else f"anon-{profile['userId'][:8]}"


def show_reporters() -> None:
    st.header("Top reporters")
    candidates = query(REPORTERS, {"limit": CANDIDATES})
    if candidates.empty:
        st.caption("No reports yet.")
        return
    rows = []
    for user_id, reports in zip(candidates["user_id"], candidates["reports"], strict=True):
        profile = me(user_id)
        outcomes = [report["outcome"] for report in profile["reports"]]
        rows.append(
            {
                "reporter": name(profile),
                "reputation": profile["reputation"],
                "weight": profile["weight"],
                "reports": reports,
                "confirmed": outcomes.count("confirmed"),
                "rejected": outcomes.count("rejected"),
                "agreed": profile["agreed"],
                "disagreed": profile["disagreed"],
            }
        )
    st.caption(
        f"The {CANDIDATES} most active reporters, ranked by reputation. Agreements and disagreements are"
        " votes that match or contradict settled content."
    )
    st.dataframe(
        pd.DataFrame(rows).sort_values(["reputation", "reports"], ascending=False),
        hide_index=True,
        column_config={
            "reputation": st.column_config.NumberColumn(format="%.2f"),
            "weight": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def show_settlements() -> None:
    st.header("Recent settlements")
    settlements = query(SETTLEMENTS, {"limit": SETTLEMENT_ROWS})
    if settlements.empty:
        st.caption("No report has settled yet.")
        return
    settlements["outcome"] = settlements["confirmed"].map({True: "confirmed", False: "rejected"})
    settlements["hours to settle"] = settlements["time_to_settle"].dt.total_seconds() / 3600
    confirmed = settlements[settlements["confirmed"]]
    if not confirmed.empty:
        st.metric("Median time to confirm", f"{confirmed['hours to settle'].median():.1f} h")
    st.dataframe(
        settlements[
            [
                "report",
                "outcome",
                "settled_by",
                "source",
                "source_url",
                "reported_at",
                "settled_at",
                "hours to settle",
            ]
        ],
        hide_index=True,
        column_config={
            "report": "Report",
            "settled_by": "Confirmed by",
            "source_url": st.column_config.LinkColumn("Source"),
            "reported_at": st.column_config.DatetimeColumn("Reported"),
            "settled_at": st.column_config.DatetimeColumn("Settled"),
            "hours to settle": st.column_config.NumberColumn(format="%.1f"),
        },
    )


def show_simulation(run: dict) -> None:
    st.header("Simulation")
    sizes = ", ".join(f"{n} {group}" for group, n in run["groups"].items())
    st.write(
        f"Simulated users vote through the API ({sizes}). Each round, early reporters report a disinformation"
        f" case that settles {run['delay']} round(s) later, late followers vote on a case that has already"
        " settled, and the bot farm flags an article of a reliable outlet."
    )
    st.caption(f"Generated {run['generated_at']} against {run.get('api')}.")
    after = run["bots_below_honest_after"]
    st.metric(
        "Reports until the bots weigh less than honest users",
        "never" if after is None else after,
    )
    rows = [
        {"round": row["round"], "group": group, **values}
        for row in run["rounds"]
        for group, values in row["groups"].items()
    ]
    frame = pd.DataFrame(rows)
    st.subheader("Weight per group")
    st.line_chart(frame, x="round", y="weight", color="group")
    st.table(frame.pivot(index="round", columns="group", values="reputation").round(3))


st.title("Crowd")
show_reporters()
show_settlements()
crowd = json.loads(SIMULATION.read_text()).get("crowd") if SIMULATION.is_file() else None
if crowd:
    show_simulation(crowd)
else:
    st.caption(f"No crowd simulation in {SIMULATION}. Run `uv run python crowd.py` in simulation/.")
