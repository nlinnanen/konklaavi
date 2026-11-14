import json
import os
from pathlib import Path

import pandas as pd
import streamlit as st

RESULTS = Path(
    os.environ.get("EVAL_RESULTS")
    or Path(__file__).resolve().parent.parent / "backend" / "app" / "eval" / "results" / "latest.json"
)


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


st.title("Evaluation")
if not RESULTS.is_file():
    st.info(f"No results at {RESULTS}. Run `python -m app.eval` in backend/.")
    st.stop()

results = json.loads(RESULTS.read_text())
m = results["metrics"]
pos, neg = results["positives"], results["negatives"]
st.caption(
    f"Generated {results['generated_at']}. Positives: {pos['pages']} fetched pages of {pos['urls']} article URLs"
    f" from {pos['cases']} held-out EUvsDisinfo cases ({pos['seed_overlap']} URLs shared with seed cases left"
    f" out). Negatives: {neg['pages']} fetched pages of {neg['urls']} reliable-outlet URLs."
)

cols = st.columns(3)
cols[0].metric("Precision", percent(m["precision"]))
cols[1].metric("Recall", percent(m["recall"]))
cols[2].metric("False-positive rate", percent(m["false_positive_rate"]))

st.table(
    pd.DataFrame(
        {
            "flagged": [m["true_positives"], m["false_positives"]],
            "not flagged": [m["false_negatives"], m["true_negatives"]],
        },
        index=["positive pages", "negative pages"],
    )
)


def show_pages(pages: list[dict]) -> None:
    for page in pages:
        with st.expander(page["url"]):
            st.dataframe(
                pd.DataFrame(page["matches"]).fillna({"narrative": "votes"}),
                hide_index=True,
                column_config={"text": st.column_config.TextColumn("Matched text", width="large")},
            )


st.subheader(f"False positives ({len(results['false_positives'])})")
show_pages(results["false_positives"])
st.subheader(f"True positives ({len(results['true_positives'])})")
show_pages(results["true_positives"])
if results["unreachable"]:
    with st.expander(f"Unreachable pages ({len(results['unreachable'])})"):
        st.write("\n".join(f"- {url}" for url in results["unreachable"]))
