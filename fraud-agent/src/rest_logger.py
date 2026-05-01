"""
REST-based HoneyHive trace logger — bypasses the OTLP ingest path entirely.

Why this exists
---------------
The HoneyHive OTLP endpoint (``/opentelemetry/v1/traces``) was returning 504
gateway timeouts during this demo's development. The REST events API works
independently and is stable, so we build traces by calling:

  POST /session/start   → create the session (root span)
  POST /events          → create one event per node / tool

The resulting session appears in the HoneyHive UI exactly like an OTLP-ingested
trace, with full parent/child span structure and input/output capture. This is
the documented REST contract:
- https://docs.honeyhive.ai/api-reference/session/start-a-new-session
- https://docs.honeyhive.ai/api-reference/events/create-a-new-event

This module is intentionally SDK-free. It uses plain ``requests`` so a broken
OTLP pipeline in the Python SDK cannot interfere.
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

    Usage::

        logger = RestTraceLogger(api_key=..., project="fraud-triage-demo", source="development")
        logger.start_session("fraud-triage-txn_001", inputs={...})
        with logger.span("intake", event_type="chain", inputs={...}) as s:
            s.outputs = {"customer_id": "cust_001"}
        # ... more spans ...
        logger.end_session(outputs={"verdict": ...})
    """

    api_key: str
    project: str
    source: str = "development"
    session_id: str | None = None
    _children: list[str] = field(default_factory=list)
    _session_start_ms: int | None = None
    _session_inputs: dict[str, Any] | None = None
    _session_name: str | None = None

    def start_session(self, name: str, inputs: dict[str, Any] | None = None) -> str:
        """Create a new session on the backend. Returns the session id (also in ``self.session_id``)."""
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
        """
        Finalize session by POSTing a summary session event. This keeps the top-level row
        in HoneyHive UI populated with outputs + duration.
        """
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
        """Context manager for an event (span). Must be called between start_session / end_session."""
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
