"""Automatic semantic analysis when copy is submitted.

Deliberately thin: three seams make it easy to grow later without touching the
API routes.

* ``should_run(trigger)``   — policy: which events analyze automatically.
* ``schedule(...)``         — execution: today FastAPI background tasks; swap
                              for a queue/worker by replacing this one function.
* ``run(...)``              — the job itself; never raises, so a provider outage
                              can never fail or block a submission.

Pending work is tracked in-process so the UI can show "analyzing…". With more
than one server process this needs shared state (e.g. a DB column).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from clearpath import review

logger = logging.getLogger("clearpath.auto_analysis")

TRIGGER_SUBMITTED = "SUBMITTED"
TRIGGER_RESUBMITTED = "RESUBMITTED"
# Extend here (e.g. "POLICY_PUBLISHED", "ASSIGNED") to analyze on more events.
ENABLED_TRIGGERS = {TRIGGER_SUBMITTED, TRIGGER_RESUBMITTED}

_pending: dict[tuple[str, int], str] = {}  # key -> ISO start time
_lock = threading.Lock()


def should_run(trigger: str) -> bool:
    if trigger not in ENABLED_TRIGGERS:
        return False
    if not review.semantic_mode_enabled():
        return False
    return os.environ.get("CLEARPATH_AUTO_ANALYZE", "true").lower() not in {"0", "false", "no"}


def is_pending(submission_id: str, version: int) -> bool:
    return pending_since(submission_id, version) is not None


def pending_since(submission_id: str, version: int) -> str | None:
    """ISO start time of the running analysis, or None when idle."""
    with _lock:
        return _pending.get((submission_id, version))


def run(db_path, submission_id: str, version: int, actor_id: str) -> None:
    """Analyze the submission's current copy as ``actor_id``. Never raises."""
    try:
        review.analyze_submission(
            db_path,
            submission_id,
            actor_id,
            datetime.now(timezone.utc).isoformat(),
            authorize=False,  # system-initiated: not a reviewer action
        )
    except Exception:  # noqa: BLE001 — must not surface to the submitter
        logger.exception("Auto-analysis failed for %s v%s", submission_id, version)
    finally:
        with _lock:
            _pending.pop((submission_id, version), None)


def schedule(background_tasks, db_path, trigger: str, submission_id: str, version: int, actor_id: str) -> bool:
    """Queue an analysis after the response is sent. Returns whether queued."""
    if not should_run(trigger):
        return False
    with _lock:
        _pending[(submission_id, version)] = datetime.now(timezone.utc).isoformat()
    background_tasks.add_task(run, db_path, submission_id, version, actor_id)
    return True
