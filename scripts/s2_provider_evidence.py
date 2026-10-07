"""S2 genuine provider smoke test + evidence record (run once).

Loads inference config from the server .env (never printing the key), calls
the OpenAI-compatible chat endpoint through the narrow adapter, runs one
semantic.analyze() on a labeled paraphrase case, and writes attribution
evidence to docs/S2_provider_evidence.md.
"""

from __future__ import annotations

import os

from clearpath import inference as inf
from clearpath import semantic

ENV_PATH = os.path.expanduser(os.environ.get("CLEARPATH_ENV_FILE", ".env"))


def load_env(path: str) -> dict[str, str]:
    vals: dict[str, str] = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            vals[k.strip()] = v.strip()
    return vals


def main() -> None:
    env = load_env(ENV_PATH)
    # S0 contract verifies the runtime-evaluator endpoint and model id:
    # the configured OpenAI-compatible endpoint serving deepseek-v4-flash.
    # The .env MODEL_BASE_URL/SERVED_MODEL_NAME point at the coder model (port
    # 8100) which is NOT the runtime evaluator, so we pin the verified values.
    base_url = os.environ.get("CLEARPATH_INFERENCE_BASE_URL", "http://localhost:8000/v1")
    model_id = "deepseek-v4-flash"
    api_key = env.get("CLEARPATH_INFERENCE_API_KEY") or env.get("VLLM_API_KEY")
    assert api_key, "no inference key present"

    config = inf.ProviderConfig(
        base_url=base_url,
        model_id=model_id,
        api_key=api_key,
        timeout_s=60.0,
        max_tokens=1024,
    )

    rules = [
        {"rule_key": "CLAIM_001", "title": "Restricted approval claim",
         "instructions": "Flag any implied approval guarantee.",
         "kind": "semantic", "product": None, "channel": None, "enabled": 1},
        {"rule_key": "CLAIM_002", "title": "Guaranteed approval claim",
         "instructions": "Flag any guaranteed approval.",
         "kind": "semantic", "product": None, "channel": None, "enabled": 1},
    ]

    copy = (
        "Your yes is already decided — everyone qualifies. "
        "You are pre-approved for ClearRewards."
    )

    inp = semantic.SemanticInput(
        copy_text=copy,
        product="CREDIT_CARD",
        channel="EMAIL",
        rules=rules,
        snapshot_id="snap-demo-1",
        snapshot_hash="d" * 64,
        snapshot_version=1,
    )

    provider = inf.OpenAICompatAdapter(config)
    result = semantic.analyze(inp, provider, config=config)

    lines = [
        "# S2 — Genuine provider evidence (run 2026-10-06)",
        "",
        f"- provider base_url: `{config.base_url}`",
        f"- provider model_id: `{config.model_id}`",
        "- api_key: present, value never printed/logged",
        f"- result status: `{result.status}`",
        f"- failure_code: `{result.failure_code or 'none'}`",
        f"- failure_message: `{result.failure_message or 'none'}`",
        f"- latency_ms: `{result.latency_ms}`",
        f"- token_usage: `{result.token_usage}`",
        f"- provider_revision: `{result.provider_revision}`",
        f"- config_fingerprint: `{result.config_fingerprint}`",
        f"- prompt_version: `{result.prompt_version}`",
        f"- snapshot_version: `{result.snapshot_version}`",
        "",
        "## Findings",
        "",
    ]
    if result.findings:
        for f in result.findings:
            lines.append(
                f"- `{f.finding_id}` severity={f.severity} "
                f"quote={f.evidence_quote!r} start={f.start} end={f.end}\n"
                f"  explanation: {f.explanation}\n"
                f"  suggested_revision: {f.suggested_revision}"
            )
    else:
        lines.append("- (none)")
    lines.append("")
    lines.append("## Copy analyzed (untrusted data)")
    lines.append(f"```\n{copy}\n```")

    out_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "S2_provider_evidence.md"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print(f"wrote {out_path}")
    print(f"status={result.status} latency={result.latency_ms} rev={result.provider_revision}")


if __name__ == "__main__":
    main()
