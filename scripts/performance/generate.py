"""Deterministic, isolated scale fixtures. Refuses to overwrite any database.

Run from the repository root:
  uv run python -m scripts.performance.generate --database /tmp/cp-10k.db --records 10000
"""

from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import uuid
from unittest.mock import patch

from clearpath import db, seed


def generate(
    path: Path, records: int, random_seed: int = 42, now: datetime | None = None
) -> dict:
    if records < 7:
        raise ValueError("At least seven records are needed for the baseline scenarios")
    path = path.resolve()
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite {path}; choose a fresh path")
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    rng = random.Random(random_seed)
    uid = lambda name: str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"clearpath-scale:{random_seed}:{name}")
    )
    iso = lambda t: t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = db.connect(path)
    try:
        db.initialize_schema(conn)
        # Preserve the golden walkthrough; all added campaigns are explicitly synthetic.
        with patch(
            "clearpath.seed.uuid.uuid4",
            side_effect=lambda: uuid.UUID(int=rng.getrandbits(128), version=4),
        ):
            seed.seed_database(conn, now)
        reviewers = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM users WHERE role='REVIEWER' ORDER BY name"
            )
        ]
        submitters = [
            r[0] for r in conn.execute("SELECT id FROM users WHERE role='SUBMITTER'")
        ]
        for role, count, existing in [
            ("REVIEWER", 38, reviewers),
            ("SUBMITTER", 199, submitters),
        ]:
            for i in range(count):
                user_id = uid(f"{role}-{i}")
                conn.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
                    (
                        user_id,
                        f"Synthetic {role.title()} {i + 1:03}",
                        role,
                        "Synthetic load-test persona",
                        iso(now),
                    ),
                )
                existing.append(user_id)
        products = ["PERSONAL_LOAN", "CREDIT_CARD", "MORTGAGE_PREQUALIFICATION"]
        channels = ["AFFILIATE", "EMAIL", "SOCIAL", "PAID_SEARCH", "WEBSITE"]
        distribution = (
            ["PENDING_ASSIGNMENT"] * 10
            + ["UNDER_REVIEW"] * 35
            + ["CHANGES_REQUESTED"] * 15
            + ["APPROVED"] * 35
            + ["REJECTED"] * 5
        )
        for i in range(records - 7):
            sid = uid(f"campaign-{i}")
            state = distribution[i % 100]
            product, channel = products[i % 3], channels[i % 5]
            owner = submitters[i % len(submitters)]
            reviewer = (
                reviewers[i % len(reviewers)] if state != "PENDING_ASSIGNMENT" else None
            )
            submitted = now - timedelta(hours=rng.randint(1, 90 * 24))
            terminal = state in ("APPROVED", "REJECTED")
            decided = (
                min(submitted + timedelta(hours=rng.randint(4, 120)), now)
                if terminal
                else None
            )
            version = 2 if i % 4 == 0 and reviewer else 1
            partner = (
                f"Synthetic Partner {i % 100:03}" if channel == "AFFILIATE" else None
            )
            disclosure = (
                "Prequalification is not a commitment to lend."
                if product == "MORTGAGE_PREQUALIFICATION"
                else "Subject to credit approval."
            )
            if channel == "AFFILIATE":
                disclosure += " ClearPath may compensate this partner."
            # Vary copy around 0.5–2 KiB; avoid unrealistically tiny/identical payloads.
            words = [
                "flexible",
                "everyday",
                "planning",
                "options",
                "rewards",
                "support",
                "compare",
                "online",
                "clear",
                "financial",
            ]
            copy = (
                f"Synthetic campaign {i:06}. "
                + " ".join(rng.choices(words, k=rng.randint(60, 220)))
                + ". "
                + disclosure
            )
            if state == "CHANGES_REQUESTED":
                copy = "Guaranteed approval. " + copy
            events = [("SUBMITTED", owner, 1, None, "PENDING_ASSIGNMENT", None)]
            if reviewer:
                events.append(
                    (
                        "AUTO_ASSIGNED",
                        owner,
                        1,
                        "PENDING_ASSIGNMENT",
                        "UNDER_REVIEW",
                        None,
                    )
                )
            if version == 2:
                events.extend(
                    [
                        (
                            "CHANGES_REQUESTED",
                            reviewer or reviewers[0],
                            1,
                            "UNDER_REVIEW",
                            "CHANGES_REQUESTED",
                            "Clarify the campaign copy.",
                        ),
                        (
                            "RESUBMITTED",
                            owner,
                            2,
                            "CHANGES_REQUESTED",
                            "UNDER_REVIEW" if reviewer else "PENDING_ASSIGNMENT",
                            None,
                        ),
                    ]
                )
            if state in ("CHANGES_REQUESTED", "APPROVED", "REJECTED"):
                events.append(
                    (
                        state,
                        reviewer,
                        version,
                        "UNDER_REVIEW",
                        state,
                        "Synthetic benchmark decision.",
                    )
                )
            conn.execute(
                """INSERT INTO submissions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid,
                    f"CP-{8909 + i}",
                    f"Synthetic {product.replace('_', ' ').title()} Campaign {i:06}",
                    partner,
                    channel,
                    product,
                    state,
                    reviewer,
                    owner,
                    (now + timedelta(days=rng.randint(1, 30))).date().isoformat(),
                    iso(submitted),
                    iso(submitted + timedelta(hours=72)),
                    iso(decided) if decided else None,
                    version,
                    len(events),
                    iso(submitted),
                    iso(decided or submitted),
                ),
            )
            for v in range(1, version + 1):
                text = (
                    copy if v == version else "Original synthetic draft. " + disclosure
                )
                conn.execute(
                    "INSERT INTO submission_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        uid(f"{i}-v{v}"),
                        sid,
                        v,
                        f"https://example.com/synthetic/{i}",
                        text,
                        owner,
                        iso(submitted),
                    ),
                )
            # Ordered, version-linked synthetic history (not a measured operational record).
            span = (
                (decided or min(now, submitted + timedelta(hours=1))) - submitted
            ).total_seconds()
            for j, (event, actor, v, source, dest, comment) in enumerate(events):
                stamp = submitted + timedelta(
                    seconds=span * j / max(1, len(events) - 1)
                )
                conn.execute(
                    """INSERT INTO audit_events (submission_id,actor_id,event_type,from_status,to_status,version_number,comment,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        sid,
                        actor,
                        event,
                        source,
                        dest,
                        v,
                        comment,
                        json.dumps({"synthetic": True}),
                        iso(stamp),
                    ),
                )
        conn.commit()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ["users", "submissions", "submission_versions", "audit_events"]
        }
        counts["statuses"] = dict(
            conn.execute(
                "SELECT status, COUNT(*) FROM submissions GROUP BY status"
            ).fetchall()
        )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    result = {
        "database": str(path),
        "seed": random_seed,
        "as_of": iso(now),
        "counts": counts,
        "database_bytes": path.stat().st_size,
        "synthetic": True,
    }
    path.with_suffix(".manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--records", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--as-of", help="ISO UTC timestamp for exact fixture reproduction"
    )
    args = parser.parse_args()
    print(
        json.dumps(
            generate(
                args.database,
                args.records,
                args.seed,
                datetime.fromisoformat(args.as_of) if args.as_of else None,
            ),
            indent=2,
        )
    )
