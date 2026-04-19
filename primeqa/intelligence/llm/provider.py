"""Provider layer \u2014 wraps Anthropic SDK with backoff, timeout, and
consistent usage extraction.

All calls to `client.messages.create(...)` in the codebase should go
through `invoke()` in this module. Direct SDK calls bypass the usage
log, the rate limiter, and the retry policy.

Retry policy (final, from architect discussion):

  RETRY   429 (rate limited)   up to 3, exponential 1s/2s/4s + jitter
  RETRY   529 (overloaded)     up to 3, exponential
  RETRY   timeout              once, same timeout
  RETRY   connection error     once

  NEVER   400 (content error)  user needs to fix
  NEVER   401/403 (auth)       ops issue
  NEVER   404                  shouldn't happen
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ---- Response + error types ----------------------------------------------

@dataclass
class ProviderResponse:
    """Normalized view of an Anthropic response. Safe to serialize for
    the usage log; does not carry the full prompt or full response text
    (the caller has those separately)."""
    content: Any                          # parsed block list or string
    raw_text: str                         # concatenated text content
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0          # from usage.cache_read_input_tokens
    cache_write_tokens: int = 0           # from usage.cache_creation_input_tokens
    latency_ms: int = 0
    request_id: Optional[str] = None
    stop_reason: Optional[str] = None
    # Phase 5: when the response used Anthropic tool_use, this holds the
    # FIRST tool_use block's .input dict (the parsed structured output).
    # None when the response was a plain text reply.
    tool_input: Optional[Any] = None
    tool_name: Optional[str] = None


class ProviderError(Exception):
    """Wraps provider-layer failures with enough context for the usage log."""

    def __init__(self, status: str, message: str,
                 latency_ms: int = 0, request_id: Optional[str] = None):
        super().__init__(message)
        self.status = status  # e.g. 'rate_limited', 'timeout', 'auth_error'
        self.message = message
        self.latency_ms = latency_ms
        self.request_id = request_id


# ---- Retry policy --------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 529}
_MAX_RETRIES_RATE_LIMIT = 3
_MAX_RETRIES_TIMEOUT = 1
_MAX_RETRIES_NETWORK = 1
# Exponential backoff base delay; actual delay is base * 2^attempt +- 25% jitter.
_BACKOFF_BASE_SEC = 1.0
_JITTER_PCT = 0.25


def _classify_exception(e: Exception) -> str:
    """Map an SDK exception to our canonical status string."""
    # Lazy-import anthropic so the provider module stays importable for tests.
    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return "provider_error"

    if isinstance(e, anthropic.RateLimitError):
        return "rate_limited"
    if isinstance(e, anthropic.APIStatusError):
        code = getattr(e, "status_code", 0)
        if code in _RETRYABLE_STATUS_CODES:
            return "overloaded" if code == 529 else "rate_limited"
        if code in (401, 403):
            return "auth_error"
        if code == 400:
            # Credit-exhausted shows up as 400 with a specific body.
            msg = str(e).lower()
            if "credit balance" in msg:
                return "quota_exceeded"
            return "content_error"
        return "provider_error"
    if isinstance(e, anthropic.APITimeoutError):
        return "timeout"
    if isinstance(e, anthropic.APIConnectionError):
        return "network"
    return "provider_error"


def _sleep_with_backoff(attempt: int) -> None:
    delay = _BACKOFF_BASE_SEC * (2 ** attempt)
    jitter = delay * _JITTER_PCT * (2 * random.random() - 1)
    time.sleep(max(0.1, delay + jitter))


# ---- Main invoke entry point ---------------------------------------------

def invoke(
    *,
    api_key: str,
    model: str,
    messages: List[Dict[str, Any]],
    max_tokens: int,
    system: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: Optional[Dict[str, Any]] = None,
    timeout: float = 90.0,
    metadata: Optional[Dict[str, Any]] = None,
) -> ProviderResponse:
    """Single Anthropic call with backoff + usage extraction.

    `system` and `messages` may contain content blocks with cache_control;
    the provider passes them through unchanged.
    """
    import anthropic

    client = anthropic.Anthropic(api_key=api_key, timeout=timeout)

    rate_limit_attempts = 0
    timeout_attempts = 0
    network_attempts = 0
    started = time.time()

    while True:
        try:
            kwargs: Dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            if tools:
                kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = tool_choice
            if metadata:
                kwargs["metadata"] = metadata

            resp = client.messages.create(**kwargs)

            latency_ms = int((time.time() - started) * 1000)

            # Extract text blocks and (when tool use was requested) the
            # first tool_use block's parsed input.
            raw_text_parts = []
            tool_input = None
            tool_name = None
            for block in getattr(resp, "content", []) or []:
                btype = getattr(block, "type", None)
                if btype == "text":
                    raw_text_parts.append(getattr(block, "text", ""))
                elif btype == "tool_use" and tool_input is None:
                    tool_input = getattr(block, "input", None)
                    tool_name = getattr(block, "name", None)
            raw_text = "".join(raw_text_parts)

            usage = getattr(resp, "usage", None)
            return ProviderResponse(
                content=list(getattr(resp, "content", []) or []),
                raw_text=raw_text,
                model=getattr(resp, "model", model),
                input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
                cached_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                cache_write_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
                latency_ms=latency_ms,
                request_id=getattr(resp, "id", None),
                stop_reason=getattr(resp, "stop_reason", None),
                tool_input=tool_input,
                tool_name=tool_name,
            )

        except Exception as e:
            status = _classify_exception(e)
            latency_ms = int((time.time() - started) * 1000)
            request_id = getattr(e, "request_id", None)
            msg = str(e)

            if status == "rate_limited":
                if rate_limit_attempts < _MAX_RETRIES_RATE_LIMIT:
                    _sleep_with_backoff(rate_limit_attempts)
                    rate_limit_attempts += 1
                    continue
            elif status == "overloaded":
                if rate_limit_attempts < _MAX_RETRIES_RATE_LIMIT:
                    _sleep_with_backoff(rate_limit_attempts)
                    rate_limit_attempts += 1
                    continue
            elif status == "timeout":
                if timeout_attempts < _MAX_RETRIES_TIMEOUT:
                    timeout_attempts += 1
                    continue
            elif status == "network":
                if network_attempts < _MAX_RETRIES_NETWORK:
                    network_attempts += 1
                    continue

            raise ProviderError(status=status, message=msg,
                                latency_ms=latency_ms,
                                request_id=request_id) from e
