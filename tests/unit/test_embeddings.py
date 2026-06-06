"""Unit tests for primeqa.intelligence.embeddings — the Voyage AI
embedding client.

HTTP is mocked with httpx.MockTransport — no test ever hits the real
Voyage API. embed_batch() builds its own httpx.Client internally, so
the tests patch `embeddings.httpx.Client` to return a client wired to
a MockTransport handler.
"""
from __future__ import annotations

from unittest import mock

import httpx
import pytest

from primeqa.intelligence import embeddings
from primeqa.intelligence.embeddings import (
    EMBEDDING_DIM,
    VoyageError,
    embed_batch,
)


pytestmark = pytest.mark.unit

# Capture the genuine httpx.Client at import time — BEFORE any test
# patches it. _patch_client's factory builds from this so it never
# recurses into its own patch.
_REAL_HTTPX_CLIENT = httpx.Client


def _vec(dim: int = EMBEDDING_DIM, fill: float = 0.1) -> list[float]:
    return [fill] * dim


def _ok_response(n: int, *, dim: int = EMBEDDING_DIM) -> dict:
    """A well-formed Voyage /embeddings response body for n inputs."""
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": _vec(dim), "index": i}
            for i in range(n)
        ],
        "model": "voyage-3",
        "usage": {"total_tokens": n * 4},
    }


def _patch_client(handler):
    """Patch embeddings.httpx.Client so embed_batch's internal client
    routes through a MockTransport running `handler`. The factory
    builds from the pre-captured real client class (not the patched
    name) and ignores embed_batch's timeout= kwarg."""
    def _factory(**_kwargs):
        return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler))
    return mock.patch.object(embeddings.httpx, "Client", _factory)


# ----------------------------------------------------------------------
# Empty input / key handling
# ----------------------------------------------------------------------

class TestEmptyAndKey:
    def test_empty_input_returns_empty_no_http_no_key(
        self, monkeypatch,
    ) -> None:
        """Empty input → [] with zero HTTP calls and no key lookup
        (key is not even read — a keyless caller can still no-op)."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        calls = []

        def handler(request):  # pragma: no cover - must not be hit
            calls.append(request)
            return httpx.Response(200, json=_ok_response(0))

        with _patch_client(handler):
            assert embed_batch([]) == []
        assert calls == []

    def test_missing_api_key_raises_non_retryable(
        self, monkeypatch,
    ) -> None:
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(VoyageError) as exc:
            embed_batch(["hello"])
        assert exc.value.retryable is False
        assert "VOYAGE_API_KEY" in str(exc.value)


# ----------------------------------------------------------------------
# Happy path
# ----------------------------------------------------------------------

class TestHappyPath:
    def test_single_text_returns_one_1024_dim_vector(
        self, monkeypatch,
    ) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(200, json=_ok_response(1))

        with _patch_client(handler):
            result = embed_batch(["salesforce account object"])
        assert len(result) == 1
        assert len(result[0]) == EMBEDDING_DIM == 1024

    def test_request_body_and_auth_header_shape(
        self, monkeypatch,
    ) -> None:
        """POST body carries input/model/input_type; the
        Authorization header is a Bearer token."""
        monkeypatch.setenv("VOYAGE_API_KEY", "secret-123")
        seen = {}

        def handler(request):
            import json as _json
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json=_ok_response(2))

        with _patch_client(handler):
            embed_batch(["a", "b"], input_type="document")
        assert seen["url"] == embeddings.VOYAGE_API_URL
        assert seen["auth"] == "Bearer secret-123"
        assert seen["body"]["model"] == "voyage-3"
        assert seen["body"]["input"] == ["a", "b"]
        assert seen["body"]["input_type"] == "document"

    def test_input_type_query_passed_through(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        seen = {}

        def handler(request):
            import json as _json
            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json=_ok_response(1))

        with _patch_client(handler):
            embed_batch(["find me"], input_type="query")
        assert seen["body"]["input_type"] == "query"


# ----------------------------------------------------------------------
# Explicit api_key (D-179) — per-env Voyage key from the LLM connection
# ----------------------------------------------------------------------

class TestExplicitApiKey:
    def test_explicit_key_used_in_auth_header_no_env(
        self, monkeypatch,
    ) -> None:
        """A passed api_key is used directly; the env var is never
        consulted (the enrichment worker has no VOYAGE_API_KEY set)."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_response(1))

        with _patch_client(handler):
            result = embed_batch(["x"], api_key="conn-voyage-key")
        assert seen["auth"] == "Bearer conn-voyage-key"
        assert len(result) == 1

    def test_explicit_key_overrides_env(self, monkeypatch) -> None:
        """When BOTH are present the explicit key wins — proves we
        never silently fall back to a stale env var."""
        monkeypatch.setenv("VOYAGE_API_KEY", "env-key")
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_response(1))

        with _patch_client(handler):
            embed_batch(["x"], api_key="explicit-key")
        assert seen["auth"] == "Bearer explicit-key"

    def test_no_key_falls_back_to_env_with_warning(
        self, monkeypatch, caplog,
    ) -> None:
        """No api_key → env-var fallback, and a warning is logged so the
        fallback is visible in worker logs (D-179)."""
        monkeypatch.setenv("VOYAGE_API_KEY", "env-key")
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_response(1))

        import logging
        with caplog.at_level(logging.WARNING,
                             logger="primeqa.intelligence.embeddings"):
            with _patch_client(handler):
                embed_batch(["x"])
        assert seen["auth"] == "Bearer env-key"
        assert any("falling back" in r.message for r in caplog.records)

    def test_blank_explicit_key_falls_back_to_env(
        self, monkeypatch,
    ) -> None:
        """A whitespace-only api_key strips to empty → env fallback (a
        connection with a blank voyage_api_key shouldn't send 'Bearer ')."""
        monkeypatch.setenv("VOYAGE_API_KEY", "env-key")
        seen = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            return httpx.Response(200, json=_ok_response(1))

        with _patch_client(handler):
            embed_batch(["x"], api_key="   ")
        assert seen["auth"] == "Bearer env-key"

    def test_blank_explicit_key_no_env_raises(self, monkeypatch) -> None:
        """Blank explicit key AND no env var → the same non-retryable
        missing-key error as a keyless call."""
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        with pytest.raises(VoyageError) as exc:
            embed_batch(["x"], api_key="  ")
        assert exc.value.retryable is False


# ----------------------------------------------------------------------
# Batch splitting
# ----------------------------------------------------------------------

class TestBatchSplitting:
    def test_over_limit_splits_into_sequential_calls(
        self, monkeypatch,
    ) -> None:
        """200 texts → 2 POSTs (128 + 72). BATCH_SIZE_LIMIT is 128."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        chunk_sizes = []

        def handler(request):
            import json as _json
            n = len(_json.loads(request.content)["input"])
            chunk_sizes.append(n)
            return httpx.Response(200, json=_ok_response(n))

        texts = [f"t{i}" for i in range(200)]
        with _patch_client(handler):
            result = embed_batch(texts)
        assert chunk_sizes == [128, 72]
        assert len(result) == 200

    def test_order_preserved_across_batches(self, monkeypatch) -> None:
        """The i-th output vector corresponds to the i-th input even
        when the input spans multiple API calls. Each mocked vector is
        tagged with its global index in element 0 so order is
        verifiable."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        # Stateful handler: assigns a monotonically increasing index
        # tag to each embedding across calls.
        state = {"next": 0}

        def handler(request):
            import json as _json
            n = len(_json.loads(request.content)["input"])
            data = []
            for _ in range(n):
                v = _vec()
                v[0] = float(state["next"])
                state["next"] += 1
                data.append({"embedding": v})
            return httpx.Response(200, json={"data": data,
                                             "usage": {"total_tokens": n}})

        texts = [f"t{i}" for i in range(150)]  # 128 + 22
        with _patch_client(handler):
            result = embed_batch(texts)
        assert len(result) == 150
        # element-0 tag equals the input index for every vector
        assert [v[0] for v in result] == [float(i) for i in range(150)]

    def test_exactly_at_limit_is_single_call(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
        n_calls = []

        def handler(request):
            import json as _json
            n_calls.append(len(_json.loads(request.content)["input"]))
            return httpx.Response(
                200, json=_ok_response(n_calls[-1]),
            )

        with _patch_client(handler):
            embed_batch([f"t{i}" for i in range(128)])
        assert n_calls == [128]


# ----------------------------------------------------------------------
# Error classification
# ----------------------------------------------------------------------

class TestErrorClassification:
    def test_429_raises_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(429, json={"detail": "rate limited"})

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is True
        assert "429" in str(exc.value)

    def test_500_raises_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(500, text="internal error")

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is True

    def test_503_raises_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(503, text="unavailable")

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is True

    def test_401_raises_non_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "bad-key")

        def handler(request):
            return httpx.Response(401, json={"detail": "unauthorized"})

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is False

    def test_403_raises_non_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(403, json={"detail": "forbidden"})

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is False

    def test_400_raises_non_retryable(self, monkeypatch) -> None:
        """A 4xx that isn't 429/401/403 (e.g. malformed request) is
        terminal — retrying the same input won't help."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(400, text="bad request")

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is False

    def test_network_error_raises_retryable(self, monkeypatch) -> None:
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            raise httpx.ConnectError("connection refused")

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is True
        assert "network error" in str(exc.value).lower()

    def test_dimension_mismatch_raises_non_retryable(
        self, monkeypatch,
    ) -> None:
        """Voyage returning a non-1024 dimension is a model/config
        mismatch — terminal, not transient."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(200, json=_ok_response(1, dim=512))

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is False
        assert "512" in str(exc.value)

    def test_unexpected_response_shape_raises_non_retryable(
        self, monkeypatch,
    ) -> None:
        """A 200 with a body missing the 'data' key → terminal
        (schema drift / wrong endpoint), not transient."""
        monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

        def handler(request):
            return httpx.Response(200, json={"unexpected": "shape"})

        with _patch_client(handler):
            with pytest.raises(VoyageError) as exc:
                embed_batch(["hello"])
        assert exc.value.retryable is False
