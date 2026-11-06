#!/usr/bin/env python3
"""Generate chapter content using Anthropic API and Jinja2 prompt templates

This script generates chapter content for a Quarto book project using the
Anthropic Claude API. It reads chapter specifications from outline.yml,
renders Jinja2 prompt templates, and writes generated content to .qmd files.

Usage:
    python generate_chapters.py --project-dir ~/Books/my-book
    python generate_chapters.py --project-dir ~/Books/my-book --chapter 3
    python generate_chapters.py --project-dir ~/Books/my-book --resume
    python generate_chapters.py --project-dir ~/Books/my-book --model claude-opus-4-6

Features:
    - Generates all chapters or specific chapter by number
    - Uses Jinja2 prompt templates from vendored templates
    - Supports --resume to skip already-generated chapters
    - Configurable AI model (default: served by the platform, cached by fetch-prompts)
    - 5-minute timeout for long content generation

Requirements:
    - ANTHROPIC_API_KEY environment variable
    - anthropic Python SDK (pip install anthropic)
    - Jinja2, PyYAML

Dependencies:
    pip install anthropic jinja2 pyyaml
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

import yaml
from anthropic import Anthropic
from jinja2 import Environment, FileSystemLoader

# Section planning is shared with the server (ai-clients) through
# proofbound-portable, vendored beside this script -- a real tree in the shipped
# zip, a symlink in a development checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "_vendor"))

from proofbound_portable.sections import (  # noqa: E402
    compute_section_word_targets,
    normalize_sections,
)

# Prompts are fetched from the platform at runtime (publish_book.py fetch-prompts)
# into a local cache, not vendored in the skill. Override with --prompts-dir.
PROMPTS_DIR = Path.home() / ".proofbound" / "cache" / "prompts"

# The default model is the server's (GET /books/prompts -> script_mode_model),
# cached beside the prompts by publish_book.py fetch-prompts, so a registry
# change reaches the skill without a release. Keep in step with publish_book.py.
SCRIPT_MODE_MODEL_FILE = "script-mode-model.json"


def resolve_model(prompts_dir: Path, model_override: str | None) -> tuple[str, int]:
    """Return (model_id, max_tokens) from the cached server default.

    ``--model`` overrides the id only; the output ceiling still comes from the
    server. Fails if the cache has no default (an older server, or a cache
    fetched before the server served one): re-run fetch-prompts.
    """
    path = prompts_dir / SCRIPT_MODE_MODEL_FILE
    try:
        served = json.loads(path.read_text(encoding="utf-8"))
        model_id, max_tokens = served["model_id"], int(served["max_output_tokens"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(
            f"❌ ERROR: no Script Mode model in {path} ({exc}).\n"
            "Refresh the cache: uv run python scripts/publish_book.py fetch-prompts"
        ) from exc
    return (model_override or model_id), max_tokens


STYLE_GUIDES_FILE = "book-type-style-guides.md"
DEFAULT_WRITING_STYLE = "practical"
DEFAULT_BOOK_TYPE = "nonfiction_practical"


def load_style_guides(prompts_dir: Path) -> dict[str, str]:
    """Parse ``book-type-style-guides.md`` (cached by fetch-prompts) into {book_type: guide}.

    The file is the single source of the valid ``book_type`` keys and their
    structural guidance: each ``## <key>`` heading is followed by its guide text.
    Fails if the cache lacks the file or it holds no guides.
    """
    path = prompts_dir / STYLE_GUIDES_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"❌ ERROR: no book-type style guides in {path} ({exc}).\n"
            "Refresh the cache: uv run python scripts/publish_book.py fetch-prompts"
        ) from exc
    guides = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^## (\w+)\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    }
    if not guides:
        raise SystemExit(f"❌ ERROR: {path} contains no '## <book_type>' sections. Refresh with fetch-prompts.")
    return guides


CRAFT_GUIDE_FILE = "guidance-craft.md"


def load_table_rules(prompts_dir: Path) -> str:
    """Return the "## Tables" section of ``guidance-craft.md`` (cached by fetch-prompts).

    The server renders that section from the same TABLE_RULES its own chapter system
    prompt carries, so Script Mode gets the identical table rules. Fails if the cache
    lacks the file or predates the section (GUIDANCE_VERSION < 1.12.0).
    """
    path = prompts_dir / CRAFT_GUIDE_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"❌ ERROR: no craft guide in {path} ({exc}).\n"
            "Refresh the cache: uv run python scripts/publish_book.py fetch-prompts"
        ) from exc
    m = re.search(r"^## Tables\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    if not m or not m.group(1).strip():
        raise SystemExit(f"❌ ERROR: {path} has no '## Tables' section. Refresh with fetch-prompts.")
    return m.group(1).strip()


def resolve_style(cc_config: dict, guides: dict[str, str]) -> tuple[str, str]:
    """Return (writing_style, style_guide) from cc-config.yml.

    Absent/empty values take the documented defaults; a present-but-wrong value
    fails fast (an unknown ``book_type`` would be rejected by the server too).
    """
    writing_style = cc_config.get("writing_style") or DEFAULT_WRITING_STYLE
    if not isinstance(writing_style, str):
        raise SystemExit(f"❌ ERROR: cc-config.yml writing_style must be a string, got {writing_style!r}")
    book_type = cc_config.get("book_type") or DEFAULT_BOOK_TYPE
    if book_type not in guides:
        raise SystemExit(f"❌ ERROR: cc-config.yml book_type {book_type!r} is not one of: {', '.join(sorted(guides))}")
    return writing_style, guides[book_type]


def response_text(response, where: str) -> str:
    """Return the text of a completed response; raise if it is truncated or empty."""
    if response.stop_reason in ("max_tokens", "refusal"):
        raise RuntimeError(
            f"{where}: response stopped with stop_reason={response.stop_reason!r} (output is incomplete)"
        )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"{where}: response contained no text block (stop_reason={response.stop_reason!r})")
    return text


def check_dependencies():
    """Check that required dependencies are available."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("❌ ERROR: anthropic package not found")
        print()
        print("Install with:")
        print("  pip install anthropic")
        sys.exit(1)

    try:
        from jinja2 import Environment  # noqa: F401
    except ImportError:
        print("❌ ERROR: jinja2 package not found")
        print()
        print("Install with:")
        print("  pip install jinja2")
        sys.exit(1)


def load_project_config(project_path: Path) -> tuple[dict, dict]:
    """Load cc-config.yml and outline.yml.

    Args:
        project_path: Path to project directory

    Returns:
        Tuple of (cc_config dict, outline dict)

    Raises:
        SystemExit: If files don't exist or can't be parsed
    """
    cc_config_file = project_path / "cc-config.yml"
    outline_file = project_path / "outline.yml"

    if not cc_config_file.exists():
        print(f"❌ ERROR: cc-config.yml not found in {project_path}")
        sys.exit(1)

    if not outline_file.exists():
        print(f"❌ ERROR: outline.yml not found in {project_path}")
        sys.exit(1)

    try:
        cc_config = yaml.safe_load(cc_config_file.read_text())
    except yaml.YAMLError as e:
        print(f"❌ ERROR: Failed to parse cc-config.yml: {e}")
        sys.exit(1)

    try:
        outline = yaml.safe_load(outline_file.read_text())
    except yaml.YAMLError as e:
        print(f"❌ ERROR: Failed to parse outline.yml: {e}")
        sys.exit(1)

    return cc_config, outline


def setup_jinja_environment(prompts_dir: Path) -> Environment:
    """Setup Jinja2 environment with prompt templates.

    Args:
        prompts_dir: Root prompts dir (contains chapter-generation/).

    Returns:
        Configured Jinja2 Environment

    Raises:
        SystemExit: If prompts directory doesn't exist
    """
    prompts_chapter_dir = prompts_dir / "chapter-generation"

    if not prompts_chapter_dir.exists():
        print(f"❌ ERROR: Prompts directory not found: {prompts_chapter_dir}")
        print()
        print("Prompts are fetched from the platform, not vendored. Run:")
        print("  uv run python scripts/publish_book.py fetch-prompts")
        print(f"to populate the cache ({PROMPTS_DIR}), or pass --prompts-dir.")
        sys.exit(1)

    env = Environment(loader=FileSystemLoader(prompts_chapter_dir))

    return env


def chapter_section_beats(chapter: dict) -> list[dict]:
    """Return a chapter's section beats via the server's ``normalize_sections``.

    Script Mode's one addition: a chapter with neither ``sections`` nor legacy
    ``chapter_ideas`` gets a single beat built from its title and description,
    so a sparse hand-written outline still generates (the server fails fast on
    zero beats instead).
    """
    return normalize_sections(chapter) or [
        {
            "heading": chapter["chapter_title"],
            "summary": chapter.get("chapter_description", ""),
        }
    ]


def generate_chapter(
    client: Anthropic,
    env: Environment,
    project_path: Path,
    cc_config: dict,
    outline: dict,
    chapter: dict,
    model: str,
    max_tokens: int,
    guides: dict[str, str],
    table_rules: str,
) -> tuple[str, int]:
    """Generate content for a single chapter, one section per beat.

    Args:
        client: Anthropic API client
        env: Jinja2 environment
        project_path: Path to project directory
        cc_config: cc-config.yml data
        outline: outline.yml data
        chapter: Chapter specification dict
        model: AI model ID
        max_tokens: Output-token ceiling per section call
        guides: {book_type: style guide} parsed from the cached style-guides file
        table_rules: the craft guide's Tables section (load_table_rules)

    Returns:
        Tuple of (generated_content, tokens_used)
    """
    # Load templates
    system_template = env.get_template("system.jinja2")
    section_template = env.get_template("section.jinja2")

    # Calculate target words per chapter, then per section beat.
    total_chapters = len(outline["chapters"])
    target_words = outline["book"]["target_length"] // total_chapters
    beats = chapter_section_beats(chapter)
    word_targets = compute_section_word_targets(beats, target_words)

    writing_style, style_guide = resolve_style(cc_config, guides)

    # System prompt carries the whole beat spine (system.jinja2 iterates `sections`).
    system_prompt = system_template.render(
        writing_style=writing_style,
        style_guide=style_guide,
        table_rules=table_rules,
        title=cc_config["title"],
        author=cc_config["author"],
        topic=outline["book"]["description"],
        chapter_context=cc_config.get("source_materials", ""),
        chapter_title=chapter["chapter_title"],
        chapter_description=chapter["chapter_description"],
        sections=beats,
        target_words=target_words,
    )

    # Write one section per beat, threading prior-section context for continuity.
    parts: list[str] = []
    previous_sections: list[dict] = []
    tokens_used = 0
    for idx, beat in enumerate(beats):
        user_prompt = section_template.render(
            title=cc_config["title"],
            author=cc_config["author"],
            topic=outline["book"]["description"],
            chapter_title=chapter["chapter_title"],
            chapter_description=chapter["chapter_description"],
            section_number=idx + 1,
            total_sections=len(beats),
            section_heading=beat["heading"],
            section_summary=beat.get("summary", ""),
            section_key_points=beat.get("key_points", []),
            section_word_target=word_targets[idx],
            previous_sections=previous_sections,
        )

        # Call Anthropic API (matches ai-clients pattern)
        print(f"   Calling Anthropic API ({model}) — section {idx + 1}/{len(beats)}...")
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # Marker only: the prefix caches only above the model's minimum size.
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_prompt}],
        )

        parts.append(
            response_text(response, f"chapter {chapter.get('number', '?')} section {idx + 1} ({beat['heading']})")
        )
        u = response.usage
        tokens_used += (
            u.input_tokens
            + u.output_tokens
            + (getattr(u, "cache_creation_input_tokens", 0) or 0)
            + (getattr(u, "cache_read_input_tokens", 0) or 0)
        )
        # section.jinja2's continuity block reads prev.topic / prev.summary.
        previous_sections.append({"topic": beat["heading"], "summary": beat.get("summary", "")})

    return "\n\n".join(parts), tokens_used


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Generate chapter content using Anthropic API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Generate all chapters
    python generate_chapters.py --project-dir ~/Books/my-book

    # Generate specific chapter
    python generate_chapters.py --project-dir ~/Books/my-book --chapter 3

    # Resume generation (skip chapters with content)
    python generate_chapters.py --project-dir ~/Books/my-book --resume

    # Use different model
    python generate_chapters.py --project-dir ~/Books/my-book --model claude-opus-4-6

Environment:
    ANTHROPIC_API_KEY - Required. Set your Anthropic API key.

Exit codes:
    0 - Success
    1 - Error (missing dependencies, invalid config, API error)
        """,
    )

    parser.add_argument(
        "--project-dir",
        required=True,
        help="Quarto project directory path",
    )
    parser.add_argument(
        "--chapter",
        type=int,
        help="Specific chapter number to generate (default: all)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip chapters that already have content (>100 chars)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="AI model to use (default: the platform's, cached by fetch-prompts)",
    )
    parser.add_argument(
        "--prompts-dir",
        type=Path,
        default=PROMPTS_DIR,
        help=f"Root prompts dir, contains chapter-generation/ (default: {PROMPTS_DIR})",
    )

    args = parser.parse_args()

    # Check dependencies
    check_dependencies()

    # Check API key
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("❌ ERROR: ANTHROPIC_API_KEY environment variable not set")
        print()
        print("Set your API key:")
        print("  export ANTHROPIC_API_KEY=sk-ant-...")
        print()
        print("Or add to your .env file:")
        print("  ANTHROPIC_API_KEY=sk-ant-...")
        return 1

    project_path = Path(args.project_dir).expanduser().resolve()

    if not project_path.exists():
        print(f"❌ ERROR: Project directory does not exist: {project_path}")
        return 1

    print(f"📝 Generating chapters for: {project_path.name}")
    print(f"   Project: {project_path}")
    model, max_tokens = resolve_model(args.prompts_dir.expanduser(), args.model)
    print(f"   Model: {model}")
    print()

    # Load project configuration
    print("📋 Loading project configuration...")
    cc_config, outline = load_project_config(project_path)
    print(f"   Title: {cc_config.get('title', 'N/A')}")
    print(f"   Chapters: {len(outline.get('chapters', []))}")
    print()

    # Setup Jinja2 environment
    env = setup_jinja_environment(args.prompts_dir.expanduser())
    guides = load_style_guides(args.prompts_dir.expanduser())
    resolve_style(cc_config, guides)  # fail before any API spend
    table_rules = load_table_rules(args.prompts_dir.expanduser())

    # Initialize Anthropic client with 5-minute timeout (same as ai-clients)
    client = Anthropic(api_key=api_key, timeout=300.0)

    # Determine chapters to generate
    chapters = outline["chapters"]
    if args.chapter:
        chapters = [ch for ch in chapters if ch["number"] == args.chapter]
        if not chapters:
            print(f"❌ ERROR: Chapter {args.chapter} not found in outline.yml")
            return 1

    # Generate chapters
    chapters_dir = project_path / "chapters"
    chapters_dir.mkdir(exist_ok=True)

    total_tokens = 0
    generated_count = 0
    skipped_count = 0

    for chapter in chapters:
        chapter_num = chapter["number"]
        chapter_title = chapter["chapter_title"]
        chapter_file = chapters_dir / f"chapter_{chapter_num}.qmd"

        # Check if --resume and file has content
        if args.resume and chapter_file.exists():
            content = chapter_file.read_text().strip()
            if len(content) > 100:  # More than just title
                print(f"⏭  Skipping chapter {chapter_num} (already has content: {len(content)} chars)")
                skipped_count += 1
                continue

        print(f"📝 Generating chapter {chapter_num}: {chapter_title}")

        try:
            content, tokens_used = generate_chapter(
                client, env, project_path, cc_config, outline, chapter, model, max_tokens, guides, table_rules
            )

            # Write to .qmd file
            chapter_file.write_text(content)

            total_tokens += tokens_used
            generated_count += 1

            print(f"   ✓ Wrote {len(content)} chars ({tokens_used:,} tokens)")
            print()

        except Exception as e:
            print(f"   ❌ Error generating chapter {chapter_num}: {e}")
            print()
            return 1

    # Summary
    print("✓ Chapter generation complete!")
    print()
    print("📊 Summary:")
    print(f"   Generated: {generated_count} chapter(s)")
    if skipped_count > 0:
        print(f"   Skipped: {skipped_count} chapter(s) (--resume)")
    print(f"   Total tokens: {total_tokens:,}")
    print()
    print("Next steps:")
    print(f"  1. Review chapters in: {chapters_dir}")
    print("  2. Publish: uv run python scripts/publish_book.py publish ...")
    print("     (the platform renders + persists the book server-side)")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
