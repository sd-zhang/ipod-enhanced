# MIT License
#
# Copyright (c) 2026 TREX Ai
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

"""Validate local Markdown links and exact GitHub-style heading anchors.

Ported from TREX Ai under the MIT license above. The original rendered-heading,
HTML-anchor, case-sensitive path and directory-README semantics are retained.
By default sources and targets must belong to the Git index. --candidate FILE
uses the reviewed path-to-SHA-256 inventory defined by check_repository.py.
Private/generated paths are forbidden in both modes. External URLs are not
fetched; URLs containing credentials are rejected without printing their value.
"""

import argparse
import html
import posixpath
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import SplitResult, unquote, urlsplit

from check_repository import CREDENTIAL, inventory
from markdown_it import MarkdownIt
from markdown_it.token import Token

_PARSER = MarkdownIt()


class _EmbeddedHTML(HTMLParser):
    """Collect anchors defined and references made by embedded HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[str] = []
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a":
            for key in ("name", "id"):
                if anchor := attributes.get(key):
                    self.anchors.append(anchor)
            if href := attributes.get("href"):
                self.references.append(href)
        elif tag == "img" and (src := attributes.get("src")):
            self.references.append(src)


def _html_refs(tokens: list[Token]) -> tuple[list[str], list[str]]:
    """Anchors and references from HTML tokens (code fences never parse as HTML)."""
    parser = _EmbeddedHTML()
    for token in tokens:
        if token.type == "html_block":
            parser.feed(token.content)
        elif token.type == "inline":
            for child in token.children or []:
                if child.type == "html_inline":
                    parser.feed(child.content)
    return parser.anchors, parser.references


def _inline_text(token: Token) -> str:
    """Collect the rendered text of an inline token, skipping raw HTML."""
    parts: list[str] = []
    for child in token.children or []:
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.children:
            parts.append(_inline_text(child))
    return "".join(parts)


def _slugify(text: str) -> str:
    value = html.unescape(text).lower()
    return re.sub(r"[^\w\- ]", "", value).replace(" ", "-")


def heading_anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    """Return the GitHub-style anchor slugs for every heading in `path`."""
    if path in cache:
        return cache[path]
    tokens = _PARSER.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for index, token in enumerate(tokens[:-1]):
        if token.type != "heading_open" or tokens[index + 1].type != "inline":
            continue
        base = _slugify(_inline_text(tokens[index + 1]))
        candidate = base
        suffix = 0
        while candidate in found:
            suffix += 1
            candidate = f"{base}-{suffix}"
        found.add(candidate)
    found.update(_html_refs(tokens)[0])
    cache[path] = found
    return found


def _check_reference(
    reference: str,
    parsed: SplitResult,
    rel_source: str,
    root: Path,
    tracked: set[str],
    cache: dict[Path, set[str]],
) -> str | None:
    """Return a failure line for `reference`, or None when it is valid."""
    relative = unquote(parsed.path)
    if relative.startswith("/"):
        return f"{rel_source} -> root-absolute {relative} (use a relative path)"
    fragment = unquote(parsed.fragment)
    anchor_file: str | None = None
    if not relative:
        # Same-document reference: the source is already open in front of us,
        # so its anchors resolve whether or not git tracks it yet. Requiring
        # tracked membership here would fail every table of contents in a doc
        # that has not been staged.
        anchor_file = rel_source
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(rel_source), relative))
        if target in tracked:
            if target.endswith(".md"):
                anchor_file = target
        elif target == "." or any(t.startswith(f"{target}/") for t in tracked):
            readme = "README.md" if target == "." else f"{target}/README.md"
            anchor_file = readme if readme in tracked else None
            if fragment and anchor_file is None:
                return f"{rel_source} -> missing anchor {relative}"
        else:
            return f"{rel_source} -> missing {relative}"
    if fragment and anchor_file is not None:
        try:
            anchors = heading_anchors(root / anchor_file, cache)
        except UnicodeDecodeError:
            return f"{rel_source} -> not valid UTF-8 {relative}"
        if fragment not in anchors:
            return f"{rel_source} -> missing anchor {relative}"
    return None


def check_links(sources: set[Path], root: Path, tracked: set[str]) -> list[str]:
    """Return failure lines for broken relative links/anchors in `sources`."""
    failures: list[str] = []
    cache: dict[Path, set[str]] = {}
    for source in sorted(sources):
        rel_source = source.relative_to(root).as_posix()
        try:
            tokens = _PARSER.parse(source.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            failures.append(f"{rel_source} -> not valid UTF-8")
            continue
        references: list[str] = []
        for token in tokens:
            if token.type != "inline" or not token.children:
                continue
            for child in token.children:
                if child.type not in {"link_open", "image"}:
                    continue
                attribute = "href" if child.type == "link_open" else "src"
                reference = child.attrGet(attribute)
                if isinstance(reference, str) and reference:
                    references.append(reference)
        references.extend(_html_refs(tokens)[1])
        for reference in references:
            parsed = urlsplit(reference)
            if parsed.password is not None or CREDENTIAL.search(reference.encode()):
                failures.append(f"{rel_source} -> credential in link (value redacted)")
                continue
            if parsed.scheme or parsed.netloc:
                continue
            failure = _check_reference(reference, parsed, rel_source, root, tracked, cache)
            if failure is not None:
                failures.append(failure)
    return failures


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Validate publication Markdown links and anchors")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        tracked, failures = inventory(root, args.candidate)
        if failures:
            for failure in failures:
                print(failure, file=sys.stderr)
            return 1
        if args.files:
            sources = {Path(arg).resolve() for arg in args.files}
            if any(
                not path.is_relative_to(root) or path.relative_to(root).as_posix() not in tracked
                for path in sources
            ):
                print("documentation source is outside publication inventory", file=sys.stderr)
                return 1
        else:
            sources = {root / name for name in tracked if name.endswith(".md")}
        failures = check_links(sources, root, tracked)
    except (OSError, UnicodeError, ValueError):
        print("cannot validate documentation (details redacted)", file=sys.stderr)
        return 1
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        return 1
    print(f"Validated {len(sources)} Markdown files; 0 broken local links or anchors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
