"""Voyage AI embedding generation via raw httpx.

Per D-049 (formally recorded in the §23 enrichment-worker cycle):
embeddings are produced by the Voyage AI API — model ``voyage-3``,
1024-dim. Raw ``httpx`` HTTP client, no vendor SDK — same pattern as
``primeqa/integrations/sf_client.py``.

Why an API and not a local model: modern PyTorch wheels are
unavailable on Intel macOS (the ecosystem dropped x86_64 macOS after
torch 2.2.2), so a local ``sentence-transformers`` model can't run on
the Intel-Mac dev box. The Voyage API runs identically on the dev box,
on Linux/Railway, and in CI — preserving the "verify locally = what
ships" parity a local model would break on this hardware.

API key: read from the ``VOYAGE_API_KEY`` environment variable. The
caller is responsible for loading ``.env`` (the worker does this at
startup via ``load_dotenv()``), exactly as ``sf_client.py`` callers
load the ``SF_*`` vars.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings"
MODEL_NAME = "voyage-3"
EMBEDDING_DIM = 1024
EMBEDDING_MODEL_TAG = "voyage-3"

# Voyage's per-request input-list cap. Inputs longer than this are
# chunked into sequential API calls by embed_batch().
BATCH_SIZE_LIMIT = 128

# Generous timeout — covers network + Voyage-side inference for a
# full 128-text batch.
REQUEST_TIMEOUT_S = 60.0

# ---------------------------------------------------------------------
# Voyage cost rate (S1 sync cost-telemetry — 1a: confirmed rate)
# ---------------------------------------------------------------------
#
# voyage-3's standard published price: USD 0.06 per 1,000,000 input tokens.
# Confirmed by AK against Voyage's pricing (1a), so VOYAGE_3_RATE_CONFIRMED is
# True and costs computed from it are authoritative (no provisional warning /
# rate_provisional marker is emitted while this flag is True). All cost math
# references this single constant via voyage_embedding_cost_usd(). If Voyage
# changes the price, update the value here and keep the flag True.
VOYAGE_3_USD_PER_1M_TOKENS = 0.06   # confirmed voyage-3 rate (USD / 1M input tokens)
VOYAGE_3_RATE_CONFIRMED = True      # rate above verified against Voyage pricing (1a)

_rate_warning_emitted = False


def voyage_embedding_cost_usd(total_tokens: int) -> float:
    """Provisional USD cost for ``total_tokens`` voyage-3 input tokens.

    ``cost = total_tokens * VOYAGE_3_USD_PER_1M_TOKENS / 1_000_000``.

    PROVISIONAL until ``VOYAGE_3_RATE_CONFIRMED`` is flipped — emits a
    one-time WARNING per process while the rate is unconfirmed so the
    placeholder can never silently pass as a real price. Pure otherwise
    (no DB, deterministic) so the cost math is unit-testable.

    Callers must pass a real measured token count; ``None``/missing tokens
    are the caller's to handle (see ``record_embedding_usage``) — this
    function never fabricates a zero.
    """
    global _rate_warning_emitted
    if not VOYAGE_3_RATE_CONFIRMED and not _rate_warning_emitted:
        logger.warning(
            "voyage_embedding_cost_usd: VOYAGE_3_USD_PER_1M_TOKENS=%s is a "
            "PLACEHOLDER (VOYAGE_3_RATE_CONFIRMED=False) — embedding costs are "
            "provisional until the rate is confirmed against Voyage pricing.",
            VOYAGE_3_USD_PER_1M_TOKENS,
        )
        _rate_warning_emitted = True
    return (int(total_tokens) * VOYAGE_3_USD_PER_1M_TOKENS) / 1_000_000.0


class VoyageError(Exception):
    """A Voyage API failure, tagged with retryability.

    ``retryable=True`` for transient conditions (network errors, 429
    rate limits, 5xx) — the worker re-queues these as
    ``failed_retryable``. ``retryable=False`` for terminal conditions
    (missing key, 401/403 auth, malformed response, dimension
    mismatch) — the worker marks these ``failed_permanent``.
    """

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable


def _get_api_key() -> str:
    """Return the Voyage API key from the environment, or raise.

    Non-retryable: a missing key won't fix itself between attempts.
    """
    key = os.environ.get("VOYAGE_API_KEY")
    if not key:
        raise VoyageError(
            "VOYAGE_API_KEY not set in environment",
            retryable=False,
        )
    return key


def embed_batch(
    texts: list[str],
    input_type: str = "document",
    api_key: str | None = None,
) -> list[list[float]]:
    """Embed a list of texts via the Voyage API (vectors only).

    Thin wrapper over :func:`embed_batch_with_usage` that discards the
    token count — preserves the original signature for the many callers
    (and tests) that only need vectors. Use ``embed_batch_with_usage``
    when you need the Voyage token count for cost attribution.
    """
    return embed_batch_with_usage(
        texts, input_type=input_type, api_key=api_key)[0]


def embed_batch_with_usage(
    texts: list[str],
    input_type: str = "document",
    api_key: str | None = None,
) -> tuple[list[list[float]], int | None]:
    """Embed a list of texts via the Voyage API, returning vectors + tokens.

    Returns ``(vectors, total_tokens)`` — a list of ``EMBEDDING_DIM``-length
    float vectors, one per input text, in input order.

    ``total_tokens`` is the Voyage input-token count for the call, summed
    across chunks. It is ``None`` when the Voyage response carried no usable
    ``usage.total_tokens`` (NO silent fabricated zero — the caller logs/marks
    the gap), and ``0`` for empty input.

    Inputs longer than ``BATCH_SIZE_LIMIT`` are split into sequential
    API calls; the per-chunk results are concatenated, so order is
    preserved across the split. Empty input is a no-op (returns ``[]``,
    no HTTP call, no key lookup).

    ``input_type='document'`` for entity content being indexed;
    ``'query'`` for search-query encoding (Voyage embeds the two
    asymmetrically). The enrichment worker always uses ``'document'``.

    ``api_key`` (D-179): when given, used directly — the enrichment worker
    resolves it per-env from the LLM connection's ``voyage_api_key``. When
    ``None``, falls back to the ``VOYAGE_API_KEY`` env var (system / non-worker
    callers, and a one-release safety net) and logs a warning so the fallback is
    visible. A missing key on either path raises ``VoyageError(retryable=False)``.

    Retry policy lives at the worker layer — this function raises
    ``VoyageError`` (with ``.retryable``) and does not retry itself.
    """
    if not texts:
        return [], 0

    if api_key:
        api_key = api_key.strip()
    if not api_key:
        logger.warning("embed_batch: no api_key passed; falling back to the "
                       "VOYAGE_API_KEY env var (D-179 — prefer a per-env LLM "
                       "connection voyage_api_key)")
        api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    all_embeddings: list[list[float]] = []
    total_tokens = 0
    tokens_missing = False

    with httpx.Client(timeout=REQUEST_TIMEOUT_S) as client:
        for start in range(0, len(texts), BATCH_SIZE_LIMIT):
            chunk = texts[start:start + BATCH_SIZE_LIMIT]
            body = {
                "input": chunk,
                "model": MODEL_NAME,
                "input_type": input_type,
            }

            try:
                resp = client.post(
                    VOYAGE_API_URL, json=body, headers=headers,
                )
            except httpx.RequestError as e:
                raise VoyageError(
                    f"Voyage API network error: {e}",
                    retryable=True,
                ) from e

            if resp.status_code == 429:
                raise VoyageError(
                    "Voyage API rate limited (429)",
                    retryable=True,
                )
            if resp.status_code >= 500:
                raise VoyageError(
                    f"Voyage API server error ({resp.status_code}): "
                    f"{resp.text[:200]}",
                    retryable=True,
                )
            if resp.status_code in (401, 403):
                raise VoyageError(
                    f"Voyage API auth error ({resp.status_code})",
                    retryable=False,
                )
            if resp.status_code != 200:
                raise VoyageError(
                    f"Voyage API error ({resp.status_code}): "
                    f"{resp.text[:200]}",
                    retryable=False,
                )

            try:
                data = resp.json()
                chunk_embeddings = [
                    item["embedding"] for item in data["data"]
                ]
            except (ValueError, KeyError, TypeError) as e:
                raise VoyageError(
                    f"Voyage API returned an unexpected response "
                    f"shape: {e}",
                    retryable=False,
                ) from e

            # Dimension guard — check once, on the first chunk. A
            # wrong dimension is a model/config mismatch, not a
            # transient fault.
            if start == 0 and chunk_embeddings:
                actual_dim = len(chunk_embeddings[0])
                if actual_dim != EMBEDDING_DIM:
                    raise VoyageError(
                        f"Voyage returned dim {actual_dim}; expected "
                        f"{EMBEDDING_DIM} (model={MODEL_NAME})",
                        retryable=False,
                    )

            all_embeddings.extend(chunk_embeddings)

            # Cost telemetry (1d): surface the Voyage input-token count.
            # A missing usage.total_tokens means we CANNOT measure this
            # call — flag the whole call unmeasured (loud) rather than
            # counting a fabricated zero.
            usage = data.get("usage") or {}
            chunk_tokens = usage.get("total_tokens")
            if chunk_tokens is None:
                tokens_missing = True
                logger.warning(
                    "embed_batch_with_usage: Voyage response had no "
                    "usage.total_tokens for a %d-text chunk — token count "
                    "unmeasured for this call (not a fabricated zero)",
                    len(chunk),
                )
            else:
                total_tokens += int(chunk_tokens)
            logger.debug(
                "Voyage embed batch: %d texts, %s tokens",
                len(chunk),
                "unmeasured" if chunk_tokens is None else chunk_tokens,
            )

    return all_embeddings, (None if tokens_missing else total_tokens)
