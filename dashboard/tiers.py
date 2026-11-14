"""Match tiers, decided from a signature as the extension decides them, with its colours and labels."""

import html

import streamlit as st

# Strongest first, the order the legend lists them in.
TIERS = {
    "confirmed": ("Confirmed", "background:#dc2626;color:#fff;", "Known misinformation, settled"),
    "likely": ("Likely", "background:rgba(249,115,22,0.35);", "Close in meaning to an active narrative"),
    "related": (
        "Related",
        "text-decoration:underline dotted #eab308 2px;",
        "Touches the topic of an active narrative",
    ),
    "reported": ("Reported", "background:rgba(168,85,247,0.18);", "Flagged by users, not confirmed"),
    "disputed": ("Disputed", "background:rgba(156,163,175,0.35);", "Users dispute this narrative"),
}


def tier(signature: dict | None) -> str | None:
    """The signature's tier, or None for a match no tier covers."""
    if not signature:
        return None
    state, level = signature.get("state"), signature.get("matchLevel")
    if state == "disputed":
        return "disputed"
    if state == "under_review" and signature.get("source") == "report":
        return "reported"
    if state != "active":
        return None
    if level in ("exact", "variant"):
        return "confirmed" if signature.get("settled") else None
    if level == "narrative":
        return "likely"
    if level == "related":
        return "related"
    return None


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
