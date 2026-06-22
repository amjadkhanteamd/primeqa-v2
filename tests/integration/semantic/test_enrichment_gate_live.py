"""FAITHFUL real-DB proof for the 1c split enrichment gate.

Mocks nothing — seeds real ``entities`` + detail rows, runs the REAL gate SQL
(``_carry_forward_embeddings`` / ``_apply_summary_gate``, the actual column-to-
column copy + read-back hash), and asserts the ACTUAL stored embedding/summary.
Per-test transaction rolls back at teardown (conftest ``conn`` fixture).

Embedding gate (high cardinality — every entity type):
  * input UNCHANGED (semantic_text_hash matches prior, prior has an embedding) →
    prior embedding carried forward; the carried set names the entity → it is NOT
    enqueued → ZERO Voyage call.
  * input CHANGED → not carried (re-embed).
  * red-on-disable → without the gate the new row's embedding stays NULL.
  * prior not yet embedded (async worker lag) → not carried (never stale).

Summary gate (ValidationRule): same, plus the divergence guard — the gate hashes
the EXACT context the worker feeds (both call enrichment_gate.build_summary_context).
"""
from __future__ import annotations

import hashlib

from sqlalchemy import text

from primeqa.sync.batching import EntityForWrite
from primeqa.sync.enrichment_gate import (
    build_summary_context,
    summary_input_hash,
    summary_input_signature,
    summary_model_tag,
    summary_prompt_version,
)
from primeqa.sync.materialize import (
    _apply_summary_gate,
    _carry_forward_embeddings,
)

_DIM = 1024  # voyage-3 dimensionality (post-D-179); the live vector column width


def _vec(val: float = 0.1) -> str:
    return "[" + ",".join([str(val)] * _DIM) + "]"


def _set_embedding(conn, entity_id, *, vec, sth: str) -> None:
    conn.execute(text("""
        UPDATE entities
        SET embedding = CASE WHEN :vec IS NULL THEN NULL
                             ELSE CAST(:vec AS vector) END,
            embedding_model = CASE WHEN :vec IS NULL THEN NULL ELSE 'voyage-3' END,
            embedding_generated_at = CASE WHEN :vec IS NULL THEN NULL ELSE NOW() END,
            semantic_text_hash = :sth
        WHERE id = CAST(:id AS uuid)
    """), {"vec": vec, "sth": sth, "id": str(entity_id)})


def _embedding_of(conn, entity_id):
    return conn.execute(text(
        "SELECT embedding FROM entities WHERE id = CAST(:id AS uuid)"
    ), {"id": str(entity_id)}).scalar()


def _efw(prior_id, sth: str) -> EntityForWrite:
    # _carry_forward_embeddings / _apply_summary_gate read only prior_entity_id
    # (+ semantic_text_hash for embed).
    return EntityForWrite(
        external_id="x", normalized={}, presentation={},
        semantic_text="", hash_normalized="h",
        semantic_text_hash=sth, prior_entity_id=str(prior_id))


def _two_versions(seed):
    return seed.version(), seed.version()


# ----------------------------------------------------------------------
# Embedding gate
# ----------------------------------------------------------------------
class TestEmbedGate:
    def _pair(self, conn, seed, *, prior_vec, prior_sth, new_sth):
        v0, v1 = _two_versions(seed)
        prior = seed.entity("ValidationRule", "Account.R", valid_from_seq=v0,
                            valid_to_seq=v1, sf_id="VR1")
        new = seed.entity("ValidationRule", "Account.R", valid_from_seq=v1,
                          sf_id="VR1")
        _set_embedding(conn, prior, vec=prior_vec, sth=prior_sth)
        _set_embedding(conn, new, vec=None, sth=new_sth)
        return prior, new

    def test_carry_forward_when_input_unchanged(self, conn, seed) -> None:
        prior, new = self._pair(conn, seed, prior_vec=_vec(0.3),
                                prior_sth="H", new_sth="H")
        carried = _carry_forward_embeddings(conn, [_efw(prior, "H")], [str(new)])
        assert carried == {str(new)}                       # carried → NOT enqueued
        assert _embedding_of(conn, new) is not None        # prior embedding copied
        assert _embedding_of(conn, new) == _embedding_of(conn, prior)

    def test_red_on_disable_without_gate_stays_unembedded(self, conn, seed) -> None:
        # RED-ON-DISABLE: skip the gate → the new row's embedding stays NULL (the
        # worker would re-embed). Running the gate then flips it (carries forward).
        prior, new = self._pair(conn, seed, prior_vec=_vec(0.3),
                                prior_sth="H", new_sth="H")
        assert _embedding_of(conn, new) is None            # gate disabled → no carry
        carried = _carry_forward_embeddings(conn, [_efw(prior, "H")], [str(new)])
        assert carried == {str(new)}
        assert _embedding_of(conn, new) is not None        # gate enabled → carried

    def test_reembed_when_input_changed(self, conn, seed) -> None:
        prior, new = self._pair(conn, seed, prior_vec=_vec(0.3),
                                prior_sth="H_OLD", new_sth="H_NEW")
        carried = _carry_forward_embeddings(conn, [_efw(prior, "H_NEW")], [str(new)])
        assert carried == set()                            # changed → re-embed
        assert _embedding_of(conn, new) is None

    def test_not_carried_when_prior_not_yet_embedded(self, conn, seed) -> None:
        # Async worker lag: prior exists, hash matches, but embedding still NULL →
        # must NOT carry a NULL forward (re-enqueue, never stale).
        prior, new = self._pair(conn, seed, prior_vec=None,
                                prior_sth="H", new_sth="H")
        carried = _carry_forward_embeddings(conn, [_efw(prior, "H")], [str(new)])
        assert carried == set()
        assert _embedding_of(conn, new) is None


# ----------------------------------------------------------------------
# Summary gate (ValidationRule) + divergence guard
# ----------------------------------------------------------------------
def _vr_detail(conn, entity_id, object_entity_id, *, summary=None, sih=None):
    conn.execute(text("""
        INSERT INTO validation_rule_details
            (entity_id, object_entity_id, summary_text, summary_input_hash,
             summary_embedding, summary_model, summary_prompt_version,
             summary_generated_at)
        VALUES (CAST(:e AS uuid), CAST(:o AS uuid), :s, :h,
                CASE WHEN :s IS NULL THEN NULL ELSE CAST(:v AS vector) END,
                CASE WHEN :s IS NULL THEN NULL ELSE 'claude' END,
                CASE WHEN :s IS NULL THEN NULL ELSE 'v1' END,
                CASE WHEN :s IS NULL THEN NULL ELSE NOW() END)
    """), {"e": str(entity_id), "o": str(object_entity_id), "s": summary,
           "h": sih, "v": _vec(0.5)})


def _summary_of(conn, entity_id):
    return conn.execute(text(
        "SELECT summary_text, summary_input_hash FROM validation_rule_details "
        "WHERE entity_id = CAST(:e AS uuid)"
    ), {"e": str(entity_id)}).fetchone()


class TestSummaryGate:
    def _seed_pair(self, conn, seed, *, same_meta: bool):
        v0, v1 = _two_versions(seed)
        obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJ")
        prior = seed.entity("ValidationRule", "Account.R", valid_from_seq=v0,
                            valid_to_seq=v1, sf_id="VR1",
                            attributes={"Metadata": {"errorMessage": "BOOM",
                                                     "errorConditionFormula": "X"}})
        new_meta = {"errorMessage": "BOOM" if same_meta else "DIFFERENT",
                    "errorConditionFormula": "X"}
        new = seed.entity("ValidationRule", "Account.R", valid_from_seq=v1,
                          sf_id="VR1", attributes={"Metadata": new_meta})
        # Detail rows (no summary yet). Store the prior's summary_input_hash via the
        # gate itself (current prompt version), then add its summary content — so
        # the prior is self-consistent with whatever the gate computes for `new`.
        _vr_detail(conn, prior, obj, summary=None, sih=None)
        _vr_detail(conn, new, obj, summary=None, sih=None)
        _apply_summary_gate(conn, "ValidationRule", [str(prior)], [])
        conn.execute(text("""
            UPDATE validation_rule_details
            SET summary_text = 'A grounded summary.',
                summary_embedding = CAST(:v AS vector),
                summary_model = 'claude',
                summary_prompt_version = :pv,
                summary_generated_at = NOW()
            WHERE entity_id = CAST(:e AS uuid)
        """), {"v": _vec(0.5), "pv": summary_prompt_version("ValidationRule"),
               "e": str(prior)})
        return obj, prior, new

    def test_carry_forward_when_summary_input_unchanged(self, conn, seed) -> None:
        obj, prior, new = self._seed_pair(conn, seed, same_meta=True)
        carried = _apply_summary_gate(
            conn, "ValidationRule", [], [(_efw(prior, ""), str(new))])
        assert carried == {str(new)}                       # carried → NOT enqueued
        s = _summary_of(conn, new)
        assert s[0] == "A grounded summary."               # prior summary copied
        assert s[1] is not None                            # new hash stored

    def test_resummarize_when_summary_input_changed(self, conn, seed) -> None:
        obj, prior, new = self._seed_pair(conn, seed, same_meta=False)
        carried = _apply_summary_gate(
            conn, "ValidationRule", [], [(_efw(prior, ""), str(new))])
        assert carried == set()                            # changed → re-summarize
        s = _summary_of(conn, new)
        assert s[0] is None                                # no carry
        assert s[1] is not None                            # ...but new hash IS stored

    def test_divergence_guard_worker_feeds_what_gate_hashes(self, conn, seed) -> None:
        # The gate stores hash(build_summary_context); the worker FEEDS
        # build_summary_context. Same function → the stored hash == hashing the
        # worker-fed context. (Drift here would re-summarize, never carry stale.)
        obj, prior, new = self._seed_pair(conn, seed, same_meta=True)
        _apply_summary_gate(conn, "ValidationRule", [str(new)], [])
        stored = _summary_of(conn, new)[1]
        fed_ctx = build_summary_context(conn, "ValidationRule", str(new))
        pv = summary_prompt_version("ValidationRule")
        # D-254: the gate namespaces the hash by prompt_version AND summary_model.
        recomputed = hashlib.sha256(
            f"{pv}\x00{summary_model_tag()}\x00{summary_input_signature(fed_ctx)}"
            .encode("utf-8")).hexdigest()
        assert stored == recomputed

    def test_producer_bump_invalidates_carry_forward(self, conn, seed) -> None:
        # PRODUCER-BUMP guard: the prior summary was authored under an OLDER prompt
        # version (different hash namespace), so even with identical org-data input
        # the gate must NOT carry it forward — re-summarize under the new prompt.
        v0, v1 = _two_versions(seed)
        obj = seed.entity("Object", "Account", valid_from_seq=v0, sf_id="OBJ")
        prior = seed.entity("ValidationRule", "Account.R", valid_from_seq=v0,
                            valid_to_seq=v1, sf_id="VR1",
                            attributes={"Metadata": {"errorMessage": "BOOM"}})
        new = seed.entity("ValidationRule", "Account.R", valid_from_seq=v1,
                          sf_id="VR1", attributes={"Metadata": {"errorMessage": "BOOM"}})
        _vr_detail(conn, prior, obj, summary=None, sih=None)
        _vr_detail(conn, new, obj, summary=None, sih=None)
        # prior hash computed under an OLD prompt version (producer drift) ...
        # (D-254: summary_input_hash also folds the summary model; the drift here
        # is the prompt version, so the current model tag is the right argument.)
        old_h = summary_input_hash(conn, "ValidationRule", str(prior),
                                   "entity_summary_validation_rule@v0",
                                   summary_model_tag())
        conn.execute(text("""
            UPDATE validation_rule_details
            SET summary_text = 'Old-prompt summary.', summary_input_hash = :h,
                summary_embedding = CAST(:v AS vector), summary_model = 'claude',
                summary_prompt_version = 'entity_summary_validation_rule@v0',
                summary_generated_at = NOW()
            WHERE entity_id = CAST(:e AS uuid)
        """), {"h": old_h, "v": _vec(0.5), "e": str(prior)})

        carried = _apply_summary_gate(
            conn, "ValidationRule", [], [(_efw(prior, ""), str(new))])
        assert carried == set()                            # producer bumped → no carry
        assert _summary_of(conn, new)[0] is None           # not the stale old summary
