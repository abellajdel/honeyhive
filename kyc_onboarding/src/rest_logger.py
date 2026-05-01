"""
REST-based HoneyHive trace logger — bypasses the OTLP ingest path entirely.

HoneyHive REST contract:
https://docs.honeyhive.ai/api-reference/session/start-a-new-session
https://docs.honeyhive.ai/api-reference/events/create-a-new-event

This module is intentionally SDK-free so a broken OTLP pipeline cannot block demos.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import requests

_BASE = os.environ.get("HONEYHIVE_API_URL", "https://api.honeyhive.ai").rstrip("/")
_TIMEOUT = float(os.environ.get("HONEYHIVE_REST_TIMEOUT", "30"))


class ProjectsListForbidden(RuntimeError):
    """Raised when GET /projects is denied — strong signal the API key is project-scoped."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(
            f"GET /projects denied (HTTP {status}): {body!r}. "
            "API key is most likely project-scoped — traces will pin to the key's project regardless of payload."
        )
        self.status = status
        self.body = body


def _now_ms() -> int:
    return int(time.time() * 1000)


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


@dataclass
class RestTraceLogger:
    """
    Lightweight trace logger that writes one session + N events via the REST API.
    """

    api_key: str
    project: str
    source: str = "development"
    session_id: str | None = None
    _children: list[str] = field(default_factory=list)
    _session_start_ms: int | None = None
    _session_inputs: dict[str, Any] | None = None
    _session_name: str | None = None

    def list_projects(self) -> list[dict[str, Any]]:
        """
        GET /projects — used to verify the configured project exists for this API key.

        Raises ``ProjectsListForbidden`` if the API responds 401/403 ("Forbidden route"), which
        means the API key is **project-scoped** (workspace-scoped keys can list projects).
        Project-scoped keys force every trace into the key's project regardless of the ``project``
        field in the payload — that is the most likely reason ``HONEYHIVE_PROJECT=kyc-onboarding``
        traces still appear under the key's home project.
        """
        r = requests.get(
            f"{_BASE}/projects",
            headers=_headers(self.api_key),
            timeout=_TIMEOUT,
        )
        if r.status_code in (401, 403):
            raise ProjectsListForbidden(r.status_code, r.text[:400])
        if r.status_code >= 400:
            raise RuntimeError(
                f"/projects (list) failed {r.status_code}: {r.text[:400]!r}"
            )
        data = r.json()
        if isinstance(data, list):
            return [p for p in data if isinstance(p, dict)]
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            return [p for p in data["projects"] if isinstance(p, dict)]
        return []

    def create_project(self, description: str | None = None) -> dict[str, Any]:
        """POST /projects — best-effort auto-create of the configured slug."""
        payload = {"name": self.project, "description": description or self.project}
        r = requests.post(
            f"{_BASE}/projects",
            json=payload,
            headers=_headers(self.api_key),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"/projects (create) failed {r.status_code}: {r.text[:400]!r}"
            )
        try:
            return r.json() or {}
        except ValueError:
            return {}

    def ensure_project_exists(self, *, auto_create: bool = True) -> str:
        """
        Verify ``self.project`` exists in HoneyHive for this API key.

        Behavior:
          - Returns ``"existing"`` if the project is in the workspace listing.
          - Returns ``"created"`` if missing and we successfully POST /projects.
          - Returns ``"unknown_project_scoped_key"`` when the API key cannot list projects (HTTP 403 from
            ``/projects``). Project-scoped HoneyHive keys (Settings → Project → API Keys → key created
            inside a single project) route ALL traces into the key's bound project regardless of the
            ``project`` field on REST payloads. We emit a loud warning and continue so the run still produces
            telemetry — even if it lands in the bound project, not ``self.project``.
          - Raises ``RuntimeError`` for other listing/creation failures.
        """
        try:
            projects = self.list_projects()
        except RuntimeError as list_err:
            if "failed 403" in str(list_err):
                print(
                    "[kyc-onboarding] WARNING: HoneyHive API key cannot list projects (403). "
                    "This API key is project-scoped (created inside one project's Settings → API Keys). "
                    f"All traces — including this run — will land in the key's bound project, NOT "
                    f"{self.project!r}. To send traces to {self.project!r} in the UI, create the project "
                    "in HoneyHive (UI → New Project), open it, then create a NEW API key inside its "
                    "Settings → API Keys, and put that key in HONEYHIVE_API_KEY_KYC (preferred) or "
                    "HONEYHIVE_API_KEY in your .env.",
                    flush=True,
                )
                return "unknown_project_scoped_key"
            raise

        names = [str(p.get("name") or "") for p in projects]
        if self.project in names:
            return "existing"

        if auto_create:
            try:
                self.create_project()
                return "created"
            except Exception as create_err:  # noqa: BLE001 - surfaced below with full context
                msg = (
                    f"HoneyHive project {self.project!r} does not exist and auto-create failed.\n"
                    f"Available projects for this API key: {names!r}\n"
                    f"Auto-create error: {create_err}\n"
                    f"Fix: set HONEYHIVE_PROJECT in .env to one of the available names, "
                    f"or create {self.project!r} in the HoneyHive UI."
                )
                raise RuntimeError(msg) from create_err

        raise RuntimeError(
            f"HoneyHive project {self.project!r} does not exist for this API key. "
            f"Available: {names!r}"
        )

    def start_session(self, name: str, inputs: dict[str, Any] | None = None) -> str:
        """Create a new session on the backend. Returns the session id."""
        self.session_id = str(uuid.uuid4())
        self._session_start_ms = _now_ms()
        self._session_name = name
        self._session_inputs = inputs or {}
        payload = {
            "session": {
                "project": self.project,
                "source": self.source,
                "session_name": name,
                "session_id": self.session_id,
                "inputs": self._session_inputs,
                "start_time": self._session_start_ms,
            }
        }
        r = requests.post(
            f"{_BASE}/session/start",
            json=payload,
            headers=_headers(self.api_key),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(
                f"/session/start failed {r.status_code}: {r.text[:400]!r}"
            )
        return self.session_id

    def end_session(
        self,
        outputs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Finalize session with a summary event."""
        if self.session_id is None or self._session_start_ms is None:
            return
        end_ms = _now_ms()
        payload = {
            "event": {
                "project": self.project,
                "source": self.source,
                "session_id": self.session_id,
                "event_id": self.session_id,
                "parent_id": self.session_id,
                "event_type": "session",
                "event_name": self._session_name or "session",
                "config": {},
                "inputs": self._session_inputs or {},
                "outputs": outputs or {},
                "metadata": metadata or {},
                "start_time": self._session_start_ms,
                "end_time": end_ms,
                "duration": float(end_ms - self._session_start_ms),
                "children_ids": list(self._children),
            }
        }
        r = requests.post(
            f"{_BASE}/events",
            json=payload,
            headers=_headers(self.api_key),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"/events (session finalize) failed {r.status_code}: {r.text[:400]!r}")

    def span(
        self,
        name: str,
        *,
        event_type: str = "chain",
        inputs: dict[str, Any] | None = None,
        config: dict[str, Any] | None = None,
    ) -> "_SpanCtx":
        """Context manager for an event (span)."""
        if self.session_id is None:
            raise RuntimeError("start_session() must be called first")
        return _SpanCtx(
            logger=self,
            name=name,
            event_type=event_type,
            inputs=inputs or {},
            config=config or {},
        )

    def _post_event(self, event: dict[str, Any]) -> None:
        r = requests.post(
            f"{_BASE}/events",
            json={"event": event},
            headers=_headers(self.api_key),
            timeout=_TIMEOUT,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"/events failed {r.status_code}: {r.text[:400]!r}")


@dataclass
class _SpanCtx:
    logger: RestTraceLogger
    name: str
    event_type: str
    inputs: dict[str, Any]
    config: dict[str, Any]
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    _start_ms: int = 0
    _event_id: str = ""

    def __enter__(self) -> "_SpanCtx":
        self._start_ms = _now_ms()
        self._event_id = str(uuid.uuid4())
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        end_ms = _now_ms()
        if exc is not None:
            self.error = f"{exc_type.__name__ if exc_type else 'Error'}: {exc}"
        payload = {
            "project": self.logger.project,
            "source": self.logger.source,
            "session_id": self.logger.session_id,
            "event_id": self._event_id,
            "parent_id": self.logger.session_id,
            "event_type": self.event_type,
            "event_name": self.name,
            "config": self.config,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "metadata": self.metadata,
            "error": self.error,
            "start_time": self._start_ms,
            "end_time": end_ms,
            "duration": float(end_ms - self._start_ms),
        }
        self.logger._post_event(payload)
        self.logger._children.append(self._event_id)

