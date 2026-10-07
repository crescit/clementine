"""Policy storage and administration service (S1).

One workspace policy set: one mutable draft, one active immutable
publication. Draft saves use optimistic concurrency (`expected_draft_version`).
Publish checks the expected draft and active version, validates the complete
set, and atomically creates a new snapshot and advances the active pointer —
no concurrent last-write-wins publishing. Published snapshots cannot be
edited or deleted through the API. A separate `policy_audit` trail records
author/publisher/timestamps/policy hash for every policy event.

Capability (`manage_policies`) is resolved server-side from the `permissions`
table. The browser never supplies a capability or role.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

# --- Canonical error codes -----------------------------------------------------
CAPABILITY_ERROR = "CAPABILITY_REQUIRED"
STALE_DRAFT = "STALE_DRAFT"
STALE_PUBLISH = "STALE_PUBLISH"
INVALID_RULE = "INVALID_RULE"


class PolicyError(Exception):
    """Domain error carrying an API code + HTTP status."""

    def __init__(self, code: str, message: str, status: int, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# --- Validation limits ----------------------------------------------------------
RULE_KEY_MAX = 32
TITLE_MAX = 120
INSTRUCTIONS_MAX = 2000
LITERAL_MAX = 500
RULE_COUNT_MAX = 50

RULE_KINDS = {"semantic", "required_disclosure"}
PRODUCTS = {"PERSONAL_LOAN", "CREDIT_CARD", "MORTGAGE_PREQUALIFICATION"}
CHANNELS = {"AFFILIATE", "EMAIL", "SOCIAL", "PAID_SEARCH", "WEBSITE"}


# --- Baseline snapshot ----------------------------------------------------------
# Seeded from the current disclosure rules (preflight). Immutable once published.
BASELINE_VERSION = 1
BASELINE_LABEL = "clearpath-demo-v1"

BASELINE_RULES: list[dict[str, Any]] = [
    {
        "rule_key": "CLAIM_001",
        "title": "Restricted approval claim",
        "instructions": (
            "Demo policy requires pre-qualified; confirm offer accuracy. "
            "Reject any claim that a loan is pre-approved."
        ),
        "kind": "semantic",
        "required_literal": None,
        "product": None,
        "channel": None,
        "enabled": 1,
    },
    {
        "rule_key": "CLAIM_002",
        "title": "Guaranteed approval claim",
        "instructions": (
            "Remove the guarantee; approval remains subject to review. "
            "Flag any claim of guaranteed approval."
        ),
        "kind": "semantic",
        "required_literal": None,
        "product": None,
        "channel": None,
        "enabled": 1,
    },
    {
        "rule_key": "DISC_001",
        "title": "Missing credit approval disclosure",
        "instructions": "Add the exact demo disclosure verbatim.",
        "kind": "required_disclosure",
        "required_literal": "Subject to credit approval.",
        "product": "PERSONAL_LOAN",
        "channel": None,
        "enabled": 1,
    },
    {
        "rule_key": "DISC_002",
        "title": "Missing mortgage prequalification disclosure",
        "instructions": "Add the exact demo disclosure verbatim.",
        "kind": "required_disclosure",
        "required_literal": "Prequalification is not a commitment to lend.",
        "product": "MORTGAGE_PREQUALIFICATION",
        "channel": None,
        "enabled": 1,
    },
    {
        "rule_key": "DISC_003",
        "title": "Missing partner disclosure",
        "instructions": "Add the exact demo partner disclosure verbatim.",
        "kind": "required_disclosure",
        "required_literal": "ClearPath may compensate this partner.",
        "product": None,
        "channel": "AFFILIATE",
        "enabled": 1,
    },
]


# --- Canonicalization ------------------------------------------------------------

def _canonical_rule(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one rule into a canonical, JSON-safe shape."""
    key = str(raw["rule_key"]).strip()
    title = str(raw["title"]).strip()
    instructions = str(raw["instructions"]).strip()
    kind = str(raw["kind"]).strip()
    literal = raw.get("required_literal")
    if literal is not None:
        literal = str(literal).strip() or None
    product = raw.get("product")
    if product is not None:
        product = str(product).strip() or None
    channel = raw.get("channel")
    if channel is not None:
        channel = str(channel).strip() or None
    enabled = int(bool(raw.get("enabled", 1)))
    return {
        "rule_key": key,
        "title": title,
        "instructions": instructions,
        "kind": kind,
        "required_literal": literal,
        "product": product,
        "channel": channel,
        "enabled": enabled,
    }


def _canonical_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_canonical_rule(r) for r in rules]


def validate_rules(rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the complete rule set; raise PolicyError on any invalid rule."""
    if not isinstance(rules, list):
        raise PolicyError(INVALID_RULE, "rules must be a list", 400, {"field": "rules"})
    if len(rules) == 0:
        raise PolicyError(INVALID_RULE, "A policy set must contain at least one rule", 400)
    if len(rules) > RULE_COUNT_MAX:
        raise PolicyError(
            INVALID_RULE,
            f"Too many rules: {len(rules)} exceeds {RULE_COUNT_MAX}",
            400,
            {"field": "rules"},
        )

    seen: set[str] = set()
    for idx, raw in enumerate(rules):
        rule = _canonical_rule(raw)
        if len(rule["rule_key"]) > RULE_KEY_MAX:
            raise PolicyError(
                INVALID_RULE,
                f"rule_key '{rule['rule_key']}' exceeds {RULE_KEY_MAX} chars",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if len(rule["title"]) > TITLE_MAX:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' title exceeds {TITLE_MAX} chars",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if len(rule["instructions"]) > INSTRUCTIONS_MAX:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' instructions exceed {INSTRUCTIONS_MAX} chars",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if rule["kind"] not in RULE_KINDS:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' has invalid kind '{rule['kind']}'",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if rule["product"] is not None and rule["product"] not in PRODUCTS:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' has invalid product '{rule['product']}'",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if rule["channel"] is not None and rule["channel"] not in CHANNELS:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' has invalid channel '{rule['channel']}'",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if rule["required_literal"] is not None and len(rule["required_literal"]) > LITERAL_MAX:
            raise PolicyError(
                INVALID_RULE,
                f"Rule '{rule['rule_key']}' required_literal exceeds {LITERAL_MAX} chars",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        if rule["rule_key"] in seen:
            raise PolicyError(
                INVALID_RULE,
                f"Duplicate rule_key '{rule['rule_key']}'",
                400,
                {"field": "rules", "index": idx, "rule_key": rule["rule_key"]},
            )
        seen.add(rule["rule_key"])

    return _canonical_rules(rules)


def policy_hash(rules: list[dict[str, Any]]) -> str:
    """Deterministic SHA-256 over the canonical rule set."""
    canonical = _canonical_rules(rules)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Capability ------------------------------------------------------------------

def check_capability(conn: sqlite3.Connection, user_id: str | None, capability: str) -> None:
    """Resolve a capability server-side from `permissions`. Raises 401/403."""
    if not user_id:
        raise PolicyError("UNAUTHENTICATED", "Missing authenticated user", 401)
    row = conn.execute(
        "SELECT 1 FROM permissions WHERE user_id = ? AND capability = ?",
        (user_id, capability),
    ).fetchone()
    if row is None:
        raise PolicyError(
            CAPABILITY_ERROR,
            f"You do not have the '{capability}' capability",
            403,
            {"capability": capability},
        )


def grant_capability(conn: sqlite3.Connection, user_id: str, capability: str, now: str) -> None:
    """Grant (idempotently) a capability to a user."""
    conn.execute(
        "INSERT OR IGNORE INTO permissions (user_id, capability, granted_at) "
        "VALUES (?, ?, ?)",
        (user_id, capability, now),
    )


# --- Seeding ---------------------------------------------------------------------

def _active_snapshot_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT id FROM policy_snapshots ORDER BY version LIMIT 1"
    ).fetchone()
    return str(row["id"]) if row else None


def seed_baseline(conn: sqlite3.Connection, reviewer_id: str, now: str) -> None:
    """Seed the baseline immutable snapshot and the matching mutable draft.

    Called once on a fresh DB (or after a demo reset). Grants `manage_policies`
    to the seeded reviewer persona and publishes the baseline snapshot (version
    1) matching the current disclosure rules (`clearpath-demo-v1`).
    """
    grant_capability(conn, reviewer_id, "manage_policies", now)

    canonical = _canonical_rules(BASELINE_RULES)
    hash_value = policy_hash(canonical)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT OR IGNORE INTO policy_snapshots "
        "(id, version, label, policy_hash, rules_json, published_by, "
        "published_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            BASELINE_VERSION,
            BASELINE_LABEL,
            hash_value,
            payload,
            reviewer_id,
            now,
            now,
        ),
    )

    # Mutable draft at version 0 (the baseline working copy).
    conn.execute(
        "INSERT OR REPLACE INTO policy_state (id, active_snapshot_id, "
        "current_draft_version, current_draft_hash, updated_at) "
        "VALUES (1, ?, 0, ?, ?)",
        (_active_snapshot_id(conn), hash_value, now),
    )

    # Draft rows at version 0.
    for rule in canonical:
        conn.execute(
            "INSERT OR IGNORE INTO policy_rules (id, draft_version, rule_key, "
            "title, instructions, kind, required_literal, product, channel, "
            "enabled, created_at, updated_at) VALUES (?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                rule["rule_key"],
                rule["title"],
                rule["instructions"],
                rule["kind"],
                rule.get("required_literal"),
                rule.get("product"),
                rule.get("channel"),
                rule["enabled"],
                now,
                now,
            ),
        )

    conn.execute(
        "INSERT INTO policy_audit (actor_id, event_type, snapshot_version, "
        "policy_hash, created_at) VALUES (?, 'PUBLISHED', ?, ?, ?)",
        (reviewer_id, BASELINE_VERSION, hash_value, now),
    )


# --- State / draft / snapshot reads ------------------------------------------------

def get_state(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        "SELECT active_snapshot_id, current_draft_version, current_draft_hash "
        "FROM policy_state WHERE id = 1"
    ).fetchone()
    if row is None:
        raise PolicyError("INTERNAL_ERROR", "policy_state row missing", 500)
    return {
        "active_snapshot_id": row["active_snapshot_id"],
        "current_draft_version": row["current_draft_version"],
        "current_draft_hash": row["current_draft_hash"],
    }


def get_draft(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return the current mutable draft rule set (ordered)."""
    state = get_state(conn)
    version = state["current_draft_version"]
    rows = conn.execute(
        "SELECT rule_key, title, instructions, kind, required_literal, product, "
        "channel, enabled FROM policy_rules WHERE draft_version = ? ORDER BY rowid",
        (version,),
    ).fetchall()
    return [
        {
            "rule_key": r["rule_key"],
            "title": r["title"],
            "instructions": r["instructions"],
            "kind": r["kind"],
            "required_literal": r["required_literal"],
            "product": r["product"],
            "channel": r["channel"],
            "enabled": r["enabled"],
        }
        for r in rows
    ]


def list_snapshots(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Immutable publication history, oldest first."""
    rows = conn.execute(
        "SELECT id, version, label, policy_hash, published_by, published_at "
        "FROM policy_snapshots ORDER BY version"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "version": r["version"],
            "label": r["label"],
            "policy_hash": r["policy_hash"],
            "published_by": r["published_by"],
            "published_at": r["published_at"],
        }
        for r in rows
    ]


def get_snapshot(conn: sqlite3.Connection, version: int) -> dict[str, Any] | None:
    """Return one immutable snapshot (with its rule set), or None."""
    row = conn.execute(
        "SELECT id, version, label, policy_hash, rules_json, published_by, "
        "published_at FROM policy_snapshots WHERE version = ?",
        (version,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"],
        "version": row["version"],
        "label": row["label"],
        "policy_hash": row["policy_hash"],
        "rules": json.loads(row["rules_json"]),
        "published_by": row["published_by"],
        "published_at": row["published_at"],
    }


def audit_trail(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Policy event history (draft save / publish / enable-disable), newest first."""
    rows = conn.execute(
        "SELECT id, actor_id, event_type, draft_version, snapshot_version, "
        "policy_hash, metadata_json, created_at "
        "FROM policy_audit ORDER BY id DESC"
    ).fetchall()
    return [
        {
            "id": r["id"],
            "actor_id": r["actor_id"],
            "event_type": r["event_type"],
            "draft_version": r["draft_version"],
            "snapshot_version": r["snapshot_version"],
            "policy_hash": r["policy_hash"],
            "metadata": json.loads(r["metadata_json"]) if r["metadata_json"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]


# --- Draft save / publish ------------------------------------------------------------

def save_draft(
    conn: sqlite3.Connection,
    actor_id: str,
    rules: list[dict[str, Any]],
    expected_draft_version: int | None,
    now: str,
) -> dict[str, Any]:
    """Validate and save a new mutable draft (optimistic concurrency)."""
    check_capability(conn, actor_id, "manage_policies")
    canonical = validate_rules(rules)

    state = get_state(conn)
    current_version = state["current_draft_version"]
    if expected_draft_version is not None and expected_draft_version != current_version:
        raise PolicyError(
            STALE_DRAFT,
            f"Stale draft: expected version {expected_draft_version}, "
            f"current is {current_version}",
            409,
            {"expected_draft_version": expected_draft_version, "current_draft_version": current_version},
        )

    next_version = current_version + 1
    hash_value = policy_hash(canonical)

    # Insert the new draft rows.
    for rule in canonical:
        conn.execute(
            "INSERT INTO policy_rules (id, draft_version, rule_key, title, "
            "instructions, kind, required_literal, product, channel, enabled, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                next_version,
                rule["rule_key"],
                rule["title"],
                rule["instructions"],
                rule["kind"],
                rule.get("required_literal"),
                rule.get("product"),
                rule.get("channel"),
                rule["enabled"],
                now,
                now,
            ),
        )

    conn.execute(
        "UPDATE policy_state SET current_draft_version = ?, current_draft_hash = ?, "
        "updated_at = ? WHERE id = 1",
        (next_version, hash_value, now),
    )

    conn.execute(
        "INSERT INTO policy_audit (actor_id, event_type, draft_version, "
        "policy_hash, created_at) VALUES (?, 'DRAFT_SAVED', ?, ?, ?)",
        (actor_id, next_version, hash_value, now),
    )

    return {
        "draft_version": next_version,
        "draft_hash": hash_value,
        "rules": canonical,
    }


def publish_draft(
    conn: sqlite3.Connection,
    actor_id: str,
    expected_draft_version: int,
    now: str,
) -> dict[str, Any]:
    """Atomically publish the current draft as a new immutable snapshot."""
    check_capability(conn, actor_id, "manage_policies")

    state = get_state(conn)
    current_version = state["current_draft_version"]
    if current_version != expected_draft_version:
        raise PolicyError(
            STALE_PUBLISH,
            f"Stale publish: expected draft {expected_draft_version}, "
            f"current is {current_version}",
            409,
            {"expected_draft_version": expected_draft_version, "current_draft_version": current_version},
        )

    draft = get_draft(conn)
    hash_value = policy_hash(draft)
    payload = json.dumps(draft, sort_keys=True, separators=(",", ":"))

    max_row = conn.execute(
        "SELECT COALESCE(MAX(version), 0) AS mv FROM policy_snapshots"
    ).fetchone()
    next_version = int(max_row["mv"]) + 1

    conn.execute(
        "INSERT INTO policy_snapshots (id, version, label, policy_hash, "
        "rules_json, published_by, published_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(uuid.uuid4()),
            next_version,
            "clearpath-demo-v" + str(next_version),
            hash_value,
            payload,
            actor_id,
            now,
            now,
        ),
    )

    # Point the active pointer at the new immutable snapshot.
    active_row = conn.execute(
        "SELECT id FROM policy_snapshots WHERE version = ?",
        (next_version,),
    ).fetchone()
    conn.execute(
        "UPDATE policy_state SET active_snapshot_id = ?, updated_at = ? WHERE id = 1",
        (active_row["id"], now),
    )

    conn.execute(
        "INSERT INTO policy_audit (actor_id, event_type, snapshot_version, "
        "policy_hash, created_at) VALUES (?, 'PUBLISHED', ?, ?, ?)",
        (actor_id, next_version, hash_value, now),
    )

    return {
        "version": next_version,
        "policy_hash": hash_value,
        "published_by": actor_id,
        "published_at": now,
        "rules": draft,
    }
