"""Central customer/agent binding for this deployment."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerAgentConfig:
    customer_key: str
    customer_name: str
    elevenlabs_agent_id: str
    timezone: str
    # Kept for backward compatibility; monitoring currently displays raw API cost values.
    cost_suffix: str


CUSTOMER_AGENT_CONFIG = CustomerAgentConfig(
    customer_key="dormero",
    customer_name="Dormero Hotel Group",
    elevenlabs_agent_id="agent_5801kjf7770wf1xvdt4rrj1gan0h",
    timezone="Europe/Berlin",
    cost_suffix="€",
)


def assert_customer_agent_config() -> None:
    configured_agent_id = CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id.strip()
    if not configured_agent_id or configured_agent_id == "replace-with-agent-id":
        raise RuntimeError(
            "Invalid customer agent configuration. "
            "Set CUSTOMER_AGENT_CONFIG.elevenlabs_agent_id in backend/app/config/customer_agent.py."
        )
