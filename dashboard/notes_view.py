"""How the dashboard shows one note."""

from urllib.parse import quote, urlsplit

import pandas as pd
import streamlit as st

STATUS = {
    "helpful": "Helpful",
    "not_helpful": "Not helpful",
    "controversial": "Controversial",
    "needs_more_ratings": "Needs more ratings",
}
CLASSIFICATION = {"misleading": "Misleading", "not_misleading": "Not misleading"}


def source_link(url: str) -> str:
    """A Markdown link to an http(s) source labelled with its host, or the escaped text of anything else."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"`{url.replace('`', '')}`"
    return f"[{parts.hostname}]({quote(url, safe=":/?#@!$&'*+,;=%~")})"


def show_note(note) -> None:
    """A row of queries._NOTE_COLUMNS in a bordered box. The body is user text, so it is shown as plain text."""
    with st.container(border=True):
        moderation = "" if note.moderation == "visible" else f" · {note.moderation.replace('_', ' ')}"
        st.markdown(
            f"**{CLASSIFICATION.get(note.classification, note.classification)}** ·"
            f" {STATUS.get(note.status, note.status)}{moderation} · #{note.id} · {note.created_at:%Y-%m-%d %H:%M}"
        )
        st.text(note.body)
        if isinstance(note.sources, list) and note.sources:
            st.markdown("Sources: " + " · ".join(source_link(url) for url in note.sources))
        if isinstance(note.moderation_reason, str) and note.moderation_reason:
            st.text(f"Moderation: {note.moderation_reason}")
        scores = (
            "" if pd.isna(note.intercept) else f" · intercept {note.intercept:.2f}, factor {note.factor:.2f}"
        )
        st.caption(
            f"{note.counted} counted ratings of {note.ratings}: {note.helpful} helpful, {note.somewhat} somewhat,"
            f" {note.not_helpful} not helpful{scores}"
        )
