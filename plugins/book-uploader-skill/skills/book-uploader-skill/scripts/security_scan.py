#!/usr/bin/env python3
"""Scan a folder for files that probably shouldn't end up in R2.

The upload script uses a blacklist approach (see ``proofbound_portable.upload.DEFAULT_EXCLUDE_DIRS``)
and includes everything else — so .env files, credentials, and large media
files leak in. This scan catches them before upload.

Usage:
    uv run python scripts/security_scan.py --project-path /path/to/folder
    uv run python scripts/security_scan.py --project-path /path/to/folder --json
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _quarto_output import is_output_path, output_dir_names  # noqa: E402

# The exclusion/author/trim policy is shared with the server through
# proofbound-portable, vendored beside these scripts -- a real tree in the shipped
# zip, a symlink in a development checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))

from proofbound_portable.upload import DEFAULT_EXCLUDE_DIRS  # noqa: E402

# Top-level dir names that are normal in a ProofBound book project; anything
# else over the threshold is worth surfacing. Keep in sync with the same
# constant in validate_folder.py.
BASE_KNOWN_TOP_DIRS = {"chapters", "covers", "_book", "_freeze", ".git", ".quarto", "docs"}
UNEXPECTED_DIR_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB

# Patterns that suggest a credential or env file.
ENV_PATTERNS = [".env", ".env.*"]
CREDENTIAL_PATTERNS = ["*credential*", "*secret*", "*.pem", "*.key", "id_rsa*", "id_ed25519*", "*.p12", "*.pfx"]

LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024  # 10 MB


def matches_any(name: str, patterns: list[str]) -> bool:
    name_lower = name.lower()
    return any(fnmatch.fnmatch(name_lower, p.lower()) for p in patterns)


def scan(project_path: Path) -> list[dict]:
    findings: list[dict] = []
    top_dir_bytes: dict[str, int] = {}
    # A custom project.output-dir is build output, pruned like _book/ and docs/
    # — upload never ships it, so a credential in there isn't leaving the machine.
    output_names = output_dir_names(project_path)

    import os

    for dirpath, dirnames, filenames in os.walk(project_path):
        # Prune excluded dirs in place
        root_rel = Path(dirpath).relative_to(project_path)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in DEFAULT_EXCLUDE_DIRS and not is_output_path((*root_rel.parts, d), output_names)
        ]

        for fname in filenames:
            full = Path(dirpath) / fname
            try:
                size = full.stat().st_size
            except OSError:
                continue

            rel = str(full.relative_to(project_path))
            rel_parts = full.relative_to(project_path).parts
            if len(rel_parts) > 1:
                top_dir_bytes[rel_parts[0]] = top_dir_bytes.get(rel_parts[0], 0) + size

            # Env files
            if matches_any(fname, ENV_PATTERNS):
                findings.append({"path": rel, "reason": "env-file", "size_bytes": size})
                continue

            # Credential-shaped names
            if matches_any(fname, CREDENTIAL_PATTERNS):
                findings.append({"path": rel, "reason": "credential-name", "size_bytes": size})
                continue

            # Large files (informational, not necessarily a leak)
            if size >= LARGE_FILE_THRESHOLD_BYTES:
                findings.append(
                    {
                        "path": rel,
                        "reason": "large-file",
                        "size_bytes": size,
                        "size_mb": round(size / (1024 * 1024), 1),
                    }
                )

    known_top_dirs = BASE_KNOWN_TOP_DIRS | {n.split("/", 1)[0] for n in output_dir_names(project_path)}
    for name, size in top_dir_bytes.items():
        if name in known_top_dirs:
            continue
        if size < UNEXPECTED_DIR_THRESHOLD_BYTES:
            continue
        findings.append(
            {
                "path": f"{name}/",
                "reason": "unexpected-large-dir",
                "size_bytes": size,
                "size_mb": round(size / (1024 * 1024), 1),
            }
        )

    return findings


def format_text(findings: list[dict]) -> str:
    if not findings:
        return "✓ No suspicious files found."

    lines = [f"⚠️  Found {len(findings)} suspicious file(s):", ""]
    for f in findings:
        if f["reason"] in ("large-file", "unexpected-large-dir"):
            lines.append(f"  • {f['path']}  ({f['size_mb']} MB, {f['reason']})")
        else:
            lines.append(f"  • {f['path']}  ({f['reason']})")
    lines.append("")
    lines.append("Move or delete these before upload, or confirm 'upload anyway' to include them.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a folder for files that may not belong in upload")
    parser.add_argument("--project-path", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if not args.project_path.is_dir():
        print(f"❌ Not a directory: {args.project_path}", file=sys.stderr)
        return 2

    findings = scan(args.project_path.resolve())

    if args.json:
        print(json.dumps({"findings": findings}, indent=2))
    else:
        print(format_text(findings))

    # Exit 1 only on credential/env findings; size-based findings are informational.
    blocking = [f for f in findings if f["reason"] in ("env-file", "credential-name")]
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
