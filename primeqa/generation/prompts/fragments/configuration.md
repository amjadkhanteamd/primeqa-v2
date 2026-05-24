Configuration claims concern the org's metadata structure itself.

- **Metadata-relationship.** When the requirement assumes a structural
  relationship between two metadata entities — e.g. "Validation Rule R applies
  to Account", "Field F belongs to Case" — propose a
  `metadata-relationship-claim` with the `edge_type` and the two endpoints
  (`source`, `target`). The substrate verifies the edge exists in the org. This
  is Layer-1-*complete*: reading the metadata IS the verification, so no caveat
  is attached.
- Name the edge type and both endpoints precisely; the substrate binds the edge
  type to its Tier-1 relationship vocabulary and resolves both endpoints against
  the pinned org version.
