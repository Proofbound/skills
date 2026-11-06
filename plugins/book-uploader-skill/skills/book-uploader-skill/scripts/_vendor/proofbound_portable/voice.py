"""Voice-axis maths shared by the server scorer and the voice-check skill (ADR-0006).

The skill's ``score --local`` and the server's ``score --remote`` must agree to
wire rounding (4 dp), and the server's slop windows must be the same windows the
skill embeds. Both therefore run this one copy:

* the pinned embedder (which weights produce the vectors);
* text preparation: NFC + whitespace-collapse, 300-word windows on a 150-word
  stride, and the character span of each window in the normalised text;
* the voice axis: cosine distance to the author's centroid, standardised by the
  author's own window-level spread, banded, with a per-window breakdown.

Loading the embedder is not here (torch; the server adds a lock and an idle TTL),
nor is building a profile (client-only). Needs numpy: install the ``voice`` extra.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np

# The checkpoint is bf16; every loader must force float32 (bf16 shifts the
# embeddings ~2e-2, which invalidates the probe and every stored profile).
EMBEDDER_MODEL = "StyleDistance/styledistance"
EMBEDDER_REVISION = "b7df5f0b0480773c097ba3121d83ca32b71015ca"
#: Full sha: a revision prefix is not a pin, and the server rejects a profile
#: whose ``embedder`` is anything but this string.
EMBEDDER = f"{EMBEDDER_MODEL}@{EMBEDDER_REVISION}"

#: 300 words matches the slices the slop probe was fit on; the half-window
#: stride gives the per-window breakdown.
WINDOW_WORDS = 300
STRIDE_WORDS = 150

VERDICT_IN_BAND = "in_band"
VERDICT_DRIFTING = "drifting"
VERDICT_TOO_CLOSE = "too_close"

SPREAD_OK = "ok"
SPREAD_TOO_UNIFORM = "too_uniform"
SPREAD_ERRATIC = "erratic"
SPREAD_NA = "n/a"


# ------------------------------------------------------------------ text prep


def normalise_text(text: str) -> tuple[str, tuple[str, ...]]:
    """NFC + whitespace-collapse; returns the normalised text and its words."""
    words = tuple(unicodedata.normalize("NFC", text).split())
    return " ".join(words), words


def build_windows(
    n_words: int,
    *,
    window: int = WINDOW_WORDS,
    stride: int = STRIDE_WORDS,
) -> tuple[tuple[int, int], ...]:
    """Word-index windows ``(start, end)``, end exclusive, covering ``n_words``.

    Full windows start every ``stride`` words; if the last full window stops
    short of the end, one more window is added that ends at the last word (so it
    overlaps the previous one by more than usual rather than being a stub).
    """
    if n_words <= 0:
        return ()
    if n_words <= window:
        return ((0, n_words),)
    starts = list(range(0, n_words - window + 1, stride))
    if starts[-1] + window < n_words:
        starts.append(n_words - window)
    return tuple((s, s + window) for s in starts)


def word_offsets(words: Sequence[str]) -> tuple[int, ...]:
    """Prefix sum of ``len(word) + 1``: character start of every word in the
    single-space join, plus one sentinel equal to ``len(text) + 1``."""
    starts = [0]
    for w in words:
        starts.append(starts[-1] + len(w) + 1)
    return tuple(starts)


def char_span(starts: Sequence[int], window: tuple[int, int]) -> tuple[int, int]:
    """Character offsets (end exclusive) of a word window, given
    :func:`word_offsets` of the normalised words."""
    start, end = window
    return starts[start], starts[end] - 1


def char_spans(
    words: Sequence[str], windows: Sequence[tuple[int, int]]
) -> tuple[tuple[int, int], ...]:
    """Character offsets of every window in the normalised text."""
    starts = word_offsets(words)
    return tuple(char_span(starts, w) for w in windows)


# ------------------------------------------------------------------ voice axis


def unit(v: np.ndarray) -> np.ndarray:
    """``v`` scaled to unit length; a zero vector comes back unchanged."""
    n = np.linalg.norm(v)
    return v / n if n else v


def cosine_distances(vecs: np.ndarray, centroid: np.ndarray) -> np.ndarray:
    """``clip(1 - v·ĉ, 0, 2)`` for unit-norm rows ``v`` against the re-normalised
    centroid."""
    return np.clip(1.0 - vecs @ unit(centroid), 0.0, 2.0)


def score_voice(
    profile: Mapping[str, Any],
    vecs: np.ndarray,
    spans: Sequence[tuple[int, int]],
) -> dict[str, Any]:
    """Score unit-norm window vectors against a profile; the wire ``voice`` block.

    ``profile`` is the artifact as a mapping: ``centroid``, ``intra`` (``mean``,
    ``sd``, ``p10``, ``p50``, ``p90``), optional ``inter`` (``p50``), and
    ``spread`` (``author_p50``, may be ``None``). ``spans`` is one character span
    per row of ``vecs``.

    * window ``z = (d - intra.mean) / intra.sd``;
    * document distance is the **median** window distance (same units as the
      window-level calibration); verdict ``drifting`` above ``intra.p90``,
      ``too_close`` below ``intra.p10``, else ``in_band``;
    * spread: the sample SD (``ddof=1``) of the window distances against the
      author's ``spread.author_p50`` — ``too_uniform`` under half of it,
      ``erratic`` over twice it, ``n/a`` with one window or no author figure;
    * ``worst`` is present (and true) on exactly the first arg-max window;
    * every float is rounded to 4 dp.
    """
    if len(spans) != len(vecs) or len(vecs) == 0:
        raise ValueError("one span per window vector, at least one window")
    centroid = np.asarray(profile["centroid"], dtype=np.float64)
    intra = profile["intra"]
    sd = intra["sd"] or 1e-9
    d = cosine_distances(np.asarray(vecs, dtype=np.float64), centroid)
    z = (d - intra["mean"]) / sd
    doc_d = float(np.median(d))
    doc_z = float((doc_d - intra["mean"]) / sd)

    if doc_d > intra["p90"]:
        verdict = VERDICT_DRIFTING
    elif doc_d < intra["p10"]:
        verdict = VERDICT_TOO_CLOSE
    else:
        verdict = VERDICT_IN_BAND

    author_spread = (profile.get("spread") or {}).get("author_p50")
    observed = float(d.std(ddof=1)) if d.size > 1 else None
    if observed is None or not author_spread:
        spread_verdict = SPREAD_NA
    elif observed < 0.5 * author_spread:
        spread_verdict = SPREAD_TOO_UNIFORM
    elif observed > 2.0 * author_spread:
        spread_verdict = SPREAD_ERRATIC
    else:
        spread_verdict = SPREAD_OK

    worst = int(np.argmax(z))  # first max wins on ties
    windows = []
    for i, (s, e) in enumerate(spans):
        w: dict[str, Any] = {
            "start": s,
            "end": e,
            "z": round(float(z[i]), 4),
            "cosine": round(float(d[i]), 4),
        }
        if i == worst:
            w["worst"] = True
        windows.append(w)

    baseline = {
        "intra_p10": round(intra["p10"], 4),
        "intra_p50": round(intra["p50"], 4),
        "intra_p90": round(intra["p90"], 4),
    }
    if profile.get("inter"):
        baseline["inter_median"] = round(profile["inter"]["p50"], 4)

    return {
        "z": round(doc_z, 4),
        "verdict": verdict,
        "cosine": round(doc_d, 4),
        "baseline": baseline,
        "spread": {
            "observed": round(observed, 4) if observed is not None else None,
            "author_p50": round(author_spread, 4) if author_spread else None,
            "verdict": spread_verdict,
        },
        "windows": windows,
    }
