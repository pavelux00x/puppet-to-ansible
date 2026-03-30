"""Puppetfile parser.

Parses the r10k/Librarian-Puppet Puppetfile format.

Supported syntax::

    forge 'https://forgeapi.puppet.com'

    mod 'puppetlabs/apache', '9.1.0'
    mod 'puppetlabs/mysql', '>= 12.0.0 < 16.0.0'
    mod 'internal', git: 'https://git.example.com/internal.git', tag: 'v2.3.0'
    mod 'internal', :git => 'https://...', :branch => 'main'
    mod 'standalone'   # no version, no author
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class PuppetModule:
    """A single entry in the Puppetfile."""
    name: str                       # module name (without author)
    author: str                     # Forge author, empty for git-only entries
    version: str | None             # version string / constraint, or None
    source: str                     # 'forge', 'git', 'svn', 'hg', 'local'
    git_url: str | None = None
    git_ref: str | None = None      # tag / branch / commit value
    git_ref_type: str | None = None # 'tag', 'branch', 'commit'

    @property
    def full_name(self) -> str:
        """Return 'author/name' for Forge modules, 'name' for git/local."""
        return f"{self.author}/{self.name}" if self.author else self.name


@dataclass
class Puppetfile:
    """Parsed representation of a Puppetfile."""
    forge_url: str = "https://forgeapi.puppet.com"
    modules: list[PuppetModule] = field(default_factory=list)

    @property
    def forge_modules(self) -> list[PuppetModule]:
        return [m for m in self.modules if m.source == "forge"]

    @property
    def git_modules(self) -> list[PuppetModule]:
        return [m for m in self.modules if m.source == "git"]

    @property
    def local_modules(self) -> list[PuppetModule]:
        return [m for m in self.modules if m.source == "local"]


# ── Parser ────────────────────────────────────────────────────────────────────

# Regex for Ruby hash-rocket  :key => 'value'  or modern  key: 'value'
_KV_PATTERN = re.compile(
    r"""
    (?::(\w+)\s*=>\s*|(\w+):\s*)   # :key => or key:
    (['"])(.+?)\3                    # quoted value
    """,
    re.VERBOSE,
)

# Match the module name line:  mod 'author/name', 'version'
# or  mod 'author/name'
_MOD_START = re.compile(
    r"""
    ^mod\s+                          # mod keyword
    (['"])                            # opening quote
    ([A-Za-z0-9_\-]+)               # author or standalone name
    (?:/([A-Za-z0-9_\-]+))?         # /module (optional)
    \1                               # closing quote
    (.*)$                            # rest of line (may have version or continue)
    """,
    re.VERBOSE,
)

# Quoted string (version) after the comma has already been stripped
_QUOTED_STRING = re.compile(r"""^(['"])([^'"]+)\1""")


class PuppetfileParser:
    """Parse a Puppetfile into a :class:`Puppetfile` object."""

    def parse_file(self, path: Path | str) -> Puppetfile:
        return self.parse(Path(path).read_text(encoding="utf-8"))

    def parse(self, content: str) -> Puppetfile:
        pf = Puppetfile()
        lines = content.splitlines()
        i = 0
        while i < len(lines):
            raw = lines[i]
            line = _strip_comment(raw).strip()
            i += 1

            if not line:
                continue

            # forge 'url'
            if line.startswith("forge "):
                m = re.match(r"""forge\s+(['"])(.+?)\1""", line)
                if m:
                    pf.forge_url = m.group(2)
                continue

            # mod 'name' ...
            m = _MOD_START.match(line)
            if not m:
                continue

            first_part, author_or_name, module_part, rest = (
                m.group(1), m.group(2), m.group(3), m.group(4)
            )

            if module_part:
                author, name = author_or_name, module_part
            else:
                author, name = "", author_or_name

            # Collect continuation lines (line ends with comma or has \)
            block = rest
            while block.rstrip().endswith(",") or block.rstrip().endswith("\\"):
                if i >= len(lines):
                    break
                next_line = _strip_comment(lines[i]).strip()
                i += 1
                block = block.rstrip(" ,\\") + " " + next_line

            mod = self._parse_mod_args(author, name, block)
            pf.modules.append(mod)

        return pf

    # ── Private ───────────────────────────────────────────────────────────────

    def _parse_mod_args(self, author: str, name: str, rest: str) -> PuppetModule:
        """Parse everything after the module name on the mod line."""
        rest = rest.strip().lstrip(",").strip()

        if not rest:
            return PuppetModule(name=name, author=author, version=None, source="forge")

        # Check for git/svn/hg/local keywords
        kv = dict(self._extract_kv(rest))

        git_url = kv.get("git")
        if git_url:
            ref, ref_type = _pick_ref(kv)
            return PuppetModule(
                name=name, author=author, version=None,
                source="git", git_url=git_url,
                git_ref=ref, git_ref_type=ref_type,
            )

        if kv.get("local") or kv.get("path"):
            return PuppetModule(name=name, author=author, version=None, source="local")

        if kv.get("svn"):
            return PuppetModule(name=name, author=author, version=None, source="svn")

        # Plain version string  (comma already stripped above)
        vm = _QUOTED_STRING.match(rest)
        if vm:
            return PuppetModule(
                name=name, author=author,
                version=vm.group(2),
                source="forge",
            )

        # Could not parse — treat as forge with no version
        return PuppetModule(name=name, author=author, version=None, source="forge")

    @staticmethod
    def _extract_kv(text: str) -> list[tuple[str, str]]:
        """Extract all key-value pairs from Ruby hash syntax."""
        pairs = []
        for m in _KV_PATTERN.finditer(text):
            key = m.group(1) or m.group(2)
            value = m.group(4)
            pairs.append((key, value))
        return pairs


def _strip_comment(line: str) -> str:
    """Remove Ruby-style inline comments (#...) respecting quoted strings."""
    in_single = False
    in_double = False
    for idx, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            return line[:idx]
    return line


def _pick_ref(kv: dict[str, str]) -> tuple[str | None, str | None]:
    for ref_type in ("tag", "branch", "commit", "ref"):
        if ref_type in kv:
            return kv[ref_type], ref_type
    return None, None
