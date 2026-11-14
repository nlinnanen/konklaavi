"""The admin panel's pages and HTMX fragments."""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.admin import charts, queries
from app.admin.session import (
    COOKIE,
    LIFETIME,
    Admin,
    base_href,
    close_session,
    current_admin,
    open_session,
    require_moderator,
    secure,
)
from app.api.account import attempt, check_password
from app.api.notes import moderate
from app.config import settings
from app.db import connection

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
CurrentAdmin = Annotated[Admin, Depends(current_admin)]


def render(request: Request, template: str, admin: Admin | None = None, code: int = 200, **context):
    pending = 0
    if admin is not None:
        with connection() as conn:
            pending = queries.pending_count(conn)
    return templates.TemplateResponse(
        request,
        template,
        {
            "base": base_href(request),
            "admin": admin,
            "pending": pending,
            "htmx": request.headers.get("hx-request") == "true",
            "settings": settings,
            **context,
        },
        status_code=code,
    )


@router.get("", include_in_schema=False)
def root_without_slash() -> Response:
    return RedirectResponse("admin/", status_code=307)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return render(request, "login.html")


@router.post("/login", response_class=HTMLResponse)
def login(request: Request, email: Annotated[str, Form()], password: Annotated[str, Form()]):
    try:
        with connection() as conn:
            account_id = check_password(conn, attempt(conn, email, None), password)
            token = open_session(conn, account_id)
    except HTTPException as error:
        return render(request, "login.html", error=error.detail, email=email, code=error.status_code)
    response = RedirectResponse(base_href(request), status_code=303)
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(LIFETIME.total_seconds()),
        # No path: the browser scopes the cookie to the directory of this form, the admin root,
        # whatever prefix the proxy serves it under.
        path=None,
        httponly=True,
        samesite="lax",
        secure=secure(request),
    )
    return response


@router.post("/logout")
def logout(request: Request) -> Response:
    token = request.cookies.get(COOKIE)
    if token:
        with connection() as conn:
            close_session(conn, token)
    response = RedirectResponse(base_href(request) + "login", status_code=303)
    response.delete_cookie(COOKIE, path=None, httponly=True, samesite="lax", secure=secure(request))
    return response


@router.get("/", response_class=HTMLResponse)
def narratives_page(
    request: Request,
    admin: CurrentAdmin,
    q: str = "",
    state: str = "",
    source: str = "note",
    sort: str = "last_seen",
):
    with connection() as conn:
        rows = queries.narratives(conn, q, state, source, sort)
    template = (
        "_narrative_rows.html" if request.headers.get("hx-target") == "narrative-rows" else "narratives.html"
    )
    return render(
        request,
        template,
        admin,
        rows=rows,
        q=q,
        state=state,
        source=source,
        sort=sort,
        states=queries.STATES,
        page="narratives",
    )


@router.get("/narratives/{narrative_id}", response_class=HTMLResponse)
def narrative_page(request: Request, narrative_id: int, admin: CurrentAdmin):
    with connection() as conn:
        narrative = queries.narrative(conn, narrative_id)
        if narrative is None:
            raise HTTPException(404, f"Narrative {narrative_id} does not exist.")
        members = queries.members(conn, narrative_id, narrative["claim"])
        ids = [m.id for m in members]
        edges = queries.derived_edges(conn, ids)
        timeline = charts.timeline(queries.sightings(conn, ids))
        pages = queries.pages(conn, ids)
        notes = queries.notes(conn, narrative_id=narrative_id)
        evolution = queries.evolution(conn, narrative_id)
    labels = {m.id: f"{m.domain or 'passage'} · {m.content[:34]}" for m in members}
    groups = {level: [m for m in members if m.level == level] for level in queries.LEVELS}
    return render(
        request,
        "narrative.html",
        admin,
        n=narrative,
        groups=groups,
        members=len(members),
        capped=len(members) >= queries.MAX_MEMBERS,
        lineage=charts.lineage(edges, labels),
        timeline=timeline,
        pages=pages,
        notes=notes,
        evolution=evolution,
        page="narratives",
    )


@router.get("/map", response_class=HTMLResponse)
def map_page(request: Request, admin: CurrentAdmin):
    with connection() as conn:
        points, narratives = queries.map_points(conn)
        candidates = queries.cluster_candidates(conn)
        run = queries.last_cluster_run(conn)
    return render(
        request,
        "map.html",
        admin,
        plot=charts.cluster_map(points, narratives),
        capped=sum(p["kind"] == "block" for p in points) >= queries.MAP_POINTS,
        candidates=candidates,
        run=run,
        page="map",
    )


@router.get("/moderation", response_class=HTMLResponse)
def moderation_page(request: Request, admin: CurrentAdmin):
    with connection() as conn:
        queue = queries.notes(conn, moderation="pending_review")
    return render(request, "moderation.html", admin, queue=queue, page="moderation")


@router.post("/moderation/notes/{note_id}", response_class=HTMLResponse)
def moderation_action(
    request: Request, note_id: int, action: Annotated[Literal["approve", "hide"], Form()], admin: CurrentAdmin
):
    require_moderator(admin)
    with connection() as conn:
        moderate(conn, note_id, action)
    with connection() as conn:
        [note] = queries.notes(conn, note_id=note_id)
    return render(request, "_moderated.html", admin, note=note, action=action)


@router.get("/notes", response_class=HTMLResponse)
def notes_page(request: Request, admin: CurrentAdmin, status: str = "", moderation: str = ""):
    with connection() as conn:
        notes = queries.notes(conn, status, moderation)
    return render(
        request,
        "notes.html",
        admin,
        notes=notes,
        status=status,
        moderation=moderation,
        statuses=queries.NOTE_STATUSES,
        moderations=queries.MODERATIONS,
        plot=charts.bridging(notes),
        page="notes",
    )


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request, admin: CurrentAdmin):
    with connection() as conn:
        users = queries.users(conn)
    return render(request, "users.html", admin, users=users, page="users")
