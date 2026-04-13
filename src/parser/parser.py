"""Puppet DSL parser — entry point.

Uses lark-parser with an Earley parser and the PuppetTransformer to produce
an AST from raw Puppet manifest text.
"""
from __future__ import annotations

import logging
from pathlib import Path

from lark import Lark, UnexpectedCharacters, UnexpectedEOF, UnexpectedToken
from lark.exceptions import VisitError

from src.parser.ast_nodes import Manifest
from src.parser.transformer import PuppetTransformer

logger = logging.getLogger(__name__)

_GRAMMAR_PATH = Path(__file__).parent / "puppet.lark"


def _build_parser(puppet_version: int = 4) -> Lark:
    """Build and cache a lark parser instance.

    The grammar is version-agnostic for now — version-specific
    behaviour is handled at the transformer/converter level.
    """
    grammar = _GRAMMAR_PATH.read_text(encoding="utf-8")
    return Lark(
        grammar,
        parser="earley",
        ambiguity="resolve",
        propagate_positions=True,
    )


# Module-level cache (one parser per version)
_parsers: dict[int, Lark] = {}


def get_parser(puppet_version: int = 4) -> Lark:
    if puppet_version not in _parsers:
        _parsers[puppet_version] = _build_parser(puppet_version)
    return _parsers[puppet_version]


class ParseError(Exception):
    """Raised when a Puppet manifest cannot be parsed."""

    def __init__(self, message: str, line: int = 0, col: int = 0, context: str = "") -> None:
        super().__init__(message)
        self.line    = line
        self.col     = col
        self.context = context

    def __str__(self) -> str:
        loc = f" (line {self.line}, col {self.col})" if self.line else ""
        ctx = f"\n  Context: {self.context}" if self.context else ""
        return f"{super().__str__()}{loc}{ctx}"


def _candidate_sources_without_trailing_brace(source: str) -> list[str]:
    """Yield candidate sources obtained by removing one trailing '}' at a time.

    Returns a list (up to 3 candidates) so the caller can retry parsing on each.
    Each candidate removes one more trailing '}' than the previous.
    Stops early if no more trailing '}' can be removed.
    """
    candidates: list[str] = []
    current = source.rstrip()
    for _ in range(3):
        if not current.endswith("}"):
            break
        current = current[:-1].rstrip()
        candidates.append(current)
    return candidates


def parse(
    source: str,
    source_file: str = "<string>",
    puppet_version: int = 4,
) -> Manifest:
    """Parse a Puppet manifest string into an AST.

    Args:
        source:          Raw Puppet manifest text.
        source_file:     Path/name of the source file (used in error messages).
        puppet_version:  3 or 4 (default 4). Affects how certain constructs
                         are interpreted during conversion.

    Returns:
        A Manifest AST node.

    Raises:
        ParseError: if the manifest has syntax errors.
    """
    parser = get_parser(puppet_version)
    transformer = PuppetTransformer()

    def _do_parse(src: str) -> object:
        try:
            return parser.parse(src)
        except UnexpectedCharacters as e:
            # Try to recover: when there's a stray `}`, try removing one
            # trailing `}` at a time and retrying (up to 3 attempts).
            if str(e.char) == "}":
                for candidate in _candidate_sources_without_trailing_brace(src):
                    try:
                        tree = parser.parse(candidate)
                        logger.warning(
                            "Recovered from stray trailing '}' in %s — "
                            "the source file has an extra closing brace.",
                            source_file,
                        )
                        return tree
                    except (UnexpectedCharacters, UnexpectedEOF, UnexpectedToken):
                        continue  # Try stripping one more `}`
            raise ParseError(
                f"Unexpected character in {source_file}",
                line=e.line,
                col=e.column,
                context=e.get_context(src, span=40),
            ) from e
        except UnexpectedEOF as e:
            raise ParseError(
                f"Unexpected end of file in {source_file} — "
                f"expected one of: {[str(t) for t in e.expected[:5]]}",
            ) from e
        except UnexpectedToken as e:
            raise ParseError(
                f"Unexpected token '{e.token}' in {source_file}",
                line=e.line,
                col=e.column,
                context=e.get_context(src, span=40),
            ) from e

    tree = _do_parse(source)

    try:
        manifest: Manifest = transformer.transform(tree)
    except VisitError as e:
        raise ParseError(
            f"AST construction failed for {source_file}: {e.orig_exc}",
        ) from e

    manifest.source_file    = source_file
    manifest.puppet_version = puppet_version
    return manifest


def parse_file(
    path: str | Path,
    puppet_version: int = 4,
) -> Manifest:
    """Parse a Puppet manifest file.

    Args:
        path:            Path to the .pp file.
        puppet_version:  3 or 4.

    Returns:
        A Manifest AST node.
    """
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    return parse(source, source_file=str(p), puppet_version=puppet_version)
