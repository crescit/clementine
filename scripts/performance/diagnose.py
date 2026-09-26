"""Count actual SQL statements for API reads; diagnostic timings are not benchmarks."""

import argparse
from collections import Counter
import json
import os
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from clearpath import db
from clearpath.api import create_app


def diagnose(database):
    statements = []
    original = db.connect

    def traced(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_trace_callback(statements.append)
        return conn

    results = {}
    with (
        patch.dict(
            os.environ, {"DATABASE_PATH": str(database.resolve()), "DEMO_MODE": "false"}
        ),
        patch.object(db, "connect", traced),
    ):
        with TestClient(create_app()) as client:
            users = client.get("/api/users").json()["users"]
            actor = next(u["id"] for u in users if u["name"] == "Sarah T.")
            for route in [
                "/api/submissions?limit=50&offset=0",
                "/api/metrics",
                "/api/submissions?search=Campaign%200009&limit=50",
            ]:
                statements.clear()
                response = client.get(route, headers={"X-Demo-User-Id": actor})
                selections = [
                    s for s in statements if s.lstrip().upper().startswith("SELECT")
                ]
                payload = response.json()
                results[route] = {
                    "http_status": response.status_code,
                    "select_statements": len(selections),
                    "response_bytes": len(response.content),
                    "returned_queue_rows": len(payload.get("submissions", [])),
                    "reported_total": payload.get("total"),
                    "limit": payload.get("limit"),
                    "offset": payload.get("offset"),
                    "sql_examples": list(
                        dict.fromkeys(s.split("WHERE")[0].strip() for s in selections)
                    ),
                }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    result = diagnose(args.database)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
