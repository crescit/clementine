"""Minimal .env loader (stdlib only).

Loads KEY=VALUE lines from the file named by CLEARPATH_ENV_FILE (default
``.env`` in the working directory) into ``os.environ``. Variables already set
in the real environment always win, so deployments can override anything.
Set CLEARPATH_DOTENV=0 to disable (the test suite does this).
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path


def parse_env_text(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key or not key.replace("_", "").isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_dotenv(
    path: str | os.PathLike[str] | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> list[str]:
    """Load the env file without overriding existing variables.

    Returns the names of variables that were set (never their values).
    """
    env = os.environ if environ is None else environ
    if env.get("CLEARPATH_DOTENV", "1").lower() in {"0", "false", "no"}:
        return []
    target = Path(path or env.get("CLEARPATH_ENV_FILE", ".env")).expanduser()
    if not target.is_file():
        return []
    loaded = []
    for key, value in parse_env_text(target.read_text(encoding="utf-8")).items():
        if key not in env:
            env[key] = value
            loaded.append(key)
    return loaded
