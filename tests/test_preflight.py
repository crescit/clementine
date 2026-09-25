"""Preflight tests — populated in K4."""

from clearpath.preflight import POLICY_VERSION


def test_policy_version_frozen() -> None:
    assert POLICY_VERSION == "clearpath-demo-v1"
