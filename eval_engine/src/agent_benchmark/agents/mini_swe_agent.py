from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import yaml

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError
from agent_benchmark.harnesses.anthropic_fallback import FALLBACKS_ENV, LEDGER_ENV
from agent_benchmark.harnesses.openai_fallback import (
    FALLBACKS_ENV as OPENAI_FALLBACKS_ENV,
)
from agent_benchmark.harnesses.openai_fallback import (
    LEDGER_ENV as OPENAI_LEDGER_ENV,
)


class MiniSweAgentAdapter(AgentAdapter):
    name = "mini-swe-agent"

    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> AgentInvocation:
        try:
            actual_version = version("mini-swe-agent")
        except PackageNotFoundError as error:
            raise StageError("mini-swe-agent is not installed") from error
        if actual_version != spec.model.subject_agent_version:
            raise StageError(
                "mini-swe-agent version mismatch: "
                f"expected {spec.model.subject_agent_version}, got {actual_version}"
            )

        config_path = run_dir / "subject-config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump(spec.model.config, sort_keys=False))
        environment = {spec.model.api_key_env: api_key}
        process_environment = {
            spec.model.api_key_env: api_key,
            "MSWEA_GLOBAL_COST_LIMIT": str(spec.budget.per_task_usd),
        }
        # Provider fallbacks travel in the environment of the runner subprocess, where
        # mini_swe_agent_bootstrap installs them. The ledger is the only record that a fallback
        # fired: a refusal and a fallback-served answer are both HTTP 200.
        if spec.model.anthropic_fallbacks:
            process_environment[FALLBACKS_ENV] = spec.model.anthropic_fallbacks
            process_environment[LEDGER_ENV] = str(run_dir / "logs" / "anthropic_fallback.jsonl")
        if spec.model.openai_fallbacks:
            process_environment[OPENAI_FALLBACKS_ENV] = spec.model.openai_fallbacks
            process_environment[OPENAI_LEDGER_ENV] = str(run_dir / "logs" / "openai_fallback.jsonl")
        if spec.model.api == "openrouter":
            environment["MSWEA_API_KEY"] = api_key
            process_environment["MSWEA_API_KEY"] = api_key
        return AgentInvocation(
            model_name=spec.model.model_id,
            kwargs={
                "config_file": str(config_path),
                "version": spec.model.subject_agent_version,
                "cost_limit": spec.budget.per_task_usd,
            },
            environment=environment,
            process_environment=process_environment,
        )


ADAPTER = MiniSweAgentAdapter()
