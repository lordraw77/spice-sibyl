"""
Provider registry — the single declarative source for provider metadata and routing.

Each provider is described by a ProviderDescriptor. Adding a provider means
registering one descriptor here: model routing (provider_factory), the
/v1/providers metadata (key hints, docs links) and connectivity test models
are all derived from this table.

Routing: the first path segment of a model id ("groq/llama-3.3" → "groq") is
looked up in PROVIDERS; unknown prefixes fall back to LiteLLMProvider, which
handles any LiteLLM-supported provider string natively.

Discovery: ``discover`` points to the adapter coroutine in
app.services.model_discovery; providers whose models are intrinsic rather
than listed by an external API (mock, agent when the sidecar has no /models)
declare them via ``static_models``.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.providers.base import BaseProvider
from app.providers.cerebras_provider import CerebrasProvider
from app.providers.cloudflare_provider import CloudflareProvider
from app.providers.gemini_provider import GeminiProvider
from app.providers.litellm_provider import LiteLLMProvider
from app.providers.mistral_provider import MistralProvider
from app.providers.mock_provider import MockProvider
from app.providers.nvidia_provider import NvidiaProvider
from app.providers.openrouter_provider import OpenRouterProvider
from app.providers.orchestrator_provider import OrchestratorProvider
from app.services import model_discovery


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    label: str
    provider_cls: type[BaseProvider]
    key_hint: str | None = None      # env var name shown in the UI, None = keyless
    docs_url: str | None = None
    test_model: str | None = None    # model used by POST /providers/{id}/test
    # Coroutine fetching the live model catalog (app.services.model_discovery)
    discover: Callable[[], Awaitable[list[dict]]] | None = None
    # Intrinsic models for self-described providers; also the fallback when
    # discover() cannot reach its source (e.g. agent sidecar without /models)
    static_models: tuple[dict, ...] = field(default=())
    # Initial enabled state, overridable at runtime via PATCH /providers/{id}
    enabled_by_default: bool = True


PROVIDERS: dict[str, ProviderDescriptor] = {
    d.id: d
    for d in (
        ProviderDescriptor(
            id='mock', label='Mock', provider_cls=MockProvider,
            enabled_by_default=False,
            static_models=(
                {'id': 'mock/spice-sibyl-1', 'name': 'spice-sibyl-1',
                 'label': 'Mock · Spice Sibyl 1', 'free': True, 'capabilities': ['chat']},
            ),
        ),
        ProviderDescriptor(
            id='agent', label='Agent (Multi-MCP)', provider_cls=OrchestratorProvider,
            discover=model_discovery.discover_agent,
            static_models=(
                {'id': 'agent/multi-mcp', 'name': 'multi-mcp',
                 'label': 'Agent · Multi-MCP Orchestrator', 'free': True,
                 'capabilities': ['chat', 'tools', 'agent']},
            ),
        ),
        ProviderDescriptor(
            id='ollama', label='Ollama', provider_cls=LiteLLMProvider,
            docs_url='https://ollama.com',
            discover=model_discovery.discover_ollama,
        ),
        ProviderDescriptor(
            id='groq', label='Groq', provider_cls=LiteLLMProvider,
            key_hint='GROQ_API_KEY', docs_url='https://console.groq.com',
            test_model='groq/llama-3.1-8b-instant',
            discover=model_discovery.discover_groq,
        ),
        ProviderDescriptor(
            id='openrouter', label='OpenRouter', provider_cls=OpenRouterProvider,
            key_hint='OPENROUTER_API_KEY', docs_url='https://openrouter.ai/keys',
            test_model='openrouter/openai/gpt-4o-mini',
            discover=model_discovery.discover_openrouter,
        ),
        ProviderDescriptor(
            id='gemini', label='Gemini', provider_cls=GeminiProvider,
            key_hint='GEMINI_API_KEY', docs_url='https://aistudio.google.com',
            test_model='gemini/gemini-1.5-flash-latest',
            discover=model_discovery.discover_gemini,
        ),
        ProviderDescriptor(
            id='cloudflare', label='Cloudflare', provider_cls=CloudflareProvider,
            key_hint='CLOUDFLARE_API_KEY', docs_url='https://dash.cloudflare.com',
            discover=model_discovery.discover_cloudflare,
        ),
        ProviderDescriptor(
            id='mistral', label='Mistral', provider_cls=MistralProvider,
            key_hint='MISTRAL_API_KEY', docs_url='https://console.mistral.ai',
            test_model='mistral/mistral-small-latest',
            discover=model_discovery.discover_mistral,
        ),
        ProviderDescriptor(
            id='cerebras', label='Cerebras', provider_cls=CerebrasProvider,
            key_hint='CEREBRAS_API_KEY', docs_url='https://cloud.cerebras.ai',
            test_model='cerebras/llama3.1-8b',
            discover=model_discovery.discover_cerebras,
        ),
        ProviderDescriptor(
            id='nvidia', label='Nvidia', provider_cls=NvidiaProvider,
            key_hint='NVIDIA_API_KEY', docs_url='https://build.nvidia.com',
            test_model='nvidia/meta/llama-3.1-8b-instruct',
            discover=model_discovery.discover_nvidia,
        ),
        ProviderDescriptor(
            id='openai', label='OpenAI', provider_cls=LiteLLMProvider,
            key_hint='OPENAI_API_KEY', docs_url='https://platform.openai.com/api-keys',
            test_model='openai/gpt-4o-mini',
        ),
        ProviderDescriptor(
            id='together_ai', label='Together AI', provider_cls=LiteLLMProvider,
            key_hint='TOGETHER_API_KEY', docs_url='https://api.together.xyz',
        ),
        ProviderDescriptor(
            id='fireworks_ai', label='Fireworks AI', provider_cls=LiteLLMProvider,
            key_hint='FIREWORKS_API_KEY', docs_url='https://fireworks.ai',
        ),
        ProviderDescriptor(
            id='huggingface', label='Huggingface', provider_cls=LiteLLMProvider,
            key_hint='HF_TOKEN', docs_url='https://huggingface.co/settings/tokens',
        ),
    )
}


def get_descriptor(provider_id: str) -> ProviderDescriptor | None:
    return PROVIDERS.get(provider_id)


def resolve_provider(model: str | None = None) -> BaseProvider:
    """Instantiate the provider that handles the given model identifier."""
    if model and '/' in model:
        descriptor = PROVIDERS.get(model.split('/', 1)[0])
        if descriptor is not None:
            return descriptor.provider_cls()
    return LiteLLMProvider()
