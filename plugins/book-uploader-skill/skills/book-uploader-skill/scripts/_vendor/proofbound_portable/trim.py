"""Trim-size catalog: pure data shared by the server, the cover pipeline and the skills."""

from __future__ import annotations

# Trim Size Catalog
# Drives cover template selection, Lulu POD SKU defaults, and Quarto interior
# geometry. Mirrored in apps/shared/src/config/trimSizes.ts for the frontend.
# CHECK constraint on book_projects.trim_size enforces these keys at the DB layer.
# A5 is 148x210mm = 5.83x8.27in. Proofbound does not currently generate a KDP
# A5 cover; cover_template_kdp is None for a5, so the cover pipeline skips KDP
# cover emission for A5 books. Whether KDP itself stocks A5 is tracked
# separately (bead proofbound_monorepo-rnvf) — Amazon does list a 5.83x8.27in
# paperback trim, so do not restate the older "KDP does not offer A5" claim
# without sourcing it from the published KDP guidelines.
# Digest (5.5x8.5in) is a standard KDP trim, so it emits BOTH publishers' covers
# (cover_template_kdp is set, unlike a5).
# SKU convention: all entries use Lulu's DOTTED pod_package_id format
# (e.g. 0550X0850.BW.STD.PB.060UW444.MXX), live since 2026-03-31. The legacy
# dot-less 27-char form was retired 2026-07-03 ahead of Lulu's 2027-02-01 cutoff.
TRIM_SIZES: dict[str, dict[str, object]] = {
    "6x9": {
        "width_in": 6.0,
        "height_in": 9.0,
        "label": "6×9 in (US Trade)",
        "cover_template_lulu": "lulu_6x9",
        "cover_template_kdp": "amazon_kdp",
        # Lulu BW SKU used as default_pod_package_id for new books at this trim.
        # Sandbox-verified 2026-07-02 (unit_tier_cost $4.49 @100pp).
        "lulu_bw_sku": "0600X0900.BW.STD.PB.060UW444.MXX",
    },
    "a5": {
        "width_in": 5.83,
        "height_in": 8.27,
        "label": "A5 (148×210 mm)",
        "cover_template_lulu": "lulu_a5",
        "cover_template_kdp": None,
        # Sandbox-verified 2026-07-02: unit_tier_cost $4.49 @100pp via
        # /print-job-cost-calculations/.
        "lulu_bw_sku": "0583X0827.BW.STD.PB.060UW444.MXX",
    },
    "digest": {
        "width_in": 5.5,
        "height_in": 8.5,
        "label": "Digest (5.5×8.5 in)",
        "cover_template_lulu": "lulu_digest",
        "cover_template_kdp": "amazon_kdp_digest",  # 5.5x8.5 is a standard KDP trim (A5 is Lulu-only in our pipeline)
        # Dotted format; sandbox-verified 2026-07-02 (unit_tier_cost $4.49 @100pp).
        "lulu_bw_sku": "0550X0850.BW.STD.PB.060UW444.MXX",
    },
}
DEFAULT_TRIM_SIZE = "6x9"
