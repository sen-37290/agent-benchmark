#!/usr/bin/env python3
"""Make one Responses-API call and require the provider to echo the requested effort."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import time
from pathlib import Path
from typing import Any

import litellm

from agent_benchmark.config.loader import model_profile
from agent_benchmark.run.retry import is_transient, retry_delay


def _dump(response: object) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        value = response.model_dump()  # type: ignore[attr-defined]
    elif isinstance(response, dict):
        value = response
    else:
        value = {}
    return value if isinstance(value, dict) else {}


def _observed_effort(response: object) -> str | None:
    reasoning = _dump(response).get("reasoning")
    if isinstance(reasoning, dict):
        value = reasoning.get("effort")
        return str(value) if value is not None else None
    value = getattr(reasoning, "effort", None)
    return str(value) if value is not None else None


def _write_log(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    attempts: list[object] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text())
            if isinstance(existing, dict) and isinstance(existing.get("attempts"), list):
                attempts = existing["attempts"]
        except (OSError, json.JSONDecodeError):
            pass
    attempts.append(payload)
    path.write_text(json.dumps({"attempts": attempts}, indent=2, sort_keys=True) + "\n")


def _request_model(profile_name: str) -> str:
    """Resolve a CLI model profile to the LiteLLM name used by the subject agent."""
    profile = model_profile(profile_name)
    config = profile.get("config")
    model_config = config.get("model") if isinstance(config, dict) else None
    value = model_config.get("model_name") if isinstance(model_config, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"model profile {profile_name!r} has no config.model.model_name request target"
        )
    return value.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", required=True)
    parser.add_argument("--expected", required=True)
    parser.add_argument("--required-litellm-version", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--retries", type=int, default=3)
    args = parser.parse_args()

    installed = importlib.metadata.version("litellm")
    if installed != args.required_litellm_version:
        parser.error(
            f"LiteLLM {installed} installed; {args.required_litellm_version} is required"
        )
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        parser.error(f"{args.api_key_env} is not set")
    if args.retries < 0:
        parser.error("--retries must be non-negative")

    try:
        model = _request_model(args.model)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    attempts = args.retries + 1
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            response = litellm.responses(
                model=model,
                input="Return only the word OK.",
                reasoning_effort=args.expected,
                max_output_tokens=4096,
                api_key=api_key,
                timeout=1800,
            )
        except Exception as error:  # noqa: BLE001 - classify provider exceptions by type/name
            retryable = is_transient(type(error).__name__, str(error))
            _write_log(
                args.log,
                {
                    "model_profile": args.model,
                    "model": model,
                    "litellm_version": installed,
                    "expected_effort": args.expected,
                    "attempt": attempt,
                    "outcome": "error",
                    "error_type": type(error).__name__,
                    "retryable": retryable,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                },
            )
            if retryable and attempt < attempts:
                time.sleep(retry_delay(attempt))
                continue
            print(
                f"FATAL: Responses effort preflight failed with {type(error).__name__} "
                f"after {attempt} attempt(s); benchmark run not created",
                flush=True,
            )
            return 1

        observed = _observed_effort(response)
        _write_log(
            args.log,
            {
                "model_profile": args.model,
                "model": model,
                "litellm_version": installed,
                "expected_effort": args.expected,
                "observed_effort": observed,
                "attempt": attempt,
                "outcome": "match" if observed == args.expected else "mismatch",
                "response_id": _dump(response).get("id"),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            },
        )
        if observed != args.expected:
            print(
                f"FATAL: requested reasoning effort {args.expected!r}, provider echoed "
                f"{observed!r}; benchmark run not created",
                flush=True,
            )
            return 1
        print(f"verified Responses effort {observed} on {model} with LiteLLM {installed}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
