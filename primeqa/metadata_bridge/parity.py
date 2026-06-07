"""Cutover Step 4a (D-185): metadata read-parity — S1 vs v1 ``meta_*``.

The parallel-run-validation harness. Proves the S1 reader
(:class:`primeqa.metadata_bridge.s1_reader.MetadataS1Reader`) returns the same semantic
metadata as the v1 :class:`MetadataRepository` for the same environment, so the
``meta_*`` drop (Step 5) is safe.

Two kinds of divergence, deliberately kept distinct:

- **Shape divergence** — a field/object present in BOTH sources, but a CRUD/type
  flag differs. This is a *reader bug*, and zero of it across the rollout is the
  Step-4 exit-gate (``ParityReport.clean``).
- **Membership divergence** — a field/object in one source but not the other. This
  is *sync-time drift* between the independent v1-meta and S1 syncs (the org
  changed between the two sync times) — expected, and exactly what
  ``MetadataService.check_drift`` surfaces. Reported, but NOT counted against
  ``clean``.

Axes deliberately EXCLUDED from the shape comparison (documented gaps, not bugs):
``MetaField.reference_to`` (S1 defers it → always ``None``), ``picklist_values``
(stored differently: v1 JSON dicts vs S1 ``list[str]``), ``is_required`` (a
**by-design definitional difference** — ``meta_*`` uses the schema rule
``not nillable AND not defaultedOnCreate``; S1 uses the UI/layout enforcement flag;
neither is a reader bug, and it drives no validator rule — only a generation prompt
hint, D-190), and VR ``error_condition_formula`` / ``is_active`` (S1 doesn't sync
them, D-162). VR parity is membership-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field as _field
from typing import Any, Dict, List

# Attributes both readers expose as plain, directly-comparable values. A
# difference on one of these for an entity present in BOTH sources is a reader
# bug — the Step-4 exit-gate. (reference_to / picklist_values / is_required are
# excluded — see the module docstring; is_required is a by-design definitional
# difference, not a reader bug, D-190.)
_OBJECT_SHAPE = ("is_createable", "is_custom")
_FIELD_SHAPE = ("field_type", "is_custom",
                "is_createable", "is_updateable")


def _obj_api_by_id(objects) -> Dict[Any, str]:
    """{object entity id -> api_name} for either reader's get_objects() rows."""
    return {o.id: o.api_name for o in objects}


def _diff_attrs(meta_row, s1_row, attrs) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for attr in attrs:
        mv = getattr(meta_row, attr, None)
        sv = getattr(s1_row, attr, None)
        if mv != sv:
            out[attr] = {"meta": mv, "s1": sv}
    return out


@dataclass
class ParityReport:
    objects: Dict[str, Any] = _field(default_factory=lambda: {
        "both": 0, "only_meta": [], "only_s1": [], "shape_mismatch": []})
    fields: Dict[str, Any] = _field(default_factory=lambda: {
        "both": 0, "only_meta": [], "only_s1": [], "shape_mismatch": []})
    vrs: Dict[str, Any] = _field(default_factory=lambda: {
        "both": 0, "only_meta": [], "only_s1": []})

    @property
    def shape_mismatches(self) -> int:
        return (len(self.objects["shape_mismatch"])
                + len(self.fields["shape_mismatch"]))

    @property
    def clean(self) -> bool:
        """Exit-gate: zero SHAPE divergence on the intersection. Membership
        divergence (only_meta / only_s1) is sync-time drift, not a reader bug."""
        return self.shape_mismatches == 0

    def summary(self) -> Dict[str, Any]:
        def _counts(d):
            return {k: (v if isinstance(v, int) else len(v)) for k, v in d.items()}
        return {
            "clean": self.clean,
            "shape_mismatches": self.shape_mismatches,
            "objects": _counts(self.objects),
            "fields": _counts(self.fields),
            "vrs": _counts(self.vrs),
        }


class MetadataParityChecker:
    """Diffs a v1 ``meta_*`` read-interface against an S1 read-interface for one
    environment. Both must answer the same ``meta_*``-shaped methods
    (``get_objects`` / ``get_fields`` / ``get_validation_rules``). The S1 reader
    ignores ``meta_version_id`` (it's pinned to the seq it hydrated at)."""

    def __init__(self, meta_repo, s1_reader):
        self._meta = meta_repo
        self._s1 = s1_reader

    def check(self, meta_version_id) -> ParityReport:
        rpt = ParityReport()
        self._check_objects(meta_version_id, rpt)
        self._check_fields(meta_version_id, rpt)
        self._check_vrs(meta_version_id, rpt)
        return rpt

    def _check_objects(self, mv, rpt: ParityReport) -> None:
        m = {o.api_name: o for o in self._meta.get_objects(mv)}
        s = {o.api_name: o for o in self._s1.get_objects(mv)}
        rpt.objects["only_meta"] = sorted(set(m) - set(s))
        rpt.objects["only_s1"] = sorted(set(s) - set(m))
        rpt.objects["both"] = len(set(m) & set(s))
        for name in sorted(set(m) & set(s)):
            d = _diff_attrs(m[name], s[name], _OBJECT_SHAPE)
            if d:
                rpt.objects["shape_mismatch"].append({"api_name": name, "attrs": d})

    def _check_fields(self, mv, rpt: ParityReport) -> None:
        m_objs = _obj_api_by_id(self._meta.get_objects(mv))
        s_objs = _obj_api_by_id(self._s1.get_objects(mv))
        m = {(m_objs.get(f.meta_object_id, "?"), f.api_name): f
             for f in self._meta.get_fields(mv)}
        s = {(s_objs.get(f.meta_object_id, "?"), f.api_name): f
             for f in self._s1.get_fields(mv)}
        rpt.fields["only_meta"] = sorted(f"{o}.{n}" for (o, n) in (set(m) - set(s)))
        rpt.fields["only_s1"] = sorted(f"{o}.{n}" for (o, n) in (set(s) - set(m)))
        rpt.fields["both"] = len(set(m) & set(s))
        for key in sorted(set(m) & set(s)):
            d = _diff_attrs(m[key], s[key], _FIELD_SHAPE)
            if d:
                rpt.fields["shape_mismatch"].append(
                    {"field": f"{key[0]}.{key[1]}", "attrs": d})

    def _check_vrs(self, mv, rpt: ParityReport) -> None:
        m_objs = _obj_api_by_id(self._meta.get_objects(mv))

        def _m_key(vr):
            return (m_objs.get(vr.meta_object_id, ""), vr.rule_name)

        def _s_key(vr):
            return (vr.meta_object.api_name if vr.meta_object else "", vr.rule_name)

        m = {_m_key(vr) for vr in self._meta.get_validation_rules(mv)}
        s = {_s_key(vr) for vr in self._s1.get_validation_rules(mv)}
        rpt.vrs["only_meta"] = sorted(f"{o}.{n}" for (o, n) in (m - s))
        rpt.vrs["only_s1"] = sorted(f"{o}.{n}" for (o, n) in (s - m))
        rpt.vrs["both"] = len(m & s)
