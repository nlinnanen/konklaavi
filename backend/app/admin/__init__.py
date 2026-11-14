"""The moderators' admin panel, served at /admin: Jinja2 pages with HTMX, signed in by cookie session."""

import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from app.admin.session import SignInRequired, base_href, signed_in
from app.admin.views import render, router

_PAGE = re.compile(r"/admin(/(?!static/)|$)")


def _sign_in(request: Request, _: SignInRequired) -> Response:
    target = base_href(request) + "login"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=200, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


async def _errors(request: Request, error: HTTPException) -> Response:
    """Admin errors as a page, or as text an HTMX request shows in place; the API keeps its JSON."""
    if not _PAGE.search(request.url.path):
        return await http_exception_handler(request, error)
    if request.headers.get("hx-request") == "true":
        return Response(str(error.detail), status_code=error.status_code, media_type="text/plain")
    return render(request, "error.html", signed_in(request), code=error.status_code, error=error)


def install(app: FastAPI) -> None:
    app.mount("/admin/static", StaticFiles(directory=Path(__file__).parent / "static"), name="admin-static")
    app.include_router(router, prefix="/admin", include_in_schema=False)
    app.add_exception_handler(SignInRequired, _sign_in)
    app.add_exception_handler(HTTPException, _errors)
