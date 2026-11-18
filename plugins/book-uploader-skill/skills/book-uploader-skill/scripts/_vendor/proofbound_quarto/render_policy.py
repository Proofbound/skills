"""What a Quarto project may ask the render host to do (bead pc62s).

Quarto treats a project's configuration as instructions: ``project.pre-render``
runs a command, ``filters`` runs a program, ``{{< env >}}`` prints a variable,
``{{< include ../x >}}`` reads a file, a path key can point anywhere on disk. A
client-supplied project (``/books/render[-staged]``, ``/books/upload``) and
caller- or model-authored chapter text (compose, ``/projects``, customization)
both reach ``quarto render``, so this module decides what a project may contain.

Two kinds of finding, with two reactions:

* **Configuration** (:func:`find_config_violations`) — execution hooks, foreign
  filters, non-allowlisted engines, path values that leave the project. Always a
  rejection: stripping would silently change the book.
* **Content** (:func:`find_content_violations`) — disallowed shortcodes and
  executable code cells in manuscript text. Rejected at client intake, where the
  author can fix them, and *neutralized* (:func:`neutralize_caller_content`) at
  render time, where the text may be model-written: the shortcode is printed
  literally and the cell becomes a plain code block.

:func:`find_render_policy_violations` is the intake check (both kinds);
:func:`enforce_render_policy` is the render-time backstop ``render_gate.run_quarto``
applies to every render, so a stored project that predates intake checks, or a
path that never ran them, cannot slip through.

Raw TeX is deliberately out of scope: client projects legitimately carry it and
TeX file reads cannot be refused from inside TeX Live 2026 (see
``services.render_sandbox``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from proofbound_quarto.config import QuartoOutputDirError, resolve_output_dir
from proofbound_quarto.managed_filters import (
    MANAGED_FILTER_MARKER,
    PB_BIDI_FILTER_PATH,
    PB_ISOTOPE_FILTER_PATH,
    PB_TABLE_HEAD_FILTER_PATH,
    PB_TABLE_WIDTH_FILTER_PATH,
    PDF_ENGINES,
)

# --- policy tables --------------------------------------------------------------

#: Keys that make Quarto, Pandoc or TeX run something or load code. Rejected
#: wherever they appear in a project's configuration or front matter.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "pre-render",
        "post-render",
        "engines",
        "jupyter",
        "knitr",
        "julia",
        "shortcodes",
        "ipynb-filters",
        "defaults",
        "data-dir",
        "pdf-engine-opt",
        "pdf-engine-opts",
        "latex-makeindex",
        "latex-makeindex-opts",
        "latex-tlmgr-opts",
        "latex-input-paths",
        "latex-tinytex",
    }
)

#: Engines a render may use (``managed_filters.PDF_ENGINES``). LuaLaTeX runs Lua,
#: latexmk reads Perl ``latexmkrc`` files, tectonic fetches over the network.
ALLOWED_PDF_ENGINES: frozenset[str] = frozenset(PDF_ENGINES)

#: ``engine:`` values that execute nothing.
ALLOWED_ENGINES: frozenset[str] = frozenset({"markdown"})

#: ``cite-method`` values; natbib/biblatex hand the bibliography to bibtex/biber.
ALLOWED_CITE_METHODS: frozenset[str] = frozenset({"citeproc"})

#: The filter files ProofBound itself writes and registers (``pdf_headers``).
MANAGED_FILTER_PATHS: frozenset[str] = frozenset(
    {
        PB_BIDI_FILTER_PATH,
        PB_TABLE_HEAD_FILTER_PATH,
        PB_TABLE_WIDTH_FILTER_PATH,
        PB_ISOTOPE_FILTER_PATH,
    }
)

#: Keys whose values name files or directories. Derived from the Quarto 1.10
#: schema's ``path``-typed options (``test_render_policy`` re-derives it from the
#: installed Quarto) plus the hidden Pandoc pass-throughs and ProofBound-relevant
#: book keys. Every string under one of these must stay inside the project.
PATH_KEYS: frozenset[str] = frozenset(
    {
        "abbreviations",
        "bibliography",
        "biblio-config",
        "brand",
        "citation-abbreviations",
        "cover-image",
        "css",
        "csl",
        "epub-cover-image",
        "epub-fonts",
        "epub-metadata",
        "epub-subdirectory",
        "execute-dir",
        "extract-media",
        "favicon",
        "file",
        "files",
        "header",
        "highlight-style",
        "image",
        "include-after",
        "include-after-body",
        "include-before",
        "include-before-body",
        "include-in-header",
        "input-file",
        "input-files",
        "language",
        "latex-output-dir",
        "lib-dir",
        "log-file",
        "logo",
        "metadata-file",
        "metadata-files",
        "output-basename",
        "output-dir",
        "output-file",
        "parallax-background-image",
        "path",
        "reference-doc",
        "render",
        "resource-path",
        "resources",
        "revealjs-url",
        "syntax-definition",
        "syntax-definitions",
        "syntax-highlighting",
        "template",
        "template-partials",
        "theme",
        "titlegraphic",
    }
)

#: Book inputs: every file entry must be a project file the policy scans (not
#: outside, not hidden, not in the output directory). Part titles share these
#: lists and pass, since a title is not absolute, a URL or ``..``.
BOOK_INPUT_KEYS: frozenset[str] = frozenset({"chapters", "appendices"})

#: Under a path key, these children carry text or a stage, not a path.
_NON_PATH_CHILD_KEYS = frozenset({"text", "at"})

#: Shortcodes that only format or read project-local data.
ALLOWED_SHORTCODES: frozenset[str] = frozenset(
    {"pagebreak", "kbd", "meta", "var", "lipsum", "placeholder", "version"}
)

#: ``include`` is allowed only for a manuscript file inside the project.
_INCLUDABLE_SUFFIXES = (".qmd", ".md")

_MANUSCRIPT_SUFFIXES = (".qmd", ".md")

# --- result types ---------------------------------------------------------------


@dataclass(frozen=True)
class Violation:
    """One thing a project asked for that the render host will not do."""

    file: str
    key: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"file": self.file, "key": self.key, "reason": self.reason}

    def __str__(self) -> str:
        return f"{self.file}: {self.key}: {self.reason}"


#: The sentence both the 422 body and the book-uploader skill lead with.
RENDER_POLICY_REFUSAL = (
    "This project asks the renderer to run commands, load code or read "
    "files outside the project. Remove these and upload again:"
)


class RenderPolicyError(ValueError):
    """A project carries configuration the render host refuses to act on."""

    error_code = "render_config_forbidden"

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations = tuple(violations)
        super().__init__(f"Project configuration not allowed: {self.summary()}")

    def summary(self, limit: int = 5) -> str:
        """The first *limit* findings as one line, with a count of the rest."""
        shown = "; ".join(str(v) for v in self.violations[:limit])
        more = len(self.violations) - limit
        return shown + (f" (and {more} more)" if more > 0 else "")

    def detail(self) -> dict:
        """The structured 422 body: a stable slug, prose, and every finding.

        ``message`` names the findings itself, so a client that only relays the
        prose (the MCP connector summarizes ``error (message)``) still tells the
        author what to remove.
        """
        return {
            "error": self.error_code,
            "message": f"{RENDER_POLICY_REFUSAL} {self.summary()}",
            "violations": [v.as_dict() for v in self.violations],
        }


# --- file discovery -------------------------------------------------------------


def _rel(project_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(project_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _output_dir(project_dir: Path) -> Path | None:
    try:
        resolved = resolve_output_dir(project_dir).resolve()
    except (QuartoOutputDirError, OSError, ValueError):
        return None
    # ``output-dir: .`` would otherwise hide every file from the scan.
    return None if resolved == project_dir.resolve() else resolved


def _project_files(project_dir: Path) -> Iterator[Path]:
    """Every non-dot file outside the rendered-output directory."""
    output_dir = _output_dir(project_dir)
    for path in sorted(project_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(project_dir).parts
        if any(part.startswith(".") for part in rel_parts):
            continue
        if output_dir is not None and output_dir in path.resolve().parents:
            continue
        yield path


def _is_root_config(rel: PurePosixPath) -> bool:
    name = rel.name
    if len(rel.parts) != 1:
        return False
    if name in {"_quarto.yml", "_quarto.yaml", "_brand.yml", "_brand.yaml"}:
        return True
    return name.startswith("_quarto-") and name.endswith((".yml", ".yaml"))


def _config_files(project_dir: Path, files: Sequence[Path]) -> list[Path]:
    """Every YAML file Quarto merges into a render's options."""
    out = []
    for path in files:
        rel = PurePosixPath(path.relative_to(project_dir).as_posix())
        if _is_root_config(rel) or rel.name in {"_metadata.yml", "_metadata.yaml"}:
            out.append(path)
    return out


# --- markdown scanning ----------------------------------------------------------

# Quarto finds cells behind any run of spaces, tabs and blockquote markers
# (``breakQuartoMd``), so a fence is matched the same way, not CommonMark's 0-3.
_FENCE_RE = re.compile(r"^[\t >]*(`{3,}|~{3,})(.*)$")
_EXECUTABLE_INFO_RE = re.compile(r"^\s*\{\s*([A-Za-z][\w-]*)")


@dataclass(frozen=True)
class _Fence:
    line_index: int
    marker: str
    info: str


def _top_level_fences(lines: Sequence[str]) -> tuple[list[_Fence], set[int]]:
    """Opening fences at nesting depth zero, plus every line inside any fence."""
    openers: list[_Fence] = []
    inside: set[int] = set()
    open_marker: str | None = None
    for index, line in enumerate(lines):
        match = _FENCE_RE.match(line)
        if open_marker is None:
            if match:
                open_marker = match.group(1)
                openers.append(_Fence(index, match.group(1), match.group(2)))
                inside.add(index)
            continue
        inside.add(index)
        if (
            match
            and match.group(1)[0] == open_marker[0]
            and len(match.group(1)) >= len(open_marker)
            and not match.group(2).strip()
        ):
            open_marker = None
    return openers, inside


def _executable_cell_language(info: str) -> str | None:
    match = _EXECUTABLE_INFO_RE.match(info)
    return match.group(1) if match else None


_YAML_BLOCK_RE = re.compile(
    r"^---[ \t]*\n(?![ \t]*\n)(.*?)^(?:---|\.\.\.)[ \t]*$", re.MULTILINE | re.DOTALL
)


def _normalize_newlines(text: str) -> str:
    """LF line endings and no BOM, as Pandoc sees the document."""
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


def _yaml_blocks(text: str) -> Iterator[tuple[str, bool]]:
    """Pandoc YAML metadata blocks outside code fences: ``(body, is_front_matter)``."""
    lines = _normalize_newlines(text).split("\n")
    _, inside = _top_level_fences(lines)
    visible = "\n".join("" if i in inside else line for i, line in enumerate(lines))
    for match in _YAML_BLOCK_RE.finditer(visible):
        yield match.group(1), match.start() == 0


# Opening ``{{<`` not preceded by ``{`` (``{{{<`` is Quarto's literal escape);
# the first ``>}}`` closes it, as in Quarto's own scanner.
_SHORTCODE_RE = re.compile(r"(?<!\{)\{\{<(.*?)>\}\}", re.DOTALL)


@dataclass(frozen=True)
class _Shortcode:
    start: int
    end: int
    name: str
    args: list[str]


def _shortcodes(text: str) -> Iterator[_Shortcode]:
    for match in _SHORTCODE_RE.finditer(text):
        tokens = _split_args(match.group(1))
        name = tokens[0] if tokens else ""
        yield _Shortcode(match.start(), match.end(), name, tokens[1:])


def _split_args(raw: str) -> list[str]:
    tokens: list[str] = []
    for match in re.finditer(r'"([^"]*)"|\'([^\']*)\'|(\S+)', raw):
        tokens.append(next(group for group in match.groups() if group is not None))
    return tokens


def _include_target_ok(project_dir: Path, source: Path, target: str) -> bool:
    if not target or _path_problem(target) is not None:
        return False
    resolved = (source.parent / target).resolve()
    root = project_dir.resolve()
    if root not in resolved.parents:
        return False
    # Dot-prefixed files and the rendered-output directory are never scanned
    # (_project_files), so neither may be pulled into the manuscript.
    if any(part.startswith(".") for part in resolved.relative_to(root).parts):
        return False
    output_dir = _output_dir(project_dir)
    if output_dir is not None and output_dir in resolved.parents:
        return False
    return resolved.suffix.lower() in _INCLUDABLE_SUFFIXES


def _shortcode_problem(project_dir: Path, source: Path, code: _Shortcode) -> str | None:
    if code.name in ALLOWED_SHORTCODES:
        return None
    if code.name == "include":
        target = code.args[0] if code.args else ""
        if _include_target_ok(project_dir, source, target):
            return None
        return f"include of {target!r} leaves the project or is not a .qmd/.md file"
    return f"shortcode {code.name!r} is not allowed"


# --- configuration checks -------------------------------------------------------

# A URL Pandoc would fetch. Not a bare ``word:`` prefix: a part title such as
# "Prologue: The Start" sits under the same keys and is not a path.
_URL_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://|data:|file:)", re.I)


def _path_problem(value: str) -> str | None:
    """Why *value* is not a project-relative path, or ``None`` when it is."""
    if "\x00" in value:
        return "contains a NUL byte"
    stripped = value.strip()
    if _URL_SCHEME_RE.match(stripped):
        return "is a URL; ship the file inside the project instead"
    if stripped.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:", stripped):
        return "is an absolute path"
    if ".." in re.split(r"[\\/]+", stripped):
        return "leaves the project directory"
    return None


def _book_input_problem(project_dir: Path, value: str) -> str | None:
    """Why a ``book.chapters``/``appendices`` entry is not a scanned project file."""
    problem = _path_problem(value)
    if problem:
        return problem
    parts = PurePosixPath(value.strip().replace("\\", "/")).parts
    if any(part.startswith(".") for part in parts):
        return "is a hidden file, which is never checked"
    output_dir = _output_dir(project_dir)
    if output_dir is not None and output_dir in (project_dir / value).resolve().parents:
        return "is inside the rendered-output directory, which is never checked"
    return None


def _path_values(value: object, key_path: str) -> Iterator[tuple[str, str]]:
    """Every string under a path key, with the dotted key it sits at."""
    if isinstance(value, str):
        yield key_path, value
    elif isinstance(value, list):
        for item in value:
            yield from _path_values(item, key_path)
    elif isinstance(value, dict):
        for child_key, child in value.items():
            if str(child_key) in _NON_PATH_CHILD_KEYS:
                continue
            yield from _path_values(child, f"{key_path}.{child_key}")


def _filter_entry_path(entry: object) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("path"), str):
        return entry["path"]
    return None


def _managed_filter_problem(project_dir: Path, path: str) -> str | None:
    target = project_dir / path
    if not target.is_file():
        return None  # Quarto fails the render on a missing filter by itself.
    try:
        first = target.read_text(encoding="utf-8").split("\n", 1)[0]
    except (OSError, UnicodeDecodeError):
        first = ""
    if not first.startswith(MANAGED_FILTER_MARKER):
        return f"{path} is not a ProofBound-managed filter"
    return None


def _config_problems(
    project_dir: Path, node: object, key_path: str = ""
) -> Iterator[tuple[str, str]]:
    """``(dotted key, reason)`` for every forbidden setting under *node*."""
    if isinstance(node, list):
        for item in node:
            yield from _config_problems(project_dir, item, key_path)
        return
    if not isinstance(node, dict):
        return
    for raw_key, value in node.items():
        key = str(raw_key)
        dotted = f"{key_path}.{key}" if key_path else key
        if key in FORBIDDEN_KEYS:
            yield dotted, "runs commands or loads code on the render host"
            continue
        if key == "filters":
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                path = _filter_entry_path(entry)
                if path not in MANAGED_FILTER_PATHS:
                    yield dotted, f"filter {path or entry!r} is not allowed"
                else:
                    problem = _managed_filter_problem(project_dir, path)
                    if problem:
                        yield dotted, problem
            continue
        if key == "pdf-engine":
            if not isinstance(value, str) or value.strip() not in ALLOWED_PDF_ENGINES:
                yield (
                    dotted,
                    (
                        f"pdf-engine {value!r} is not allowed; use "
                        f"{' or '.join(sorted(ALLOWED_PDF_ENGINES))}"
                    ),
                )
            continue
        if key == "engine":
            if not isinstance(value, str) or value.strip() not in ALLOWED_ENGINES:
                yield dotted, f"engine {value!r} executes code on the render host"
            continue
        if key == "cite-method":
            if not isinstance(value, str) or value.strip() not in ALLOWED_CITE_METHODS:
                yield dotted, f"cite-method {value!r} is not allowed; use citeproc"
            continue
        if key in BOOK_INPUT_KEYS:
            for where, path_value in _path_values(value, dotted):
                problem = _book_input_problem(project_dir, path_value)
                if problem:
                    yield where, f"{path_value!r} {problem}"
            continue
        if key in PATH_KEYS:
            for where, path_value in _path_values(value, dotted):
                problem = _path_problem(path_value)
                if problem:
                    yield where, f"{path_value!r} {problem}"
            continue
        yield from _config_problems(project_dir, value, dotted)


def _load_yaml(text: str) -> tuple[object, str | None]:
    try:
        return yaml.safe_load(text), None
    except yaml.YAMLError as exc:
        return None, f"is not valid YAML ({exc.__class__.__name__})"


def _metadata_file_targets(
    project_dir: Path, source: Path, config: object
) -> list[Path]:
    """Files a config pulls in through ``metadata-files``; Quarto merges them too."""
    if not isinstance(config, dict):
        return []
    out = []
    for key in ("metadata-files", "metadata-file"):
        raw = config.get(key)
        entries = raw if isinstance(raw, list) else [raw]
        for entry in entries:
            if isinstance(entry, str) and _path_problem(entry) is None:
                target = (source.parent / entry).resolve()
                if target.is_file() and project_dir.resolve() in target.parents:
                    out.append(target)
    return out


def _front_matter_blocks(path: Path) -> Iterator[tuple[object, str | None, bool]]:
    """``(data, yaml error, is_front_matter)`` for each YAML block in a manuscript."""
    text = path.read_text(encoding="utf-8", errors="replace")
    for body, is_front_matter in _yaml_blocks(text):
        data, error = _load_yaml(body)
        yield data, error, is_front_matter


def _config_closure(project_dir: Path, files: Sequence[Path]) -> list[Path]:
    """Every YAML file Quarto merges: root/directory configs plus their
    ``metadata-files`` targets (and the targets' targets), wherever they sit."""
    pending = list(_config_files(project_dir, files))
    for path in files:
        if path.suffix.lower() in _MANUSCRIPT_SUFFIXES:
            for data, error, _ in _front_matter_blocks(path):
                if error is None:
                    pending += _metadata_file_targets(project_dir, path, data)
    closure: list[Path] = []
    seen: set[Path] = set()
    while pending:
        path = pending.pop(0)
        if path.resolve() in seen:
            continue
        seen.add(path.resolve())
        closure.append(path)
        config, error = _load_yaml(path.read_text(encoding="utf-8", errors="replace"))
        if error is None:
            pending += _metadata_file_targets(project_dir, path, config)
    return closure


def find_config_violations(project_dir: Path) -> list[Violation]:
    """Every configuration setting in *project_dir* the render host refuses."""
    project_dir = Path(project_dir)
    files = list(_project_files(project_dir))
    violations: list[Violation] = []

    if (project_dir / "_extensions").is_dir():
        violations.append(
            Violation(
                "_extensions/",
                "_extensions",
                "Quarto extensions load filters and shortcodes; remove the directory",
            )
        )

    def check(rel: str, config: object) -> None:
        violations.extend(
            Violation(rel, key, reason)
            for key, reason in _config_problems(project_dir, config)
        )

    for path in _config_closure(project_dir, files):
        rel = _rel(project_dir, path)
        config, error = _load_yaml(path.read_text(encoding="utf-8", errors="replace"))
        if error:
            violations.append(Violation(rel, "(file)", error))
        else:
            check(rel, config)

    for path in files:
        if path.suffix.lower() not in _MANUSCRIPT_SUFFIXES:
            continue
        rel = _rel(project_dir, path)
        for data, error, is_front_matter in _front_matter_blocks(path):
            if error is None:
                check(rel, data)
            elif is_front_matter:
                violations.append(Violation(rel, "(front matter)", error))
    return violations


# --- content checks and neutralization ------------------------------------------


def _text_files(project_dir: Path) -> list[Path]:
    """Files whose text Quarto reads for shortcodes: manuscripts and every config
    in the merge closure (``metadata-files`` targets included)."""
    files = list(_project_files(project_dir))
    manuscripts = [p for p in files if p.suffix.lower() in _MANUSCRIPT_SUFFIXES]
    configs = [p for p in _config_closure(project_dir, files) if p not in manuscripts]
    return manuscripts + configs


def find_content_violations(project_dir: Path) -> list[Violation]:
    """Disallowed shortcodes and executable code cells in *project_dir*."""
    project_dir = Path(project_dir)
    violations: list[Violation] = []
    for path in _text_files(project_dir):
        rel = _rel(project_dir, path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for code in _shortcodes(text):
            problem = _shortcode_problem(project_dir, path, code)
            if problem:
                violations.append(Violation(rel, "{{< " + code.name + " >}}", problem))
        if path.suffix.lower() not in _MANUSCRIPT_SUFFIXES:
            continue
        openers, _ = _top_level_fences(_normalize_newlines(text).split("\n"))
        for fence in openers:
            language = _executable_cell_language(fence.info)
            if language:
                violations.append(
                    Violation(
                        rel,
                        "{" + language + "}",
                        f"line {fence.line_index + 1}: executable code cell; "
                        f"use ```{language} (no braces) to show code",
                    )
                )
    return violations


def find_render_policy_violations(project_dir: Path) -> list[Violation]:
    """Intake check for a client-supplied project: configuration and content."""
    return find_config_violations(project_dir) + find_content_violations(project_dir)


def _escape_shortcodes(project_dir: Path, path: Path, text: str) -> str:
    out: list[str] = []
    last = 0
    for code in _shortcodes(text):
        if _shortcode_problem(project_dir, path, code) is None:
            continue
        span = text[code.start : code.end]
        out.append(text[last : code.start])
        out.append("{" + span + "}")
        last = code.end
    out.append(text[last:])
    return "".join(out)


def _neutralize_cells(text: str) -> str:
    lines = text.split("\n")
    openers, _ = _top_level_fences(lines)
    for fence in openers:
        language = _executable_cell_language(fence.info)
        if language:
            line = lines[fence.line_index]
            prefix = line[: len(line) - len(line.lstrip("\t >"))]
            lines[fence.line_index] = f"{prefix}{fence.marker}{{.{language}}}"
    return "\n".join(lines)


def neutralize_caller_content(project_dir: Path) -> list[str]:
    """Make disallowed shortcodes and code cells inert, in place; return touched files.

    A disallowed ``{{< x >}}`` becomes Quarto's literal form ``{{{< x >}}}`` (the
    reader sees what was written) and an executable cell's fence becomes a plain
    ``{.lang}`` code block. Idempotent. Allowed shortcodes, raw TeX (``\\index``
    markers included), citations and images are left exactly as they are.
    """
    project_dir = Path(project_dir)
    touched: list[str] = []
    for path in _text_files(project_dir):
        original = path.read_text(encoding="utf-8", errors="surrogateescape")
        text = _escape_shortcodes(project_dir, path, original)
        if path.suffix.lower() in _MANUSCRIPT_SUFFIXES:
            text = _neutralize_cells(text)
        if text != original:
            path.write_text(text, encoding="utf-8", errors="surrogateescape")
            touched.append(_rel(project_dir, path))
    return touched


_PDF_ENGINE_FLAG = "--pdf-engine"


def _cli_engine_violations(cmd: Sequence[str]) -> list[Violation]:
    engines: list[str] = []
    for index, arg in enumerate(cmd):
        if arg == _PDF_ENGINE_FLAG and index + 1 < len(cmd):
            engines.append(cmd[index + 1])
        elif arg.startswith(_PDF_ENGINE_FLAG + "="):
            engines.append(arg.split("=", 1)[1])
    return [
        Violation(
            "(command line)", "--pdf-engine", f"pdf-engine {engine!r} is not allowed"
        )
        for engine in engines
        if engine not in ALLOWED_PDF_ENGINES
    ]


def enforce_render_policy(project_dir: Path, cmd: Sequence[str] = ()) -> list[str]:
    """Render-time backstop: refuse forbidden configuration, neutralize content.

    Raises :class:`RenderPolicyError` before anything runs; otherwise returns the
    files whose content was neutralized.
    """
    violations = _cli_engine_violations(cmd) + find_config_violations(project_dir)
    if violations:
        raise RenderPolicyError(violations)
    return neutralize_caller_content(project_dir)
