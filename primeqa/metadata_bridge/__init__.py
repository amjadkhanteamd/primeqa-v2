"""metadata_bridge — v1's read/ops bridge to the Substrate-1 org model.

Relocated out of primeqa/metadata/ (D-191) so the Step-5 deletion of the v1
metadata module is a clean removal. These modules read S1 (never meta_*):
accessor (the cutover_read_s1 read switch), s1_reader (S1 -> meta-shape reader),
parity (the S1-vs-meta_* parity instrument), s1_sync_console (the v1 UI/ops
bridge to the S1 sync + S1 freshness).
"""
