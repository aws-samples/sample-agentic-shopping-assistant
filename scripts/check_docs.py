#!/usr/bin/env python3
"""Docs checks for the repository's knowledge base. Stdlib only; runs from any directory.

    python3 scripts/check_docs.py                    # every check
    python3 scripts/check_docs.py --skip-generated   # all but the docs/generated/ freshness check

`pnpm check` runs it through scripts/check.py. It lists every failure, grouped by check, and exits 1
if there is any:

- links: every relative link and image in tracked and untracked (non-ignored) markdown resolves to a
  file or folder in the repository, and every #anchor to a heading (GitHub/GitLab slugs) or an HTML
  id/name in the target. CHANGELOG.md files and node_modules are skipped. A link to its own file needs an
  #anchor, and a label that names a markdown file (`[scripts/README.md](...)`) must point at that file.
- indexes: every .md under docs/design-docs/, docs/product-specs/ and docs/guides/ (index files and
  .ko.md twins excepted) is linked from that folder's index.md.
- twins: every English guide docs/guides/*.md, and every README.md with a README.ko.md beside it, has
  a .ko.md twin with the same number of ## and ### headings, fenced code blocks and table rows.
- agents: the root AGENTS.md is at most 100 lines; each packages/*/AGENTS.md at most 40.
- status: every design doc and product spec (index files and .ko.md twins excepted) has a Status:
  line in its first 5 lines.
- generated: docs/generated/ matches the code. Runs, from the repository root:
      uv run --project packages/infra --frozen python scripts/gen_docs.py --check
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Dict, Iterator, List, NamedTuple, Optional, Set, Tuple
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]

INDEXED_FOLDERS = ("docs/design-docs", "docs/product-specs", "docs/guides")
GUIDES = "docs/guides"
DESIGN_DOCS = "docs/design-docs"
PRODUCT_SPECS = "docs/product-specs"
ROOT_AGENTS_MAX = 100
PACKAGE_AGENTS_MAX = 40
DESIGN_STATUS = re.compile(r"^Status: (proposed|accepted|verified|superseded)( · Verified: \d{4}-\d{2}-\d{2}.*)?$")
SPEC_STATUS = re.compile(r"^Status: (shipped|partial|planned)$")
# What check_generated() runs, for its messages; the call itself spells it out as literal arguments.
GEN_DOCS = "uv run --project packages/infra --frozen python scripts/gen_docs.py --check"

# Without git, walk the tree and skip what .gitignore would.
WALK_SKIP = {".git", "node_modules", ".venv", "cdk.out", "__pycache__", ".pytest_cache", ".ruff_cache", ".nuxt",
             ".output", "artifacts", ".claude", ".idea", ".vscode"}


# ── Markdown parsing ────────────────────────────────────────────────────


class Heading(NamedTuple):
    level: int
    text: str
    line: int


class Link(NamedTuple):
    line: int
    target: str


class Doc(NamedTuple):
    headings: List[Heading]
    links: List[Link]
    html_ids: Set[str]


FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"^\s*(`{3,}|~{3,})\s*$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
THEMATIC_BREAK = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
BLOCKQUOTE = re.compile(r"^ {0,3}>")
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d{1,9}[.)])(?:\s|$)")
REF_DEF = re.compile(r"^ {0,3}\[(?!\^)(?:[^\]\\]|\\.)+\]:[ \t]*(<[^>]*>|\S+)(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^)]*\)))?[ \t]*$")
CODE_SPAN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)")
HTML_TAG = re.compile(r"<[A-Za-z][^>]*>")
HTML_LINK_ATTR = re.compile(r"\s(?:href|src)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
HTML_ID_ATTR = re.compile(r"\s(?:id|name)\s*=\s*(?:\"([^\"]*)\"|'([^']*)')", re.I)
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _strip_blockquote(line: str) -> str:
    while BLOCKQUOTE.match(line):
        line = BLOCKQUOTE.sub("", line, count=1)
        if line.startswith(" "):
            line = line[1:]
    return line


def _strip_comments(line: str, in_comment: bool) -> Tuple[str, bool]:
    """Drop HTML comments, which can span lines. Returns the visible text and the new state."""
    out = []
    while line:
        if in_comment:
            end = line.find("-->")
            if end < 0:
                return "".join(out), True
            line, in_comment = line[end + 3:], False
        else:
            start = line.find("<!--")
            if start < 0:
                out.append(line)
                break
            out.append(line[:start])
            line, in_comment = line[start + 4:], True
    return "".join(out), in_comment


def _link_targets(text: str) -> Iterator[str]:
    """Destinations of inline links and images, `[text](target "title")`, in one line of text."""
    start = 0
    while True:
        at = text.find("](", start)
        if at < 0:
            return
        start = at + 2
        if "[" not in text[:at]:
            continue
        i = start
        while i < len(text) and text[i] in " \t":
            i += 1
        if i < len(text) and text[i] == "<":
            end = text.find(">", i + 1)
            if end > 0:
                yield text[i + 1:end]
            continue
        depth, j = 0, i
        while j < len(text):
            char = text[j]
            if char == "\\" and j + 1 < len(text):
                j += 2
                continue
            if char in " \t":
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        if j > i:
            yield text[i:j]


def parse_markdown(text: str) -> Doc:
    """Headings, link targets and HTML ids, ignoring fenced code, inline code and HTML comments."""
    headings: List[Heading] = []
    links: List[Link] = []
    html_ids: Set[str] = set()
    lines = text.splitlines()
    first = 0
    if lines and lines[0].strip() == "---":  # YAML front matter
        for k in range(1, len(lines)):
            if lines[k].strip() in ("---", "..."):
                first = k + 1
                break
    fence: Optional[Tuple[str, int]] = None
    in_comment = False
    paragraph: Optional[List[str]] = None  # the open paragraph, when a setext underline may follow
    setext_ok = False
    for index in range(first, len(lines)):
        number = index + 1
        raw = lines[index]
        line = _strip_blockquote(raw)
        quoted = line != raw
        if fence:
            close = FENCE_CLOSE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            continue
        if not in_comment:
            opening = FENCE_OPEN.match(line)
            if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
                fence = (opening.group(1)[0], len(opening.group(1)))
                paragraph = None
                continue
        was_in_comment = in_comment
        visible, in_comment = _strip_comments(line, in_comment)
        if was_in_comment:
            # The rest of a line that closes a comment is plain text, never a block start.
            _collect_inline(visible, number, links, html_ids)
            continue
        atx = ATX.match(visible)
        if atx:
            headings.append(Heading(len(atx.group(1)), _atx_text(atx.group(2) or ""), number))
            paragraph = None
        elif paragraph is not None and setext_ok and not quoted and SETEXT.match(visible):
            level = 1 if visible.strip()[0] == "=" else 2
            headings.append(Heading(level, " ".join(part.strip() for part in paragraph), number - len(paragraph)))
            paragraph = None
        elif not visible.strip() or THEMATIC_BREAK.match(visible) or quoted:
            paragraph = None
        elif LIST_ITEM.match(visible) or visible.lstrip().startswith(("|", "<")):
            paragraph, setext_ok = [visible], False
        elif paragraph is not None:
            paragraph.append(visible)
        else:
            paragraph, setext_ok = [visible], len(visible) - len(visible.lstrip(" ")) < 4
        _collect_inline(visible, number, links, html_ids)
        definition = REF_DEF.match(visible)
        if definition:
            target = definition.group(1)
            links.append(Link(number, target[1:-1] if target.startswith("<") else target))
    return Doc(headings, links, html_ids)


def _collect_inline(text: str, number: int, links: List[Link], html_ids: Set[str]) -> None:
    text = CODE_SPAN.sub(" code ", text)
    links.extend(Link(number, target) for target in _link_targets(text))
    for tag in HTML_TAG.findall(text):
        links.extend(Link(number, a or b) for a, b in HTML_LINK_ATTR.findall(tag))
        html_ids.update(a or b for a, b in HTML_ID_ATTR.findall(tag))


def _atx_text(text: str) -> str:
    """Heading text without the optional closing sequence of #s."""
    if re.fullmatch(r"#+", text):
        return ""
    return re.sub(r"[ \t]+#+$", "", text)


def plain_text(markdown: str) -> str:
    """Approximately the text a heading renders to: what GitHub and GitLab build the anchor from."""
    parts = []
    for index, segment in enumerate(re.split(r"(`+[^`]*?`+)", markdown)):
        if index % 2:
            parts.append(segment.strip("`").strip() if segment.startswith("``") else segment.strip("`"))
            continue
        segment = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", segment)  # images render no text
        segment = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", segment)  # [text](url)
        segment = re.sub(r"\[([^\]]*)\]\[[^\]]*\]", r"\1", segment)  # [text][ref]
        segment = re.sub(r"<[^>]+>", "", segment)  # inline HTML
        segment = re.sub(r"(?<![\w\\])(_{1,3})(?=\S)(.+?)(?<=\S)\1(?!\w)", r"\2", segment)  # _emphasis_
        segment = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", segment)  # backslash escapes
        parts.append(html.unescape(segment))
    return "".join(parts).strip()


def _keep(char: str) -> bool:
    return char in "- _" or unicodedata.category(char)[0] in "LMN" or unicodedata.category(char) == "Pc"


def github_slug(text: str) -> str:
    """GitHub's anchor: lowercase, punctuation dropped (hyphens, underscores, spaces kept), spaces to hyphens."""
    return "".join(char for char in plain_text(text).lower() if _keep(char)).replace(" ", "-")


def gitlab_slug(text: str) -> str:
    """GitLab's anchor: like GitHub's, but runs of hyphens collapse to one."""
    return re.sub(r"-{2,}", "-", github_slug(text))


def anchors(doc: Doc) -> Set[str]:
    """Every fragment that lands somewhere in the doc, under GitHub's or GitLab's rules."""
    found = {unicodedata.normalize("NFC", value) for value in doc.html_ids}
    for slugify in (github_slug, gitlab_slug):
        seen: Dict[str, int] = {}
        for heading in doc.headings:
            base = unicodedata.normalize("NFC", slugify(heading.text))
            slug = base
            if base in seen:
                count = seen[base]
                while True:
                    count += 1
                    slug = f"{base}-{count}"
                    if slug not in seen:
                        break
                seen[base] = count
            seen[slug] = 0
            found.add(slug)
    return found


# ── Repository files ────────────────────────────────────────────────────


def rel(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def repository_files() -> Tuple[Set[str], bool]:
    """Tracked and untracked, non-ignored files that exist, as POSIX paths from the root.

    The flag is False when git isn't available and the tree was walked instead.
    """
    try:
        listed = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            capture_output=True, check=True,
        ).stdout.decode("utf-8", "surrogateescape")
        # Deleted files stay in the index until the deletion is staged.
        return {name for name in listed.split("\0") if name and (ROOT / name).is_file()}, True
    except (OSError, subprocess.CalledProcessError):
        files = set()
        for directory, subdirectories, names in os.walk(ROOT):
            subdirectories[:] = [name for name in subdirectories if name not in WALK_SKIP]
            for name in names:
                files.add(rel(Path(directory) / name))
        return files, False


class Repository:
    def __init__(self) -> None:
        self.files, self.from_git = repository_files()
        self.folders = {"."}
        for name in self.files:
            parts = name.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                self.folders.add("/".join(parts[:depth]))
        self._docs: Dict[str, Doc] = {}
        self._anchors: Dict[str, Set[str]] = {}
        self._listings: Dict[Path, Set[str]] = {}

    def markdown(self, prefix: str = "") -> List[str]:
        return sorted(name for name in self.files if name.lower().endswith(".md") and name.startswith(prefix)
                      and "node_modules" not in name.split("/"))

    def doc(self, name: str) -> Doc:
        if name not in self._docs:
            self._docs[name] = parse_markdown((ROOT / name).read_text(encoding="utf-8", errors="replace"))
        return self._docs[name]

    def anchors(self, name: str) -> Set[str]:
        if name not in self._anchors:
            self._anchors[name] = anchors(self.doc(name))
        return self._anchors[name]

    def exact_case(self, path: Path) -> bool:
        """True if every component exists with exactly this case (macOS file systems ignore case; Git doesn't)."""
        current = ROOT
        for part in path.relative_to(ROOT).parts:
            if current not in self._listings:
                try:
                    self._listings[current] = {unicodedata.normalize("NFC", name) for name in os.listdir(current)}
                except OSError:
                    return False
            if unicodedata.normalize("NFC", part) not in self._listings[current]:
                return False
            current = current / part
        return True


# ── Checks ──────────────────────────────────────────────────────────────


def _resolve(source: str, target: str) -> Tuple[Optional[Path], str]:
    """The absolute path a link points to (None for same-file) and its decoded fragment."""
    path, _, fragment = re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", target).partition("#")
    path = unquote(path.split("?", 1)[0])
    fragment = unicodedata.normalize("NFC", unquote(fragment))
    if not path:
        return None, fragment
    base = ROOT if path.startswith("/") else (ROOT / source).parent
    return Path(os.path.normpath(base / path.lstrip("/"))), fragment


def check_links(repo: Repository) -> List[str]:
    failures = []
    for source in repo.markdown():
        if Path(source).name == "CHANGELOG.md":
            continue
        for link in repo.doc(source).links:
            target = link.target.strip()
            if not target or SCHEME.match(target) or target.startswith("//"):
                continue
            where = f"{source}:{link.line}: ({target})"
            path, fragment = _resolve(source, target)
            if path is None:
                if fragment and fragment not in repo.anchors(source):
                    failures.append(f"{where} no heading or id #{fragment} in this file")
                continue
            if path != ROOT and ROOT not in path.parents:
                failures.append(f"{where} points outside the repository")
                continue
            name = rel(path)
            if name == source and not fragment:
                failures.append(f"{where} links to its own file; drop the link or add an #anchor")
                continue
            if not path.exists():
                failures.append(f"{where} {name} does not exist")
                continue
            if not repo.exact_case(path):
                failures.append(f"{where} {name} exists only with different letter case (Git and Linux are case-sensitive)")
                continue
            if name not in repo.files and name not in repo.folders:
                failures.append(f"{where} {name} is ignored by Git, so the link breaks for everyone else")
                continue
            if fragment and path.is_file() and path.suffix.lower() == ".md" and fragment not in repo.anchors(name):
                failures.append(f"{where} no heading or id #{fragment} in {name}")
    return failures


LABELLED_LINK = re.compile(r"\[`?([^\]`]+?)`?\]\(([^)\s]+)\)")


def _visible_lines(name: str) -> Iterator[Tuple[int, str]]:
    """Lines outside fenced code blocks, numbered from 1."""
    fence: Optional[Tuple[str, int]] = None
    for number, line in enumerate((ROOT / name).read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if fence:
            close = FENCE_CLOSE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            continue
        opening = FENCE_OPEN.match(line)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            continue
        yield number, line


def _english_name(path: str) -> str:
    base = Path(path).name
    return base[:-6] + ".md" if base.endswith(".ko.md") else base


def check_labels(repo: Repository) -> List[str]:
    """A label that names a markdown file must point at that file (a Korean twin counts as the same file)."""
    failures = []
    for source in repo.markdown():
        if Path(source).name == "CHANGELOG.md":
            continue
        for number, line in _visible_lines(source):
            for match in LABELLED_LINK.finditer(line):
                label, target = match.group(1).strip(), match.group(2)
                if not label.lower().endswith(".md") or SCHEME.match(target) or target.startswith("#"):
                    continue
                path = target.split("#", 1)[0]
                if _english_name(label) != _english_name(path):
                    failures.append(f"{source}:{number}: [{label}]({target}) names one file and links another")
    return failures


def _blocks(name: str) -> Tuple[int, int]:
    """Fenced code blocks and table rows."""
    fences = rows = 0
    fence: Optional[Tuple[str, int]] = None
    for line in (ROOT / name).read_text(encoding="utf-8", errors="replace").splitlines():
        if fence:
            close = FENCE_CLOSE.match(line)
            if close and close.group(1)[0] == fence[0] and len(close.group(1)) >= fence[1]:
                fence = None
            continue
        opening = FENCE_OPEN.match(line)
        if opening and not (opening.group(1)[0] == "`" and "`" in opening.group(2)):
            fence = (opening.group(1)[0], len(opening.group(1)))
            fences += 1
        elif line.lstrip().startswith("|"):
            rows += 1
    return fences, rows


def _twin(name: str) -> str:
    return name[:-3] + ".ko.md"


def _is_index_or_twin(name: str) -> bool:
    base = Path(name).name
    return base in ("index.md", "index.ko.md") or base.endswith(".ko.md")


def check_indexes(repo: Repository) -> List[str]:
    failures = []
    for folder in INDEXED_FOLDERS:
        if not (ROOT / folder).is_dir():
            failures.append(f"{folder}/ is missing")
            continue
        index = f"{folder}/index.md"
        if not (ROOT / index).is_file():
            failures.append(f"{index} is missing, so nothing in {folder}/ is indexed")
            continue
        indexes = [name for name in repo.markdown(folder + "/") if Path(name).name == "index.md"]
        linked = set()
        for name in indexes:
            for link in repo.doc(name).links:
                path, _ = _resolve(name, link.target.strip())
                if path is not None and not SCHEME.match(link.target.strip()):
                    linked.add(rel(path))
        for name in repo.markdown(folder + "/"):
            if _is_index_or_twin(name) or name in linked:
                continue
            nearest = [index for index in indexes if name.startswith(str(Path(index).parent.as_posix()) + "/")]
            failures.append(f"{name} is not linked from {' or '.join(sorted(nearest)) or index}")
    return failures


def _heading_levels(doc: Doc) -> List[Heading]:
    return [heading for heading in doc.headings if heading.level in (2, 3)]


def _show(heading: Optional[Heading], name: str) -> str:
    return f"{Path(name).name}:{heading.line} {'#' * heading.level} {heading.text}" if heading else f"{Path(name).name}: (none)"


def check_twins(repo: Repository) -> List[str]:
    failures = []
    english = [name for name in repo.markdown(GUIDES + "/") if "/" not in name[len(GUIDES) + 1:] and not name.endswith(".ko.md")]
    english += [name for name in repo.markdown() if Path(name).name == "README.md" and (ROOT / _twin(name)).is_file()]
    for name in sorted(set(english)):
        twin = _twin(name)
        if not (ROOT / twin).is_file() or not repo.exact_case(ROOT / twin):
            failures.append(f"{name} has no Korean twin {twin}")
            continue
        ours, theirs = _heading_levels(repo.doc(name)), _heading_levels(repo.doc(twin))
        counts = [(sum(h.level == level for h in ours), sum(h.level == level for h in theirs)) for level in (2, 3)]
        if any(a != b for a, b in counts):
            detail = ", ".join(f"{'#' * level} {a} vs {b}" for level, (a, b) in zip((2, 3), counts) if a != b)
            position = next((i for i, (a, b) in enumerate(zip(ours, theirs)) if a.level != b.level), min(len(ours), len(theirs)))
            first = ours[position] if position < len(ours) else None
            second = theirs[position] if position < len(theirs) else None
            failures.append(f"{name} and {twin} differ in headings ({detail}); first mismatch: "
                            f"{_show(first, name)} | {_show(second, twin)}")
        (fences, rows), (their_fences, their_rows) = _blocks(name), _blocks(twin)
        if fences != their_fences:
            failures.append(f"{name} has {fences} fenced code blocks and {twin} has {their_fences}")
        if rows != their_rows:
            failures.append(f"{name} has {rows} table rows and {twin} has {their_rows}")
    return failures


def _line_count(path: Path) -> int:
    return len(path.read_text(encoding="utf-8", errors="replace").splitlines())


def check_agents() -> List[str]:
    failures = []
    root = ROOT / "AGENTS.md"
    if not root.is_file():
        failures.append(f"AGENTS.md is missing (the agent map; at most {ROOT_AGENTS_MAX} lines)")
    elif _line_count(root) > ROOT_AGENTS_MAX:
        failures.append(f"AGENTS.md has {_line_count(root)} lines; the limit is {ROOT_AGENTS_MAX}")
    for path in sorted(ROOT.glob("packages/*/AGENTS.md")):
        if _line_count(path) > PACKAGE_AGENTS_MAX:
            failures.append(f"{rel(path)} has {_line_count(path)} lines; the limit is {PACKAGE_AGENTS_MAX}")
    return failures


def check_status(repo: Repository) -> List[str]:
    failures = []
    rules = ((DESIGN_DOCS, DESIGN_STATUS, "Status: proposed|accepted|verified|superseded[ · Verified: YYYY-MM-DD…]"),
             (PRODUCT_SPECS, SPEC_STATUS, "Status: shipped|partial|planned"))
    for folder, pattern, expected in rules:
        for name in repo.markdown(folder + "/"):
            if _is_index_or_twin(name):
                continue
            head = (ROOT / name).read_text(encoding="utf-8", errors="replace").splitlines()[:5]
            if any(pattern.match(line) for line in head):
                continue
            near = next((line for line in head if line.lower().lstrip("*_ ").startswith("status")), None)
            hint = f"; found {near!r}" if near else ""
            failures.append(f"{name} has no '{expected}' line in its first 5 lines{hint}")
    return failures


def check_generated() -> List[str]:
    if shutil.which("uv") is None:
        return ["uv is not installed, so scripts/gen_docs.py --check can't run (https://docs.astral.sh/uv/)"]
    command = GEN_DOCS
    try:
        result = subprocess.run(["uv", "run", "--project", "packages/infra", "--frozen", "python", "scripts/gen_docs.py", "--check"],
                                cwd=ROOT, capture_output=True, text=True, env={**os.environ, "AWS_EC2_METADATA_DISABLED": "true"})
    except OSError as error:
        return [f"{command} could not start: {error}"]
    if result.returncode == 0:
        return []
    output = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
    tail = output[-40:]
    return [f"{command} exited {result.returncode}:" + "".join(f"\n      {line}" for line in tail)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--skip-generated", action="store_true", help="skip the docs/generated/ freshness check")
    args = parser.parse_args(argv)
    repo = Repository()
    results = [
        ("links", check_links(repo) + check_labels(repo)),
        ("indexes", check_indexes(repo)),
        ("twins", check_twins(repo)),
        ("agents", check_agents()),
        ("status", check_status(repo)),
    ]
    if not args.skip_generated:
        results.append(("generated", check_generated()))
    if not repo.from_git:
        print("check_docs: git unavailable; walked the tree instead of using git's file list", file=sys.stderr)
    total = sum(len(failures) for _, failures in results)
    for check, failures in results:
        for failure in failures:
            print(f"{check}: {failure}")
    checked = len(repo.markdown())
    if total:
        print(f"check_docs: {total} failure{'s' if total != 1 else ''} ({checked} markdown files)")
        return 1
    print(f"check_docs: all checks passed ({checked} markdown files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
