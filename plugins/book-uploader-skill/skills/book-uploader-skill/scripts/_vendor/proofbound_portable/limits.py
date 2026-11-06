"""Length limits both the server and the skills enforce.

Pure data; the server re-exports these from ``proofbound_shared.constants``.
"""

from __future__ import annotations

from .author import MAX_AUTHOR_BIO_CHARS  # noqa: F401

MIN_TOTAL_TARGET_WORDS = 10000
MAX_TOTAL_TARGET_WORDS = 80000

# Cover Generation Character Limits
# These limits are enforced in:
# - OpenAPI specs (openapi/services/cc-template-api-*.yml)
# - ReportLab composition (proofbound_shared/covers/composition.py)
# - API validation (FastAPI request models)
MAX_BOOK_BLURB_CHARS = 1200  # Back cover book description

# Scoring input limits (ADR-0006, POST /voice/score-slop and
# POST /voice/profiles/{id}/score). Mirrored in
# openapi/components/schemas/voice.yml (SlopScoreRequest) and enforced by
# cc_template_api/routers/voice.py: fewer words -> 422, more chars or more
# words -> 413. The word cap is what one request can embed inside the app's
# 100 s deadline on the production droplet (~1 s per 300-word window after a
# ~20 s cold load, bead b61z.8): 5,000 words = 33 windows, which still fits
# at the 2 s/window seen while the droplet was swapping. Longer drafts must be
# split by the client (Phase C, bead b61z.2.12); the 413 body says so.
SLOP_TEXT_MIN_WORDS = 150
SLOP_TEXT_MAX_WORDS = 5_000
