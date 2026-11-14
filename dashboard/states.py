"""Narrative display states with the extension's highlight styles and their labels."""

import html

import streamlit as st

# Strongest first. Each state has a style for exact and variant matches and a lighter one for narrative and
# related matches.
STATES = {
    "misleading": (
        "Misleading",
        "background-color:#dc2626;color:#fff;",
        "background-color:rgba(220,38,38,0.25);",
        "A helpful note says this is misleading",
    ),
    "context": (
        "Context",
        "background-color:rgba(14,165,233,0.22);border-bottom:2px solid #0284c7;",
        "background-color:rgba(14,165,233,0.1);border-bottom:1px solid #0284c7;",
        "A helpful note adds context",
    ),
    "controversial": (
        "Controversial",
        "background-color:rgba(107,114,128,0.25);",
        "background-color:rgba(107,114,128,0.12);",
        "Raters disagree",
    ),
    "needs_ratings": (
        "Needs ratings",
        "background-color:rgba(234,179,8,0.15);",
        "background-color:rgba(234,179,8,0.07);",
        "Notes waiting for ratings",
    ),
    "tracked": (
        "Tracked",
        "text-decoration:underline dotted rgba(100,116,139,0.9);",
        "text-decoration:underline dotted rgba(100,116,139,0.5);",
        "Part of a narrative seen on other sites",
    ),
}
LIGHT_LEVELS = ("narrative", "related")


def label(state: str | None) -> str:
    return STATES[state][0] if state in STATES else "Unknown"


def mark(text: str, state: str | None, level: str | None = None) -> str:
    """The text as HTML, styled as the extension highlights a match of its state and level."""
    escaped = html.escape(text)
    if state not in STATES:
        return escaped
    style = STATES[state][2 if level in LIGHT_LEVELS else 1]
    return f'<span style="{style}padding:0 2px;border-radius:2px">{escaped}</span>'


def legend() -> None:
    st.markdown(
        " &nbsp; ".join(
            f"{mark(title, state)} {html.escape(hint)}" for state, (title, _, _, hint) in STATES.items()
        ),
        unsafe_allow_html=True,
    )
