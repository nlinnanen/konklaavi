"""Match tiers, decided from a signature as the extension decides them, with its colours and labels."""

import html

import streamlit as st

# Strongest first, the order the legend lists them in. The styles are TIER_STYLES in client/lib/tier.ts.
TIERS = {
    "confirmed": ("Confirmed", "background-color:#dc2626;color:#fff;", "Known misinformation, settled"),
    "likely": ("Likely", "background-color:rgba(249,115,22,0.3);", "Close in meaning to an active narrative"),
    "reported": ("Reported", "background-color:rgba(168,85,247,0.15);", "Flagged by users, not confirmed"),
    "related": (
        "Related",
        "text-decoration:underline dotted rgba(234,179,8,0.9);text-decoration-thickness:2px;",
        "Touches the topic of an active narrative",
    ),
    "disputed": ("Disputed", "background-color:rgba(156,163,175,0.25);", "Users dispute this narrative"),
}


def tier(result: dict) -> str | None:
    """The tier of a LookupResult, or None when it is not shown; tierOf in client/lib/tier.ts."""
    signature = result.get("signature")
    if not signature:
        return "reported" if result.get("flagged") else None
    state, level = signature.get("state"), signature.get("matchLevel")
    if state == "disputed":
        return "disputed"
    if state == "under_review":
        return "reported" if signature.get("source") == "report" or result.get("flagged") else None
    if level == "related":
        return "related"
    if level == "narrative":
        return "likely"
    return "confirmed" if signature.get("settled") else "likely"


def label(name: str | None) -> str:
    return TIERS[name][0] if name else "Unclassified"


def mark(text: str, name: str | None) -> str:
    """The text as HTML, styled as the extension highlights its tier."""
    escaped = html.escape(text)
    if name is None:
        return escaped
    return f'<span style="{TIERS[name][1]}padding:0 2px;border-radius:2px">{escaped}</span>'


def legend() -> None:
    st.markdown(
        " &nbsp; ".join(
            f"{mark(title, name)} {html.escape(hint)}" for name, (title, _, hint) in TIERS.items()
        ),
        unsafe_allow_html=True,
    )
