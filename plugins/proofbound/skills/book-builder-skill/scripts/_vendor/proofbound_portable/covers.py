"""Cover choices both the server and the cover-generator skill offer.

Pure data. The server's ``/covers/*`` request models build their ``Literal``
types from these tuples and the skill builds its ``--model`` / ``--template-name``
choices from them, so a model added here reaches both with no second edit.
``openapi/components/schemas/cover-generation.yml`` mirrors the enums; a
cc-template-api test pins the spec to this module.
"""

from __future__ import annotations

IMAGE_MODELS = (
    "ideogram-v3-turbo",
    "ideogram-v3-balanced",
    "ideogram-v3-quality",
    "recraft-v3",
    "flux-dev",
    "flux-1.1-pro",
    "gpt-image-2",
    "mock",
)
DEFAULT_IMAGE_MODEL = "gpt-image-2"

# Print layouts a compose renders (one PDF each).
COVER_TEMPLATES = (
    "amazon_kdp",
    "lulu_6x9",
    "lulu_a5",
    "lulu_digest",
    "amazon_kdp_digest",
)
DEFAULT_COVER_TEMPLATES = ("amazon_kdp", "lulu_6x9")

# Visual styles (colors, fonts) for the composed cover.
COVER_TEMPLATE_NAMES = ("professional_dark", "professional_light", "minimal_v1")
DEFAULT_COVER_TEMPLATE_NAME = "professional_dark"
