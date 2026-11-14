import json

import pandas as pd
import streamlit as st

from common import RESULTS, SIMULATION


def percent(value: float | None) -> str:
    return "n/a" if value is None else f"{100 * value:.1f}%"


def show_metrics(m: dict) -> None:
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


def show_matches(pages: list[dict]) -> None:
    for page in pages:
        with st.expander(page["url"]):
            if "flagged_by_own_narrative" in page:
                st.caption(
                    "Flagged by its own case's narrative"
                    if page["flagged_by_own_narrative"]
                    else "Flagged only by other narratives"
                )
            st.dataframe(
                pd.DataFrame(page["matches"]).fillna({"narrative": "votes"}),
                hide_index=True,
                column_config={"text": st.column_config.TextColumn("Matched text", width="large")},
            )


def show_misses(pages: list[dict]) -> None:
    st.dataframe(
        pd.DataFrame(pages, columns=["url", "case_id", "reason"]),
        hide_index=True,
        column_config={
            "url": st.column_config.LinkColumn("URL"),
            "reason": st.column_config.TextColumn(width="large"),
        },
    )


SWEEP_COLUMNS = {
    "false_positive_rate": "False-positive rate",
    "known_precision": "Known precision",
    "known_recall": "Known recall",
    "unseen_precision": "Unseen precision",
    "unseen_recall": "Unseen recall",
}


def show_sweep(section: dict) -> None:
    st.header("Narrative-level matching")
    limit = percent(section["max_false_positive_rate"])
    best = section["best_threshold"]
    st.write(
        f"A block of {section['min_words']} or more words without an exact or variant match is flagged when its"
        f" cosine to one of an active narrative's vectors reaches the threshold ({section['vectors']} vectors of"
        f" {section['narratives']} narratives). Precision and recall count exact and variant matches too."
    )
    sweep = (
        pd.DataFrame(section["sweep"])
        .set_index("threshold")[list(SWEEP_COLUMNS)]
        .rename(columns=SWEEP_COLUMNS)
    )
    st.line_chart(sweep, x_label="Cosine threshold", y_label="Rate")
    st.table(sweep.map(lambda v: percent(None if pd.isna(v) else v)))
    if best is None:
        st.warning(f"No threshold keeps the false-positive rate at or below {limit}.")
        return
    st.metric(f"Best known recall at a false-positive rate of at most {limit}", f"threshold {best}")
    st.subheader(f"Narrative-level false positives at {best} ({len(section['false_positives'])})")
    show_matches(section["false_positives"])
    with st.expander(
        f"Positives flagged only at the narrative level at {best} ({len(section['true_positives'])})"
    ):
        show_matches(section["true_positives"])


def show_evaluation(results: dict) -> None:
    known, unseen, neg = results["known"], results["unseen"], results["negatives"]
    st.caption(
        f"Generated {results['generated_at']}. Negatives: {neg['pages']} fetched pages of {neg['urls']}"
        " reliable-outlet URLs, shared by both sets."
    )

    st.header("Known narratives")
    st.write(
        f"Each seed case with two or more article URLs seeded its narrative from some and held the rest out."
        f" Positives: {known['pages']} fetched pages of {known['urls']} held-out URLs and {known['mirrors']}"
        f" mirrors on other domains, from {known['cases']} cases ({known['seed_overlap']} held-out URLs that"
        f" another case seeded are left out). {known['flagged_by_own_narrative']} pages were flagged by their"
        " own case's narrative."
    )
    show_metrics(known["metrics"])

    st.header("Unseen narratives")
    st.write(
        f"Test cases were held out whole, so nothing was seeded for them and low recall is expected: a page"
        f" is flagged only when it repeats another narrative. Positives: {unseen['pages']} fetched pages of"
        f" {unseen['urls']} URLs from {unseen['cases']} cases ({unseen['seed_overlap']} URLs that a seed case"
        " shares are left out)."
    )
    show_metrics(unseen["metrics"])

    if "narrative" in results:
        show_sweep(results["narrative"])

    st.header("Pages")
    st.subheader(f"False positives ({len(neg['false_positives'])})")
    show_matches(neg["false_positives"])
    st.subheader(f"False negatives, known narratives ({len(known['false_negatives'])})")
    show_misses(known["false_negatives"])
    with st.expander(f"True positives, known narratives ({len(known['true_positives'])})"):
        show_matches(known["true_positives"])
    with st.expander(f"False negatives, unseen narratives ({len(unseen['false_negatives'])})"):
        show_misses(unseen["false_negatives"])
    with st.expander(f"True positives, unseen narratives ({len(unseen['true_positives'])})"):
        show_matches(unseen["true_positives"])
    if results["unreachable"]:
        with st.expander(f"Unreachable pages ({len(results['unreachable'])})"):
            st.dataframe(pd.DataFrame(results["unreachable"]), hide_index=True)


def needed(n: int | None, cap: int) -> str:
    return f"more than {cap}" if n is None else str(n)


def show_simulation(runs: dict) -> None:
    st.header("Simulation")
    st.write(
        "Simulated users vote through the API: honest users vote correctly, lazy users at random, partisans"
        " flag reliable content. A brigade of accounts votes in a burst, either fresh accounts or established"
        " ones with honest earlier votes. Each run is named after the API build it ran against."
    )
    for label, run in runs.items():
        st.subheader(f"Run {label}")
        st.caption(f"Generated {run['generated_at']} against {run.get('api')}, seed {run['seed']}.")
        st.table(
            pd.DataFrame(
                [
                    {
                        "users": ", ".join(f"{n} {kind}" for kind, n in row["users"].items()),
                        "votes": row["votes"],
                        "reliable flagged": percent(row["reliable_flagged"]),
                        "false flagged": percent(row["false_flagged"]),
                    }
                    for row in run["population"]
                ]
            )
        )
        flag = pd.DataFrame(run["brigade_flag"])
        flag["needed"] = [needed(n, run["cap"]) for n in flag["needed"]]
        st.write("Brigade accounts needed to flag a reliable page, after its trustworthy votes:")
        st.table(flag.pivot(index="honest_votes", columns="accounts", values="needed"))
        st.write("Brigade accounts voting trustworthy needed to retire an active narrative:")
        if run["brigade_retire"]:
            st.table(
                pd.DataFrame(
                    [
                        {
                            "target": row["narrative_id"] or row["sentence_hash"][:12],
                            "accounts": row["accounts"],
                            "needed": needed(row["needed"], run["cap"]),
                        }
                        for row in run["brigade_retire"]
                    ]
                )
            )
        else:
            st.caption("Not run: the API offered no active narrative sentence.")


st.title("Evaluation")
if RESULTS.is_file():
    show_evaluation(json.loads(RESULTS.read_text()))
else:
    st.info(f"No results at {RESULTS}. Run `python -m app.eval` in backend/.")
if SIMULATION.is_file() and (runs := json.loads(SIMULATION.read_text()).get("runs")):
    show_simulation(runs)
else:
    st.info(f"No simulation results at {SIMULATION}. Run `uv run python sim.py` in simulation/.")
