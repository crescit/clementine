"""The .env loader fills missing variables and never overrides real env."""

from __future__ import annotations

from pathlib import Path

from clearpath import envfile, inference


def test_parse_handles_comments_export_and_quotes() -> None:
    text = "# c\n\nexport A=1\nB='two'\nC=\"three\"\nbad line\n=x\nD=a=b\n"
    assert envfile.parse_env_text(text) == {"A": "1", "B": "two", "C": "three", "D": "a=b"}


def test_load_sets_missing_and_keeps_existing(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("CLEARPATH_INFERENCE_API_KEY=from-file\nCLEARPATH_INFERENCE_MODEL=file-model\n")
    env = {"CLEARPATH_INFERENCE_MODEL": "real-model"}
    loaded = envfile.load_dotenv(f, env)
    assert loaded == ["CLEARPATH_INFERENCE_API_KEY"]
    assert env["CLEARPATH_INFERENCE_MODEL"] == "real-model"
    cfg = inference.config_from_env(env)
    assert cfg.api_key == "from-file"
    assert cfg.model_id == "real-model"


def test_load_disabled_or_missing_file(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("X=1\n")
    assert envfile.load_dotenv(f, {"CLEARPATH_DOTENV": "0"}) == []
    assert envfile.load_dotenv(tmp_path / "nope.env", {}) == []


def test_env_file_path_from_variable(tmp_path: Path) -> None:
    f = tmp_path / "custom.env"
    f.write_text("Y=2\n")
    env = {"CLEARPATH_ENV_FILE": str(f)}
    assert envfile.load_dotenv(environ=env) == ["Y"]
    assert env["Y"] == "2"
