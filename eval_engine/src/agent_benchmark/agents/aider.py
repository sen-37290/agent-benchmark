from __future__ import annotations

from pathlib import Path

import yaml

from agent_benchmark.agents.base import AgentAdapter, AgentInvocation
from agent_benchmark.config.schema import ResolvedSpec
from agent_benchmark.exceptions import StageError

AIDER_VERSION = "0.86.0"


class AiderAdapter(AgentAdapter):
    name = "aider"

    def invocation(
        self,
        spec: ResolvedSpec,
        run_dir: Path,
        api_key: str,
    ) -> AgentInvocation:
        if spec.model.subject_agent_version != AIDER_VERSION:
            raise StageError(
                f"Aider version mismatch: expected {AIDER_VERSION}, "
                f"got {spec.model.subject_agent_version}"
            )
        model_name = spec.model.model_id
        if spec.model.api == "openrouter":
            model_name = f"openrouter/{model_name}"

        model_setting: dict[str, object] = {
            "name": model_name,
            "edit_format": str(spec.benchmark.settings["edit_format"]),
            "accepts_settings": ["reasoning_effort"],
        }
        provider = spec.model.config.get("model", {}).get("model_kwargs", {}).get("provider")
        if spec.model.api == "openrouter" and isinstance(provider, dict):
            model_setting["extra_params"] = {"extra_body": {"provider": provider}}

        native_dir = run_dir / "artifacts" / "aider_native"
        native_dir.mkdir(parents=True, exist_ok=True)
        settings_path = native_dir / "model-settings.yml"
        settings_path.write_text(yaml.safe_dump([model_setting], sort_keys=False))
        environment = {spec.model.api_key_env: api_key}
        return AgentInvocation(
            model_name=model_name,
            kwargs={
                "edit_format": spec.benchmark.settings["edit_format"],
                "reasoning_effort": spec.model.reasoning_effort,
                "model_settings": str(settings_path),
            },
            environment=environment,
            process_environment=environment,
        )


ADAPTER = AiderAdapter()
