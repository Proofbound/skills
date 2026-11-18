"""Run the server's render policy locally, on the files the upload would ship.

The policy is not re-implemented here: ``find_render_policy_violations`` is the
server's own module (``proofbound_quarto.render_policy``), vendored beside these
scripts -- a real tree in the shipped zip, a symlink in a development checkout.
What this file adds is only the file set: the project is staged exactly as
``upload.build_upload_zip`` would zip it (allowlist, exclusions, output dir
skipped), so a finding in a file the server never sees is not reported and every
finding the server would raise is.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from _upload_plan import iter_upload_files

sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))
from proofbound_quarto.render_policy import (  # noqa: E402
    RENDER_POLICY_REFUSAL,
    Violation,
    find_render_policy_violations,
)

__all__ = ["RENDER_POLICY_REFUSAL", "Violation", "check_render_policy", "format_refusal"]


def check_render_policy(project_path: Path) -> list[Violation]:
    """Every render-policy violation the server would 422 this project for.

    Raises :class:`_upload_plan.SymlinkEscapeError` when a shipped file resolves
    outside the project (reported by the callers as their own blocker).
    """
    with tempfile.TemporaryDirectory(prefix="pb-policy-") as tmp:
        staged = Path(tmp)
        for file_path, rel in iter_upload_files(project_path, None):
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(file_path, dest)
            except OSError:
                continue  # a dangling link or unreadable file: nothing to scan, and the zip step reports it
        return find_render_policy_violations(staged)


def format_refusal(violations: list[Violation]) -> str:
    """Every finding, one per line, under the server's own refusal sentence."""
    lines = [f"  - {v}" for v in violations]
    return "\n".join([RENDER_POLICY_REFUSAL, *lines])
