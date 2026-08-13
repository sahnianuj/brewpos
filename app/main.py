"""FastAPI entrypoint.

The live app no longer talks to Capella Server directly - all data access
happens in the browser via Couchbase Lite for JavaScript, syncing through
Capella App Services (see app/static/js/cbl/ and sync-gateway/). This file's
only jobs are: serve the page shells + static assets, and inject the App
Services connection details (from .env) into each page as
`window.APP_SERVICES_CONFIG`, the same way `DEFAULT_STORE_ID` was already
injected.

The Couchbase Server SDK (app/db.py) is still used, but only by the one-time
admin/provisioning scripts in scripts/ - not by this app.

Run with:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import get_settings

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Brew POS Demo")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# One App Endpoint per scope (see app/config.py) - keep this in sync with
# app.static.js.cbl.client's COLLECTIONS if scopes ever change.
SCOPES = ["catalog", "people", "operations", "inventory"]


def _ctx(request: Request, **extra) -> dict:
    settings = get_settings()
    return {
        "request": request,
        "default_store_id": settings.default_store_id,
        "app_services_config": {
            "configured": settings.app_services_configured,
            "scopeUrls": {scope: settings.app_services_url_for_scope(scope) for scope in SCOPES},
            "username": settings.app_services_username,
            "password": settings.app_services_password,
        },
        **extra,
    }


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse("index.html", _ctx(request))


@app.get("/pos")
def pos(request: Request):
    return templates.TemplateResponse("pos.html", _ctx(request))


@app.get("/kds/espresso")
def kds_espresso(request: Request):
    return templates.TemplateResponse(
        "kds.html", _ctx(request, station="ESPRESSO_BAR", station_label="Espresso Bar")
    )


@app.get("/kds/warming")
def kds_warming(request: Request):
    return templates.TemplateResponse(
        "kds.html", _ctx(request, station="WARMING_STATION", station_label="Warming Station")
    )


@app.get("/pickup")
def pickup(request: Request):
    return templates.TemplateResponse("pickup.html", _ctx(request))


@app.get("/manager")
def manager(request: Request):
    return templates.TemplateResponse("manager.html", _ctx(request))
