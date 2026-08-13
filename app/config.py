"""
Application configuration, loaded from environment variables / .env.

See .env.example for the full list of variables and where to find them
in the Capella UI. The four cb_* connection fields are REQUIRED - there
are no baked-in placeholder defaults - so a missing/incomplete .env fails
immediately and loudly here, instead of silently connecting to a fake
host that then hangs (that used to be possible, and was the root cause
of a real "hang" we tracked down: no defaults, no ambiguity).

Three separate concerns share this one file:
  - cb_* (required): used ONLY by the one-time admin/provisioning scripts
    (scripts/setup_capella.py, seed_data.py, generate_orders.py,
    simulate_traffic.py) via the Couchbase Server SDK. The live web app no
    longer talks to Capella Server directly - see app_services_*.
  - app_services_* (optional): the live app's connection to Capella App
    Services, consumed entirely in the browser via Couchbase Lite for
    JavaScript. Optional so the app still boots and serves pages before
    App Services exists; each page shows a clear "not configured" banner
    instead of the JS silently trying (and hanging) to connect - the
    browser-side version of the same lesson from the cb_* timeout fix.
  - app_services_admin_* (optional): the Admin API credential
    scripts/assign_app_service_channels.py uses to grant the App User its
    channels. A distinct concern from app_services_* above even though the
    two happen to share a value today - see effective_admin_username/
    password, which fall back to app_services_* when left unset.
"""
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to the project root, NOT the process's current
# working directory. Without this, running e.g. `python scripts/seed_data.py`
# from inside scripts/ (or any dir other than the repo root) silently fails
# to find .env - and previously that meant falling back to placeholder
# defaults and connecting to a fake host with no indication why.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Fields that hold secrets - masked when printing a settings summary.
_SECRET_FIELDS = {"cb_password", "app_services_password", "app_services_admin_password"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    # Required: must come from .env (or the real environment) - see
    # .env.example for where to find each of these in the Capella UI.
    cb_conn_string: str
    cb_username: str
    cb_password: str
    cb_bucket: str

    default_store_id: str = "10492"

    # How long to wait for the initial Capella connection before giving up.
    # Bounds *all* of the bootstrap/DNS/connect phases (see app/db.py) -
    # a bad connection string fails within this window instead of hanging
    # for up to two minutes, which is what the SDK's own default allows.
    cb_connect_timeout_seconds: int = 15

    # Capella App Services - the live app's actual data path (see module
    # docstring). Optional: unset until App Services is stood up.
    #
    # App Services ties each App Endpoint to exactly one scope (confirmed
    # against Capella's own docs - "App Endpoints can share scopes but
    # cannot link to the same collections", and creation requires picking
    # a single scope). Our data model has 4 scopes, so there are 4 App
    # Endpoints - one connection URL each, sharing the same host/port:
    #   wss://<app-id>.apps.cloud.couchbase.com:4984/catalog
    #   wss://<app-id>.apps.cloud.couchbase.com:4984/people
    #   wss://<app-id>.apps.cloud.couchbase.com:4984/operations
    #   wss://<app-id>.apps.cloud.couchbase.com:4984/inventory
    # APP_SERVICES_BASE_URL is everything before the scope name
    # (no trailing slash) - see app_services_url_for_scope() below.
    app_services_base_url: Optional[str] = None
    app_services_username: Optional[str] = None
    app_services_password: Optional[str] = None

    # Admin API credential for scripts/assign_app_service_channels.py - a
    # separate concern from app_services_username/password above (that's
    # what the *browser* syncs as; this is what *grants* it channels), kept
    # as its own field even though today, confirmed against a real Capella
    # deployment, it happens to be the same App User. Left blank in .env,
    # effective_admin_username/password below fall back to the sync
    # credential automatically.
    app_services_admin_username: Optional[str] = None
    app_services_admin_password: Optional[str] = None

    @property
    def app_services_configured(self) -> bool:
        return bool(self.app_services_base_url and self.app_services_username and self.app_services_password)

    @property
    def effective_admin_username(self) -> Optional[str]:
        return self.app_services_admin_username or self.app_services_username

    @property
    def effective_admin_password(self) -> Optional[str]:
        return self.app_services_admin_password or self.app_services_password

    def app_services_url_for_scope(self, scope_name: str) -> Optional[str]:
        """The App Endpoint URL for one scope, e.g. app_services_url_for_scope("catalog")
        -> "wss://.../catalog". Endpoint names are expected to match scope
        names exactly - see sync-gateway/app-services-setup.md."""
        if not self.app_services_base_url:
            return None
        return f"{self.app_services_base_url.rstrip('/')}/{scope_name}"

    def describe(self) -> dict:
        """Masked view of the active settings, safe to print/log."""
        data = self.model_dump()
        for field in _SECRET_FIELDS:
            if data.get(field):
                data[field] = "*" * 8
        return data


@lru_cache
def get_settings() -> Settings:
    print(f"[config] .env file: {ENV_FILE} ({'found' if ENV_FILE.exists() else 'NOT FOUND'})")

    try:
        settings = Settings()
    except ValidationError as e:
        print(
            f"[config] ✗ Missing/invalid configuration in {ENV_FILE}:\n{e}\n\n"
            f"Copy .env.example to {ENV_FILE} and fill in CB_CONN_STRING / "
            "CB_USERNAME / CB_PASSWORD / CB_BUCKET from your Capella cluster's "
            "Connect tab and Database Access credentials."
        )
        sys.exit(1)

    for key, value in settings.describe().items():
        print(f"[config]   {key} = {value}")

    return settings
