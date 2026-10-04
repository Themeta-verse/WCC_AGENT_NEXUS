"""Model router and provider abstraction layer for NEXUS.

This module is additive and optional. It provides a configurable, task-oriented
provider and model abstraction layer supporting:
- Ollama / local models
- OpenRouter
- Generic OpenAI-compatible endpoints (vLLM, LMStudio, llama.cpp, LocalAI)
- Mock provider for deterministic offline tests

Core Truth Boundary Invariant:
Every model response represents inferred/untrusted data. The router strictly
enforces that `reality == 'INFERRED'` and `untrusted == True`. Model outputs
can NEVER claim OBSERVED or VERIFIED reality states.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TASK_PLANNING = "planning"
TASK_IMPLEMENTATION = "implementation"
TASK_CODE_REVIEW = "code_review"
TASK_TESTING = "testing"
TASK_SECURITY_REVIEW = "security_review"
TASK_DEBUGGING = "debugging"
TASK_VERIFICATION = "verification"
TASK_RESEARCH = "research"
TASK_ARCHITECTURE = "architecture"
TASK_REPORT = "report"

# Phase 7: unified model contract tasks (agent-facing aliases)
TASK_AGENT_REASONING = "agent_reasoning"

VALID_TASKS = {
    TASK_PLANNING,
    TASK_IMPLEMENTATION,
    TASK_CODE_REVIEW,
    TASK_TESTING,
    TASK_SECURITY_REVIEW,
    TASK_DEBUGGING,
    TASK_VERIFICATION,
    TASK_RESEARCH,
    TASK_ARCHITECTURE,
    TASK_REPORT,
    TASK_AGENT_REASONING,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ModelToolCall:
    """Structured tool request emitted by a model.

    Phase 7 tool-call protocol: the model may REQUEST a bounded tool using
    {"tool": "<capability>", "arguments": {...}}. NEXUS validates the request
    against the agent's capabilities before any execution.
    """
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "arguments": dict(self.arguments), "call_id": self.call_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelToolCall":
        if not isinstance(data, dict):
            raise ValueError("tool call must be a dict")
        tool = data.get("tool") or data.get("capability") or data.get("name")
        if not tool or not isinstance(tool, str):
            raise ValueError("tool call requires a string 'tool' field")
        args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
        if not isinstance(args, dict):
            raise ValueError("tool call 'arguments' must be a dict")
        return cls(tool=tool.strip(), arguments=dict(args), call_id=str(data.get("call_id", "")))


@dataclass
class ToolDefinition:
    """Tool advertised to the model in the bounded context."""
    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters_schema": dict(self.parameters_schema)}


@dataclass
class ModelRequest:
    """Phase 7 structured model interface (request side).

    Agents ask NEXUS for reasoning — they never instantiate provider SDKs.
    The router translates this into provider-specific calls.
    """
    agent_objective: str = ""
    task: str = ""
    system_instructions: str | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    constraints: dict[str, Any] = field(default_factory=dict)
    tool_definitions: list[ToolDefinition] = field(default_factory=list)
    execution_metadata: dict[str, Any] = field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    task_type: str = TASK_AGENT_REASONING
    temperature: float = 0.2
    timeout: int = 30
    max_tokens: int | None = None

    def to_redacted_dict(self) -> dict[str, Any]:
        return {
            "agent_objective": self.agent_objective[:2000],
            "task": self.task[:2000],
            "system_instructions": (self.system_instructions or "")[:2000],
            "artifact_count": len(self.artifacts),
            "message_count": len(self.messages),
            "constraints": _redact_dict(self.constraints),
            "tools": [t.to_dict() for t in self.tool_definitions],
            "execution_metadata": _redact_dict(self.execution_metadata),
            "provider": self.provider,
            "model": self.model,
            "task_type": self.task_type,
        }


@dataclass
class ModelResponse:
    content: str
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    reality: str = "INFERRED"
    untrusted: bool = True
    status: str = "SUCCESS"
    raw_response: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] | None = None
    error: str | None = None
    provenance: list[str] = field(default_factory=list)
    # Phase 7 extensions (all optional, backwards compatible)
    tool_calls: list[ModelToolCall] = field(default_factory=list)
    execution_id: str = ""
    finish_reason: str = "stop"
    attempted_providers: list[str] = field(default_factory=list)
    fallback_used: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Strict NEXUS Truth Boundary Invariant:
        # Model output is ALWAYS inferred and untrusted, regardless of provider claims.
        self.reality = "INFERRED"
        self.untrusted = True
        if not self.provenance:
            self.provenance = [f"model-provider:{self.provider}", f"model:{self.model}"]
        if not self.execution_id:
            import uuid as _uuid
            self.execution_id = f"model-exec-{_uuid.uuid4().hex[:12]}"
        # Normalize tool_calls entries
        normalized: list[ModelToolCall] = []
        for tc in self.tool_calls or []:
            if isinstance(tc, ModelToolCall):
                normalized.append(tc)
            elif isinstance(tc, dict):
                try:
                    normalized.append(ModelToolCall.from_dict(tc))
                except ValueError:
                    continue
        self.tool_calls = normalized
        if not self.usage:
            self.usage = {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            }

    @property
    def text(self) -> str:
        """Alias for reasoning result text."""
        return self.content

    @property
    def structured_output(self) -> dict[str, Any] | None:
        return self.structured

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Never leak provider secrets; raw_response is sanitized
        data["raw_response"] = _redact_dict(self.raw_response)
        data["tool_calls"] = [tc.to_dict() if isinstance(tc, ModelToolCall) else tc for tc in self.tool_calls]
        return data

    def to_redacted_dict(self) -> dict[str, Any]:
        d = self.to_dict()
        d.pop("raw_response", None)
        return d


def _redact_dict(value: Any) -> Any:
    """Recursively redact API keys / secrets from arbitrary structures."""
    import re as _re
    sensitive_keys = {"api_key", "apikey", "authorization", "bearer", "token", "secret", "password", "api-key"}
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            kl = str(k).lower()
            if any(s in kl for s in sensitive_keys):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact_dict(v)
        # Also scrub bearer tokens embedded in strings
        return out
    if isinstance(value, list):
        return [_redact_dict(v) for v in value]
    if isinstance(value, str):
        # Scrub sk-*, Bearer ..., api_key=... patterns
        redacted = _re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]+", r"\1***REDACTED***", value)
        redacted = _re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)(['\"]?)[A-Za-z0-9._\-~+/=]{8,}\2", r"\1\2***REDACTED***\2", redacted)
        if len(redacted) > 8000:
            return redacted[:8000] + "...[truncated]"
        return redacted
    return value


def redact_for_log(payload: dict[str, Any]) -> dict[str, Any]:
    """Public helper: sanitize provider metadata for logs/events/traces."""
    return _redact_dict(payload)


def parse_tool_calls_from_payload(payload: Any) -> list[ModelToolCall]:
    """Parse structured tool-call requests from model output.

    Accepted shapes:
      {"tool_calls": [{"tool": ..., "arguments": {...}}]}
      {"tool": "filesystem.read", "arguments": {...}}
      [{"tool": ..., "arguments": {...}}]
    Malformed entries are skipped by the caller (rejected, never executed).
    """
    calls: list[ModelToolCall] = []
    candidates: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("tool_calls"), list):
            candidates = payload["tool_calls"]
        elif payload.get("tool"):
            candidates = [payload]
        else:
            return []
    elif isinstance(payload, list):
        candidates = payload
    else:
        return []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        try:
            calls.append(ModelToolCall.from_dict(entry))
        except ValueError:
            continue
    return calls


class ModelProviderError(Exception):
    """Exception raised for model provider errors."""
    def __init__(self, provider: str, message: str, status_code: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status_code = status_code


class ModelProvider(ABC):
    """Abstract base provider for LLM inference."""
    name: str = "abstract"

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Check provider connectivity, availability, and active models."""
        ...

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        timeout: int = 30,
        model: str | None = None,
    ) -> ModelResponse:
        """Execute text completion or structured JSON inference."""
        ...

    def generate(self, request: ModelRequest, prompt: str) -> ModelResponse:
        """Phase 7 entry point: generate from a structured ModelRequest.

        Default implementation delegates to complete() and parses tool calls
        from structured output. Providers may override for native tool use.
        """
        schema: dict[str, Any] | None = None
        if request.tool_definitions:
            schema = {"type": "object"}
        resp = self.complete(
            prompt,
            system=request.system_instructions,
            schema=schema,
            temperature=request.temperature,
            timeout=request.timeout,
            model=request.model,
        )
        # Parse tool calls from structured output (never trusted, validated later)
        try:
            payload = resp.structured if isinstance(resp.structured, (dict, list)) else None
            if payload is None and resp.content.strip().startswith(("{", "[")):
                import json as _json
                try:
                    payload = _json.loads(resp.content)
                except Exception:
                    payload = None
            if payload is not None:
                resp.tool_calls = parse_tool_calls_from_payload(payload)
        except Exception:
            resp.tool_calls = []
        return resp


@dataclass
class ModelConfig:
    """Phase 7 unified configuration from environment.

    Supported variables (NEXUS_MODEL_* take precedence; provider-specific
    legacy variables remain supported):
      NEXUS_MODEL_PROVIDER  (openai|anthropic|google|ollama|openrouter|mock|openai-compatible|"")
      NEXUS_MODEL_NAME
      NEXUS_MODEL_API_KEY   (never persisted, never logged)
      NEXUS_MODEL_BASE_URL
      NEXUS_MODEL_FALLBACK_PROVIDER
      NEXUS_MODEL_TIMEOUT_SECONDS
      NEXUS_MODEL_TEMPERATURE
    """
    provider: str = ""
    model: str = ""
    api_key_present: bool = False
    base_url: str = ""
    fallback_provider: str = ""
    timeout_seconds: int = 30
    temperature: float = 0.2

    @classmethod
    def from_env(cls) -> "ModelConfig":
        provider = (os.getenv("NEXUS_MODEL_PROVIDER", "") or "").strip().lower()
        model = (os.getenv("NEXUS_MODEL_NAME", "") or "").strip()
        api_key = os.getenv("NEXUS_MODEL_API_KEY", "") or ""
        base_url = (os.getenv("NEXUS_MODEL_BASE_URL", "") or "").strip()
        fallback = (os.getenv("NEXUS_MODEL_FALLBACK_PROVIDER", "") or "").strip().lower()
        try:
            timeout = int(os.getenv("NEXUS_MODEL_TIMEOUT_SECONDS", "30") or "30")
        except ValueError:
            timeout = 30
        try:
            temperature = float(os.getenv("NEXUS_MODEL_TEMPERATURE", "0.2") or "0.2")
        except ValueError:
            temperature = 0.2
        # Legacy inference: if NEXUS_MODEL_* empty, detect provider-specific config
        if not provider:
            if os.getenv("NEXUS_OPENROUTER_API_KEY"):
                provider = "openrouter"
                model = model or os.getenv("NEXUS_OPENROUTER_MODEL", "")
            elif os.getenv("NEXUS_OPENAI_COMPATIBLE_BASE_URL"):
                provider = "openai-compatible"
            elif os.getenv("OPENAI_API_KEY"):
                provider = "openai"
                model = model or os.getenv("OPENAI_MODEL", "")
            elif os.getenv("ANTHROPIC_API_KEY"):
                provider = "anthropic"
                model = model or os.getenv("ANTHROPIC_MODEL", "")
            elif os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"):
                provider = "google"
                model = model or os.getenv("GOOGLE_MODEL", os.getenv("GEMINI_MODEL", ""))
        if not api_key:
            # Provider-specific keys count as present
            for var in ("NEXUS_OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                        "GOOGLE_API_KEY", "GEMINI_API_KEY", "NEXUS_OPENAI_COMPATIBLE_API_KEY"):
                if os.getenv(var):
                    api_key = "present-via-legacy-env"
                    break
        return cls(
            provider=provider,
            model=model,
            api_key_present=bool(api_key),
            base_url=base_url,
            fallback_provider=fallback,
            timeout_seconds=max(1, min(timeout, 300)),
            temperature=temperature,
        )

    @property
    def is_configured(self) -> bool:
        if not self.provider or self.provider in ("", "none", "disabled"):
            return False
        if self.provider in ("mock", "ollama"):
            return True
        return self.api_key_present

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider or "NOT_CONFIGURED",
            "model": self.model or "default",
            "api_key_present": self.api_key_present,
            "base_url": self.base_url,
            "fallback_provider": self.fallback_provider,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "is_configured": self.is_configured,
        }


class MockModelAdapter(ModelProvider):
    """Deterministic in-memory mock adapter for offline execution, unit tests, and CI."""
    name: str = "mock"

    def __init__(
        self,
        default_response: str = '{"status": "ok"}',
        canned_responses: dict[str, str] | None = None,
        latency: float = 0.0,
        should_fail: bool = False,
        default_model: str = "mock-deterministic-v1",
    ):
        self.default_response = default_response
        self.canned_responses = canned_responses or {}
        self.latency = latency
        self.should_fail = should_fail
        self.default_model = default_model
        self.calls: list[dict[str, Any]] = []

    def health(self) -> dict[str, Any]:
        if self.should_fail:
            return {
                "provider": self.name,
                "status": "UNAVAILABLE",
                "availability": False,
                "error": "forced mock failure",
            }
        return {
            "provider": self.name,
            "status": "AVAILABLE",
            "availability": True,
            "type": "deterministic_mock",
            "offline": True,
            "default_model": self.default_model,
        }

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        timeout: int = 30,
        model: str | None = None,
    ) -> ModelResponse:
        start_time = time.monotonic()
        if self.latency > 0:
            time.sleep(self.latency)
        duration = round(time.monotonic() - start_time, 4)

        target_model = model or self.default_model
        self.calls.append({
            "prompt": prompt,
            "system": system,
            "schema": schema,
            "model": target_model,
            "timestamp": utc_now(),
        })

        if self.should_fail:
            raise ModelProviderError(self.name, "Mock provider forced failure")

        content = self.canned_responses.get(prompt, self.default_response)
        structured = None
        if schema or content.strip().startswith(("{", "[")):
            try:
                structured = json.loads(content)
            except json.JSONDecodeError:
                structured = None

        return ModelResponse(
            content=content,
            model=target_model,
            provider=self.name,
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(content.split()),
            total_tokens=len(prompt.split()) + len(content.split()),
            duration_seconds=duration,
            reality="INFERRED",
            untrusted=True,
            status="SUCCESS",
            structured=structured,
            raw_response={"mock": True},
        )


class OllamaModelAdapter(ModelProvider):
    """Direct HTTP adapter for local Ollama instances (e.g. http://127.0.0.1:11434)."""
    name: str = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        default_model: str = "qwen2.5-coder:latest",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
    ):
        self.base_url = (base_url or os.getenv("NEXUS_OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.default_model = os.getenv("NEXUS_OLLAMA_MODEL", default_model)
        self.timeout = timeout
        self.request_fn = request_fn

    def health(self) -> dict[str, Any]:
        url = f"{self.base_url}/api/tags"
        try:
            if self.request_fn is not None:
                resp_data = self.request_fn("GET", url, timeout=3)
                models = [m.get("name") for m in resp_data.get("models", [])]
                return {
                    "provider": self.name,
                    "status": "AVAILABLE",
                    "availability": True,
                    "base_url": self.base_url,
                    "models": models,
                }
            req = Request(url, headers={"User-Agent": "NEXUS-ModelRouter/1.0"}, method="GET")
            with urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name") for m in data.get("models", [])]
                return {
                    "provider": self.name,
                    "status": "AVAILABLE",
                    "availability": True,
                    "base_url": self.base_url,
                    "models": models,
                }
        except Exception as exc:
            return {
                "provider": self.name,
                "status": "UNAVAILABLE",
                "availability": False,
                "base_url": self.base_url,
                "error": str(exc),
            }

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        timeout: int = 60,
        model: str | None = None,
    ) -> ModelResponse:
        start_time = time.monotonic()
        target_model = model or self.default_model
        url = f"{self.base_url}/api/chat"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if schema:
            payload["format"] = "json"

        try:
            if self.request_fn is not None:
                result = self.request_fn("POST", url, payload=payload, timeout=timeout or self.timeout)
            else:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = Request(
                    url,
                    data=data_bytes,
                    headers={"Content-Type": "application/json", "User-Agent": "NEXUS-ModelRouter/1.0"},
                    method="POST",
                )
                with urlopen(req, timeout=timeout or self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

            content = result.get("message", {}).get("content", "")
            duration = round(time.monotonic() - start_time, 4)
            prompt_tokens = result.get("prompt_eval_count", 0)
            completion_tokens = result.get("eval_count", 0)
            structured = None
            if schema or content.strip().startswith(("{", "[")):
                try:
                    structured = json.loads(content)
                except json.JSONDecodeError:
                    structured = None

            return ModelResponse(
                content=content,
                model=target_model,
                provider=self.name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                duration_seconds=duration,
                reality="INFERRED",
                untrusted=True,
                status="SUCCESS",
                structured=structured,
                raw_response=result,
            )
        except HTTPError as exc:
            raise ModelProviderError(self.name, f"HTTP Error {exc.code}: {exc.read().decode('utf-8', errors='ignore')}", status_code=exc.code) from exc
        except Exception as exc:
            raise ModelProviderError(self.name, f"Connection failed: {exc}") from exc


class GenericOpenAICompatibleAdapter(ModelProvider):
    """Adapter for any OpenAI-compatible API (vLLM, LMStudio, llama.cpp, LocalAI, OpenRouter)."""
    name: str = "openai-compatible"

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str = "default-model",
        provider_name: str = "openai-compatible",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
    ):
        self.base_url = (base_url or os.getenv("NEXUS_OPENAI_COMPATIBLE_BASE_URL", "http://127.0.0.1:8000/v1")).rstrip("/")
        self.api_key = api_key or os.getenv("NEXUS_OPENAI_COMPATIBLE_API_KEY", "EMPTY")
        self.default_model = default_model
        self.name = provider_name
        self.timeout = timeout
        self.request_fn = request_fn

    def health(self) -> dict[str, Any]:
        url = f"{self.base_url}/models"
        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "NEXUS-ModelRouter/1.0"}
        try:
            if self.request_fn is not None:
                resp_data = self.request_fn("GET", url, headers=headers, timeout=3)
                models = [m.get("id") for m in resp_data.get("data", [])]
                return {
                    "provider": self.name,
                    "status": "AVAILABLE",
                    "availability": True,
                    "base_url": self.base_url,
                    "models": models,
                }
            req = Request(url, headers=headers, method="GET")
            with urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("id") for m in data.get("data", [])]
                return {
                    "provider": self.name,
                    "status": "AVAILABLE",
                    "availability": True,
                    "base_url": self.base_url,
                    "models": models,
                }
        except Exception as exc:
            return {
                "provider": self.name,
                "status": "UNAVAILABLE",
                "availability": False,
                "base_url": self.base_url,
                "error": str(exc),
            }

    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        timeout: int = 60,
        model: str | None = None,
    ) -> ModelResponse:
        start_time = time.monotonic()
        target_model = model or self.default_model
        url = f"{self.base_url}/chat/completions"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "temperature": temperature,
        }
        if schema:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "NEXUS-ModelRouter/1.0",
        }

        try:
            if self.request_fn is not None:
                result = self.request_fn("POST", url, payload=payload, headers=headers, timeout=timeout or self.timeout)
            else:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = Request(url, data=data_bytes, headers=headers, method="POST")
                with urlopen(req, timeout=timeout or self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

            choices = result.get("choices", [])
            content = choices[0].get("message", {}).get("content", "") if choices else ""
            duration = round(time.monotonic() - start_time, 4)
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

            structured = None
            if schema or content.strip().startswith(("{", "[")):
                try:
                    structured = json.loads(content)
                except json.JSONDecodeError:
                    structured = None

            return ModelResponse(
                content=content,
                model=target_model,
                provider=self.name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                duration_seconds=duration,
                reality="INFERRED",
                untrusted=True,
                status="SUCCESS",
                structured=structured,
                raw_response=result,
            )
        except HTTPError as exc:
            raise ModelProviderError(self.name, f"HTTP Error {exc.code}: {exc.read().decode('utf-8', errors='ignore')}", status_code=exc.code) from exc
        except Exception as exc:
            raise ModelProviderError(self.name, f"Connection failed: {exc}") from exc


class OpenRouterAdapter(GenericOpenAICompatibleAdapter):
    """Specialized adapter for OpenRouter supporting free and low-cost models."""
    name: str = "openrouter"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "meta-llama/llama-3.2-3b-instruct:free",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
    ):
        key = api_key or os.getenv("NEXUS_OPENROUTER_API_KEY", "")
        super().__init__(
            base_url="https://openrouter.ai/api/v1",
            api_key=key,
            default_model=os.getenv("NEXUS_OPENROUTER_MODEL", default_model),
            provider_name="openrouter",
            timeout=timeout,
            request_fn=request_fn,
        )

    def health(self) -> dict[str, Any]:
        if not self.api_key or self.api_key == "EMPTY":
            return {
                "provider": self.name,
                "status": "UNCONFIGURED",
                "availability": False,
                "reason": "NEXUS_OPENROUTER_API_KEY is not set",
            }
        return super().health()


class OpenAIAdapter(GenericOpenAICompatibleAdapter):
    """Phase 7: first-class OpenAI provider (Chat Completions, no SDK)."""
    name: str = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gpt-4o-mini",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
        base_url: str | None = None,
    ):
        # NEXUS_MODEL_* take precedence over legacy OPENAI_* variables.
        key = (
            api_key
            or os.getenv("NEXUS_MODEL_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", "")
        )
        model = (
            os.getenv("NEXUS_MODEL_NAME", "")
            or default_model
        )
        # When NEXUS_MODEL_PROVIDER=openai, NEXUS_MODEL_NAME selects the model.
        cfg_provider = (os.getenv("NEXUS_MODEL_PROVIDER", "") or "").strip().lower()
        if cfg_provider == "openai" and os.getenv("NEXUS_MODEL_NAME"):
            model = os.getenv("NEXUS_MODEL_NAME", model)
        super().__init__(
            base_url=base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            api_key=key,
            default_model=model,
            provider_name="openai",
            timeout=timeout,
            request_fn=request_fn,
        )

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "provider": self.name,
                "status": "UNCONFIGURED",
                "availability": False,
                "reason": "OPENAI_API_KEY / NEXUS_MODEL_API_KEY is not set",
            }
        return super().health()


class AnthropicAdapter(ModelProvider):
    """Phase 7: Anthropic Messages API adapter (no SDK, injectable transport)."""
    name: str = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "claude-3-5-haiku-latest",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
        base_url: str | None = None,
    ):
        cfg_provider = (os.getenv("NEXUS_MODEL_PROVIDER", "") or "").strip().lower()
        key = api_key or os.getenv("NEXUS_MODEL_API_KEY", "") or os.getenv("ANTHROPIC_API_KEY", "")
        model = os.getenv("NEXUS_MODEL_NAME", "") if cfg_provider == "anthropic" else ""
        self.api_key = key
        self.default_model = model or os.getenv("ANTHROPIC_MODEL", default_model)
        self.base_url = (base_url or "https://api.anthropic.com/v1").rstrip("/")
        self.timeout = timeout
        self.request_fn = request_fn

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "provider": self.name,
                "status": "UNCONFIGURED",
                "availability": False,
                "reason": "ANTHROPIC_API_KEY / NEXUS_MODEL_API_KEY is not set",
            }
        return {
            "provider": self.name,
            "status": "CONFIGURED",
            "availability": True,
            "base_url": self.base_url,
            "default_model": self.default_model,
        }

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=60, model=None) -> ModelResponse:
        import uuid as _uuid
        start_time = time.monotonic()
        target_model = model or self.default_model
        url = f"{self.base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "NEXUS-ModelRouter/1.0",
        }
        payload: dict[str, Any] = {
            "model": target_model,
            "max_tokens": 2000,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        try:
            if self.request_fn is not None:
                result = self.request_fn("POST", url, payload=payload, headers=headers, timeout=timeout or self.timeout)
            else:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = Request(url, data=data_bytes, headers=headers, method="POST")
                with urlopen(req, timeout=timeout or self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            blocks = result.get("content", [])
            content = "".join(b.get("text", "") for b in blocks if isinstance(b, dict)) if blocks else ""
            duration = round(time.monotonic() - start_time, 4)
            usage = result.get("usage", {})
            pt = usage.get("input_tokens", 0)
            ct = usage.get("output_tokens", 0)
            structured = None
            if schema or content.strip().startswith(("{", "[")):
                try:
                    structured = json.loads(content)
                except json.JSONDecodeError:
                    structured = None
            return ModelResponse(
                content=content, model=target_model, provider=self.name,
                prompt_tokens=pt, completion_tokens=ct, total_tokens=pt + ct,
                duration_seconds=duration, reality="INFERRED", untrusted=True,
                status="SUCCESS", structured=structured,
                raw_response={"provider": "anthropic"},
                execution_id=f"model-exec-{_uuid.uuid4().hex[:12]}",
            )
        except HTTPError as exc:
            raise ModelProviderError(self.name, f"HTTP Error {exc.code}", status_code=exc.code) from exc
        except Exception as exc:
            raise ModelProviderError(self.name, f"Connection failed: {exc}") from exc


class GoogleAdapter(ModelProvider):
    """Phase 7: Google Gemini generateContent adapter (no SDK)."""
    name: str = "google"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str = "gemini-2.0-flash",
        timeout: int = 60,
        request_fn: Callable[..., Any] | None = None,
    ):
        cfg_provider = (os.getenv("NEXUS_MODEL_PROVIDER", "") or "").strip().lower()
        key = api_key or os.getenv("NEXUS_MODEL_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
        model = os.getenv("NEXUS_MODEL_NAME", "") if cfg_provider == "google" else ""
        self.api_key = key
        self.default_model = model or os.getenv("GOOGLE_MODEL", os.getenv("GEMINI_MODEL", default_model))
        self.timeout = timeout
        self.request_fn = request_fn

    def health(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "provider": self.name,
                "status": "UNCONFIGURED",
                "availability": False,
                "reason": "GOOGLE_API_KEY / NEXUS_MODEL_API_KEY is not set",
            }
        return {
            "provider": self.name,
            "status": "CONFIGURED",
            "availability": True,
            "default_model": self.default_model,
        }

    def complete(self, prompt, *, system=None, schema=None, temperature=0.2, timeout=60, model=None) -> ModelResponse:
        import uuid as _uuid
        start_time = time.monotonic()
        target_model = model or self.default_model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent"
        full_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": full_prompt}]}],
            "generationConfig": {"temperature": temperature},
        }
        headers = {"Content-Type": "application/json", "User-Agent": "NEXUS-ModelRouter/1.0"}
        try:
            if self.request_fn is not None:
                result = self.request_fn("POST", url, payload=payload, headers=headers, timeout=timeout or self.timeout)
            else:
                data_bytes = json.dumps(payload).encode("utf-8")
                req = Request(f"{url}?key={self.api_key}", data=data_bytes, headers=headers, method="POST")
                with urlopen(req, timeout=timeout or self.timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
            cands = result.get("candidates", [])
            content = ""
            if cands:
                parts = cands[0].get("content", {}).get("parts", [])
                content = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
            duration = round(time.monotonic() - start_time, 4)
            structured = None
            if schema or content.strip().startswith(("{", "[")):
                try:
                    structured = json.loads(content)
                except json.JSONDecodeError:
                    structured = None
            return ModelResponse(
                content=content, model=target_model, provider=self.name,
                prompt_tokens=len(full_prompt.split()), completion_tokens=len(content.split()),
                total_tokens=len(full_prompt.split()) + len(content.split()),
                duration_seconds=duration, reality="INFERRED", untrusted=True,
                status="SUCCESS", structured=structured,
                raw_response={"provider": "google"},
                execution_id=f"model-exec-{_uuid.uuid4().hex[:12]}",
            )
        except HTTPError as exc:
            raise ModelProviderError(self.name, f"HTTP Error {exc.code}", status_code=exc.code) from exc
        except Exception as exc:
            raise ModelProviderError(self.name, f"Connection failed: {exc}") from exc


class ModelRouter:
    """Configurable, task-oriented model router with graceful failover.

    Supports configurable task routing policies, provider priority lists,
    and automatic failover to secondary providers when a candidate is unavailable.
    """

    def __init__(
        self,
        providers: list[ModelProvider] | None = None,
        default_task_policies: dict[str, list[str]] | None = None,
        allow_mock_fallback: bool = True,
    ):
        self.providers: dict[str, ModelProvider] = {}
        self.allow_mock_fallback = allow_mock_fallback

        # Task routing policy: task -> prioritized provider list
        self.task_policies: dict[str, list[str]] = dict(default_task_policies) if default_task_policies else {
            TASK_PLANNING: ["ollama", "openrouter", "openai-compatible", "mock"],
            TASK_IMPLEMENTATION: ["ollama", "openrouter", "openai-compatible", "mock"],
            TASK_TESTING: ["ollama", "openrouter", "openai-compatible", "mock"],
            TASK_CODE_REVIEW: ["openrouter", "ollama", "mock"],
            TASK_SECURITY_REVIEW: ["mock", "ollama"],  # Fast deterministic check preferred
            TASK_DEBUGGING: ["ollama", "openrouter", "mock"],
            TASK_VERIFICATION: ["mock"],  # Verification must be deterministic
        }
        # Phase 7 task defaults
        self.task_policies.setdefault(TASK_RESEARCH, ["ollama", "openrouter", "openai", "anthropic", "google", "mock"])
        self.task_policies.setdefault(TASK_ARCHITECTURE, ["ollama", "openrouter", "openai", "anthropic", "google", "mock"])
        self.task_policies.setdefault(TASK_REPORT, ["ollama", "openrouter", "openai", "anthropic", "google", "mock"])
        self.task_policies.setdefault(TASK_AGENT_REASONING, ["ollama", "openrouter", "openai", "anthropic", "google", "mock"])

        if providers:
            for p in providers:
                self.register_provider(p)
        else:
            self._init_default_providers()

    def _init_default_providers(self) -> None:
        """Initialize standard providers from environment configurations."""
        self.register_provider(MockModelAdapter())
        self.register_provider(OllamaModelAdapter())
        if os.getenv("NEXUS_OPENAI_COMPATIBLE_BASE_URL"):
            self.register_provider(GenericOpenAICompatibleAdapter())
        if os.getenv("NEXUS_OPENROUTER_API_KEY") or (os.getenv("NEXUS_MODEL_PROVIDER", "").lower() == "openrouter"):
            self.register_provider(OpenRouterAdapter(
                api_key=os.getenv("NEXUS_OPENROUTER_API_KEY") or os.getenv("NEXUS_MODEL_API_KEY") or "",
            ))
        # Phase 7 first-class providers (registered always; health reports UNCONFIGURED when no key)
        try:
            self.register_provider(OpenAIAdapter())
        except Exception:
            pass
        try:
            self.register_provider(AnthropicAdapter())
        except Exception:
            pass
        try:
            self.register_provider(GoogleAdapter())
        except Exception:
            pass
        # Unified NEXUS_MODEL_* override: ensure the configured provider exists
        try:
            cfg = ModelConfig.from_env()
            if cfg.provider and cfg.provider not in self.providers:
                if cfg.provider in ("openai-compatible", "vllm", "lmstudio", "localai"):
                    self.register_provider(GenericOpenAICompatibleAdapter(
                        base_url=cfg.base_url or None,
                        provider_name=cfg.provider,
                    ))
        except Exception:
            pass
        # Phase 7 task defaults are initialized in __init__ before providers.

    def register_provider(self, provider: ModelProvider) -> None:
        """Register a new or custom model provider."""
        self.providers[provider.name] = provider

    def unregister_provider(self, provider_name: str) -> None:
        """Unregister an existing provider."""
        self.providers.pop(provider_name, None)

    def get_provider(self, name: str) -> ModelProvider | None:
        return self.providers.get(name)

    def set_task_policy(self, task: str, provider_priorities: list[str]) -> None:
        """Configure the prioritized provider list for a specific task."""
        if task not in VALID_TASKS:
            raise ValueError(f"Unknown task '{task}'. Valid tasks: {sorted(VALID_TASKS)}")
        if not provider_priorities:
            raise ValueError("Provider priorities cannot be empty")
        self.task_policies[task] = list(provider_priorities)

    def get_task_policy(self, task: str) -> list[str]:
        """Get the prioritized provider list for a task."""
        return self.task_policies.get(task, ["mock"])

    def health(self) -> dict[str, Any]:
        """Aggregate health status across all registered providers (keys redacted)."""
        raw = {name: p.health() for name, p in self.providers.items()}
        return {
            "router_status": "READY",
            "registered_providers": list(self.providers.keys()),
            "task_policies": self.task_policies,
            "provider_health": _redact_dict(raw),
        }

    # ---- Phase 7: unified config / status / structured generation ----------

    def model_config(self) -> ModelConfig:
        return ModelConfig.from_env()

    def is_configured(self) -> bool:
        """True when a real (non-mock) provider is configured via environment."""
        cfg = self.model_config()
        if not cfg.is_configured:
            return False
        provider = self.providers.get(cfg.provider)
        if provider is None:
            return False
        try:
            h = provider.health()
            return bool(h.get("availability"))
        except Exception:
            return False

    def execution_mode(self) -> str:
        """Report DETERMINISTIC vs MODEL availability (never claims LLM use)."""
        return "MODEL" if self.is_configured() else "DETERMINISTIC"

    def redacted_status(self) -> dict[str, Any]:
        """Provider/model visibility for UI/traces (never includes secrets)."""
        cfg = self.model_config()
        return {
            "execution_mode": self.execution_mode(),
            "provider": cfg.provider or "NOT_CONFIGURED",
            "model": cfg.model or "default",
            "fallback_provider": cfg.fallback_provider,
            "is_configured": cfg.is_configured,
            "registered_providers": sorted(self.providers.keys()),
        }

    def select_model(self, request: ModelRequest) -> tuple[str, str]:
        """Select (provider_name, model_name) for a structured request."""
        cfg = self.model_config()
        provider_name = request.provider or cfg.provider or None
        model_name = request.model or cfg.model or None
        if provider_name:
            return provider_name, model_name or ""
        # Fall back to task policy ordering
        task = request.task_type if request.task_type in VALID_TASKS else TASK_AGENT_REASONING
        policy = self.get_task_policy(task)
        for name in policy:
            prov = self.providers.get(name)
            if prov is None:
                continue
            try:
                if prov.health().get("availability"):
                    return name, model_name or ""
            except Exception:
                continue
        return "mock", model_name or ""

    def generate(self, request: ModelRequest, prompt: str) -> ModelResponse:
        """Phase 7 structured generation with fallback + failure capture.

        - Selects provider/model from request or NEXUS_MODEL_* config.
        - Tries primary then fallback providers; captures failures.
        - Records execution metadata (sanitized) on the response.
        - Enforces INFERRED/untrusted on every response.
        - Raises ModelProviderError when all candidates fail (caller falls back
          to deterministic execution and records the failure honestly).
        """
        import uuid as _uuid
        execution_id = f"model-exec-{_uuid.uuid4().hex[:12]}"
        cfg = self.model_config()
        task = request.task_type if request.task_type in VALID_TASKS else TASK_AGENT_REASONING

        primary, model_override = self.select_model(request)
        candidates: list[str] = []
        if request.provider:
            candidates.append(request.provider)
        elif primary:
            candidates.append(primary)
        # Config fallback provider
        fallback_name = cfg.fallback_provider or ""
        if fallback_name and fallback_name not in candidates:
            candidates.append(fallback_name)
        # Task policy order (dedup)
        for name in self.get_task_policy(task):
            if name not in candidates:
                candidates.append(name)
        if self.allow_mock_fallback:
            for name, prov in self.providers.items():
                if isinstance(prov, MockModelAdapter) and name not in candidates:
                    candidates.append(name)

        attempted: list[str] = []
        errors: list[str] = []
        # Only attempt mock providers when nothing real is configured OR when
        # explicitly requested — preserves deterministic behavior otherwise.
        real_configured = self.is_configured()
        for provider_name in candidates:
            provider = self.providers.get(provider_name)
            if not provider:
                errors.append(f"{provider_name}: provider not registered")
                continue
            is_mock = isinstance(provider, MockModelAdapter)
            if is_mock and real_configured and provider_name != request.provider and task == TASK_VERIFICATION:
                # Verification stays deterministic; mock is fine there.
                pass
            elif is_mock and real_configured and request.provider not in (None, "", "mock") and provider_name not in (request.provider,):
                # Skip implicit mock fallback when a real provider was explicitly requested
                # and failed — caller must handle failure honestly, not silently mock.
                if request.provider:
                    errors.append(f"{provider_name}: skipped implicit mock fallback (real provider requested)")
                    continue
            try:
                h = provider.health()
            except Exception as exc:
                errors.append(f"{provider_name}: health check failed: {exc}")
                continue
            if not h.get("availability") and not is_mock:
                errors.append(f"{provider_name}: reported unavailable")
                continue
            attempted.append(provider_name)
            try:
                eff_request = ModelRequest(
                    agent_objective=request.agent_objective,
                    task=request.task,
                    system_instructions=request.system_instructions,
                    artifacts=request.artifacts,
                    messages=request.messages,
                    constraints=request.constraints,
                    tool_definitions=request.tool_definitions,
                    execution_metadata=request.execution_metadata,
                    provider=provider_name,
                    model=request.model or model_override or cfg.model or None,
                    task_type=task,
                    temperature=request.temperature,
                    timeout=request.timeout or cfg.timeout_seconds,
                    max_tokens=request.max_tokens,
                )
                resp = provider.generate(eff_request, prompt)
                resp.reality = "INFERRED"
                resp.untrusted = True
                resp.execution_id = execution_id
                resp.attempted_providers = list(attempted)
                resp.fallback_used = len(attempted) > 1
                if f"task:{task}" not in resp.provenance:
                    resp.provenance.append(f"task:{task}")
                # Sanitized execution metadata (never raw keys)
                resp.usage = _redact_dict(resp.usage)
                resp.raw_response = _redact_dict(resp.raw_response)
                return resp
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                continue

        raise ModelProviderError(
            "router",
            f"All provider candidates failed for task '{task}'. Errors: {'; '.join(errors)}",
        )

    def select_provider(self, task: str, preferred_provider: str | None = None) -> ModelProvider:
        """Select the highest-priority available provider for a task."""
        if preferred_provider:
            candidates = [preferred_provider]
        else:
            candidates = self.get_task_policy(task)

        for name in candidates:
            provider = self.providers.get(name)
            if not provider:
                continue
            h = provider.health()
            if h.get("availability"):
                return provider

        # If no preferred candidate is available and mock fallback is allowed
        if self.allow_mock_fallback and "mock" in self.providers:
            return self.providers["mock"]

        raise ModelProviderError(
            "router",
            f"No available model provider for task '{task}' among candidates: {candidates}",
        )

    def complete(
        self,
        task: str,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        timeout: int = 30,
        model: str | None = None,
        preferred_provider: str | None = None,
    ) -> ModelResponse:
        """Execute task completion with automatic failover across candidates.

        Enforces the NEXUS Truth Boundary:
        Resulting ModelResponse is guaranteed to have reality='INFERRED' and untrusted=True.
        """
        if task not in VALID_TASKS:
            raise ValueError(f"Invalid task '{task}'. Valid tasks are: {sorted(VALID_TASKS)}")

        candidates = [preferred_provider] if preferred_provider else list(self.get_task_policy(task))
        if self.allow_mock_fallback:
            for name, prov in self.providers.items():
                if isinstance(prov, MockModelAdapter) and name not in candidates:
                    candidates.append(name)

        errors: list[str] = []
        for provider_name in candidates:
            provider = self.providers.get(provider_name)
            if not provider:
                errors.append(f"{provider_name}: provider not registered")
                continue

            h = provider.health()
            # If provider is explicitly unavailable and not mock, skip to next candidate
            if not h.get("availability") and not isinstance(provider, MockModelAdapter):
                errors.append(f"{provider_name}: reported unavailable")
                continue

            try:
                response = provider.complete(
                    prompt,
                    system=system,
                    schema=schema,
                    temperature=temperature,
                    timeout=timeout,
                    model=model,
                )
                # Enforce NEXUS Truth Boundary Invariants unconditionally
                response.reality = "INFERRED"
                response.untrusted = True
                response.provenance.append(f"task:{task}")
                return response
            except Exception as exc:
                errors.append(f"{provider_name}: {exc}")
                continue

        # If all candidates fail, raise explicit fail-closed error
        raise ModelProviderError(
            "router",
            f"All provider candidates failed for task '{task}'. Errors: {'; '.join(errors)}",
        )
