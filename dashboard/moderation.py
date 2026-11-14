import uuid

import httpx
import streamlit as st

from common import API_URL, me, query
from notes_view import show_note
from queries import MODERATION_QUEUE


def moderate(note_id: int, action: str) -> None:
    response = httpx.post(
        f"{API_URL}/moderation/notes/{note_id}", json={"userId": user_id, "action": action}, timeout=30
    )
    if response.status_code == 200:
        query.clear()
        st.rerun()
    elif response.status_code == 403:
        st.error("This account is not a moderator.")
    else:
        st.error(f"The API answered {response.status_code}: {response.text}")


st.title("Moderation")
st.caption(
    "Notes the LLM review held and notes readers reported. Approving shows a note; hiding removes it."
    " Moderators are accounts listed in MODERATOR_EMAILS or with enough reputation."
)

# The dashboard signs in like an extension install: a random install id linked to an account by magic link.
if "install" not in st.session_state:
    st.session_state.install = str(uuid.uuid4())
user_id = st.text_input(
    "Install id",
    key="install",
    help="This browser session's id, or the userId of an extension install already signed in",
)

try:
    profile = me(user_id)
except httpx.HTTPError as error:
    st.error(f"Could not reach the API at {API_URL}: {error}")
    st.stop()

if not profile.get("verified"):
    with st.form("sign-in"):
        st.write("Sign in with a moderator account. The API emails a link; open it, then reload this page.")
        email = st.text_input("Email")
        if st.form_submit_button("Send sign-in link") and email.strip():
            response = httpx.post(
                f"{API_URL}/auth/magic-link", json={"userId": user_id, "email": email.strip()}, timeout=30
            )
            if response.status_code == 200:
                st.success(f"Link sent to {email.strip()}. After opening it, reload to refresh.")
                me.clear()
            else:
                st.error(f"The API answered {response.status_code}: {response.text}")
    st.stop()

st.success(f"Signed in as {profile['email']}, reputation {profile.get('reputation', 0):.2f}")

queue = query(MODERATION_QUEUE)
st.subheader(f"Queue ({len(queue)})")
if queue.empty:
    st.caption("Nothing to review.")
for note in queue.itertuples():
    st.markdown(f"On **{note.narrative}** (narrative #{note.narrative_id})")
    show_note(note)
    approve, hide, _ = st.columns([1, 1, 6])
    if approve.button("Approve", key=f"approve-{note.id}", type="primary"):
        moderate(note.id, "approve")
    if hide.button("Hide", key=f"hide-{note.id}"):
        moderate(note.id, "hide")
