# Salesforce API References

Pinned canonical references for the Salesforce APIs PrimeQA
integrates against. Used by the sf_client integration layer
(`primeqa/integrations/sf_client.py`) and during sync-layer
design.

## Files

- `salesforce_metadata_api_v66.0.pdf` — Metadata API Developer
  Guide (Salesforce Spring '26). Covers SOAP and REST Metadata
  API operations, listMetadata, the canonical StandardValueSet
  names catalog (Appendix C), and metadata type definitions.
- `salesforce_tooling_api_v66.0.pdf` — Tooling API Developer
  Guide (Salesforce Spring '26). Covers Tooling SOQL grammar,
  sobject schemas (RecordType, ValidationRule, GlobalValueSet,
  etc.), and the Metadata-or-FullName 1-row constraint family.
- `salesforce_soap_api_v66.0.pdf` — SOAP API Developer Guide
  (Salesforce Spring '26). Canonical reference for standard
  sObject schemas, describe response shapes, and the formal
  permission model used by Profile and PermissionSet entities
  (relevant for upcoming 2C-extended methods).
- `salesforce_soql_sosl_v66.0.pdf` — SOQL and SOSL Reference
  (Salesforce Spring '26). Foundational query-language
  reference: grammar, governor limits, function reference,
  and the formal definition of constraints documented in
  PHASE_2_PLAN_corrections.md (reified columns, subquery
  limits, relationship traversal rules).
- `salesforce_object_reference_v66.0.pdf` — Object Reference
  for the Salesforce Platform (Salesforce Spring '26).
  Per-object field listings with semantics. Used to verify
  test-generation assertions against the documented platform
  schema rather than relying on CC's training-data
  reconstructions.

## API version pinning

Both files are pinned to Salesforce API v66.0 (Spring '26),
matching `SalesforceClient.api_version` in
`primeqa/integrations/sf_client.py`.

When the API version bumps:
1. Re-download both PDFs from Salesforce's developer documentation
   site for the new version.
2. Update filenames to reflect the new version
   (`salesforce_metadata_api_v<NEW>.pdf` etc.).
3. Replace the old files in this directory.
4. Re-audit `primeqa/integrations/sf_constants.py`
   `STANDARD_VALUE_SET_LABELS` against the new Metadata API PDF
   (the canonical StandardValueSet names section).
5. Re-run live integration tests against the sandbox; surface any
   new constraints in `PHASE_2_PLAN_corrections.md`.

## Why these are committed to the repo

- Salesforce documentation URLs occasionally change or move
  behind interstitial pages; pinned PDFs survive.
- Future contractors have offline-capable references to the
  exact API version PrimeQA is pinned against.
- The audit-at-version-bump discipline becomes operationally
  concrete: replace the PDFs, re-run the catalogs.

## Related documents

- `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN.md`
  — §4.2 sf_client design
- `docs/architecture/substrate_1_semantic_org_model/PHASE_2_PLAN_corrections.md`
  — §1, §2, §4, §5: documented Salesforce-API constraint family
  discovered during 2C and 2C-extended
