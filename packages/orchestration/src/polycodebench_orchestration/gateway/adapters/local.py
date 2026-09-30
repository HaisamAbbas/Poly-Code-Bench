"""Local inference endpoints (llama.cpp, vLLM, Ollama ``/v1`` and similar).

They speak the OpenAI-compatible wire format but are a distinct adapter: registration must be
explicit and internal (see ``endpoint_policy.required_policy_kind``), authentication is
optional, and approval needs a passing conformance report because "compatible" servers differ
in tool calling, seed handling and usage reporting.
"""

from __future__ import annotations

from polycodebench_core.model_contracts import ProviderKind

from polycodebench_orchestration.gateway.adapters.openai_compatible import OpenAICompatibleAdapter


class LocalEndpointAdapter(OpenAICompatibleAdapter):
    provider_kind = ProviderKind.LOCAL
    requires_authentication = False
