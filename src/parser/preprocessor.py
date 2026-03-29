"""Cross-file Puppet manifest preprocessor.

Resolves include/import chains by discovering and loading dependent
manifests before conversion. Handles:
- ``include`` statements → load class definition from module path
- ``import`` statements (Puppet 3) → inline file content
- Module auto-loading conventions (``manifests/init.pp``, ``manifests/<class>.pp``)
- Cycle detection to prevent infinite loops
- Partial loading (missing files logged as warnings, not fatal errors)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# Puppet module directory names that are NOT modules (reserved names)
_RESERVED_DIRS = {"lib", "tests", "spec", "examples", "functions", "types", "tasks", "plans"}


@dataclass
class PreprocessorResult:
    """Collected manifests after include/import resolution."""

    # Ordered list of manifest paths (root first, dependencies after)
    manifest_paths: list[Path] = field(default_factory=list)
    # Map of class name → source file where it's defined
    class_sources: dict[str, Path] = field(default_factory=dict)
    # Map of defined-type name → source file
    defined_type_sources: dict[str, Path] = field(default_factory=dict)
    # Files that were requested but could not be found
    missing_files: list[str] = field(default_factory=list)
    # Informational warnings accumulated during preprocessing
    warnings: list[str] = field(default_factory=list)


class ManifestPreprocessor:
    """Resolves cross-file dependencies for a Puppet manifest or module.

    Typical usage::

        pp = ManifestPreprocessor(module_paths=['/etc/puppet/modules'])
        result = pp.resolve('/etc/puppet/manifests/site.pp')
        # result.manifest_paths is an ordered list ready for parsing

    ``module_paths`` is the Puppet module path (``$modulepath``).  Multiple
    directories are supported (searched left-to-right, first match wins).
    """

    # Regex patterns for cheap pre-scan (no full parse needed at this stage)
    _INCLUDE_RE = re.compile(
        r"""(?x)
        \b include \s+ (['"]?)
        ([\w:]+)          # class name
        \1
        """,
        re.MULTILINE,
    )
    _REQUIRE_RE = re.compile(
        r"""(?x)
        \b require \s* => \s* (['"]?)
        ([\w:]+)
        \1
        """,
        re.MULTILINE,
    )
    _CLASS_DEF_RE = re.compile(r"^\s*class\s+([\w:]+)", re.MULTILINE)
    _DEFINE_DEF_RE = re.compile(r"^\s*define\s+([\w:]+)", re.MULTILINE)
    _IMPORT_RE = re.compile(r'^\s*import\s+[\'"]([^\'"]+)[\'"]', re.MULTILINE)

    def __init__(
        self,
        module_paths: Sequence[str | Path] | None = None,
        puppet_version: int = 4,
        max_depth: int = 50,
    ) -> None:
        self.module_paths: list[Path] = [Path(p) for p in (module_paths or [])]
        self.puppet_version = puppet_version
        self.max_depth = max_depth

        # Internal state (reset on each resolve() call)
        self._visited: set[Path] = set()
        self._result: PreprocessorResult = PreprocessorResult()

    # ── Public API ────────────────────────────────────────────────────────────

    def resolve(self, root_manifest: str | Path) -> PreprocessorResult:
        """Resolve all dependencies of *root_manifest* and return the result.

        The returned ``PreprocessorResult.manifest_paths`` is ordered so that
        dependencies come **before** the files that need them (topological order).
        """
        self._visited = set()
        self._result = PreprocessorResult()

        root = Path(root_manifest).resolve()
        if not root.exists():
            raise FileNotFoundError(f"Root manifest not found: {root}")

        self._process_file(root, depth=0)
        return self._result

    def resolve_module(self, module_dir: str | Path) -> PreprocessorResult:
        """Resolve all manifests in a Puppet module directory.

        Processes ``manifests/init.pp`` first, then all other ``.pp`` files
        in ``manifests/`` in alphabetical order.
        """
        self._visited = set()
        self._result = PreprocessorResult()

        mod_dir = Path(module_dir).resolve()
        manifests_dir = mod_dir / "manifests"

        if not manifests_dir.is_dir():
            raise NotADirectoryError(f"No manifests/ directory in {mod_dir}")

        init_pp = manifests_dir / "init.pp"
        if init_pp.exists():
            self._process_file(init_pp, depth=0)

        for pp_file in sorted(manifests_dir.rglob("*.pp")):
            if pp_file != init_pp:
                self._process_file(pp_file, depth=0)

        return self._result

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _process_file(self, path: Path, depth: int) -> None:
        """Recursively process a manifest file and its dependencies."""
        if depth > self.max_depth:
            self._result.warnings.append(
                f"Max include depth ({self.max_depth}) reached at {path} — stopping recursion"
            )
            return

        resolved = path.resolve()
        if resolved in self._visited:
            return  # Already processed (cycle guard)
        self._visited.add(resolved)

        source = self._read_safe(resolved)
        if source is None:
            return

        # Scan for class/defined-type definitions in this file
        self._scan_definitions(source, resolved)

        # Handle Puppet 3 import statements first (they inline other files)
        if self.puppet_version == 3:
            self._handle_imports(source, resolved, depth)

        # Handle include statements (load class definitions)
        self._handle_includes(source, resolved, depth)

        # Add this file AFTER its dependencies (topological order)
        if resolved not in {p for p in self._result.manifest_paths}:
            self._result.manifest_paths.append(resolved)

    def _scan_definitions(self, source: str, file_path: Path) -> None:
        """Index all class and defined-type names found in *source*."""
        for m in self._CLASS_DEF_RE.finditer(source):
            class_name = m.group(1)
            if class_name not in self._result.class_sources:
                self._result.class_sources[class_name] = file_path

        for m in self._DEFINE_DEF_RE.finditer(source):
            dt_name = m.group(1)
            if dt_name not in self._result.defined_type_sources:
                self._result.defined_type_sources[dt_name] = file_path

    def _handle_imports(self, source: str, file_path: Path, depth: int) -> None:
        """Process Puppet 3 ``import`` statements relative to *file_path*."""
        for m in self._IMPORT_RE.finditer(source):
            pattern = m.group(1)
            base_dir = file_path.parent

            # Support glob patterns (e.g. import "nodes/*.pp")
            import glob as glob_mod
            matched = glob_mod.glob(str(base_dir / pattern))
            if not matched:
                self._result.missing_files.append(f"import '{pattern}' from {file_path}")
                self._result.warnings.append(
                    f"Import '{pattern}' in {file_path} matched no files"
                )
                continue

            for imp_path in sorted(matched):
                self._process_file(Path(imp_path), depth + 1)

    def _handle_includes(self, source: str, file_path: Path, depth: int) -> None:
        """Find all ``include`` class names and locate their source files."""
        # Collect include targets from both include statements and require =>
        targets: set[str] = set()
        for m in self._INCLUDE_RE.finditer(source):
            targets.add(m.group(2))
        for m in self._REQUIRE_RE.finditer(source):
            targets.add(m.group(2))

        for class_name in sorted(targets):
            # Already indexed → no need to load again
            if class_name in self._result.class_sources:
                continue

            candidate = self._find_class_file(class_name, file_path)
            if candidate:
                self._process_file(candidate, depth + 1)
            else:
                self._result.missing_files.append(class_name)
                self._result.warnings.append(
                    f"Cannot find source for class '{class_name}' "
                    f"(referenced in {file_path})"
                )

    def _find_class_file(self, class_name: str, referencing_file: Path) -> Path | None:
        """Try to locate the ``.pp`` file that defines *class_name*.

        Search strategy (Puppet module autoloading rules):
        1. ``<module>/manifests/<subclass>.pp`` for ``module::subclass``
        2. ``<module>/manifests/init.pp`` for the module's top-level class
        3. Same directory as the referencing file (flat layout)
        4. All configured module paths
        """
        parts = class_name.split("::")
        module_name = parts[0]
        sub_parts = parts[1:]

        # Try each module path
        search_paths = list(self.module_paths)

        # Also search relative to the referencing file's ancestor module dir
        # (walk up to find a directory that looks like a module root)
        ancestor = self._find_module_root(referencing_file)
        if ancestor and ancestor.parent not in search_paths:
            search_paths.insert(0, ancestor.parent)

        for mod_path in search_paths:
            mod_dir = mod_path / module_name
            if not mod_dir.is_dir():
                continue

            manifests_dir = mod_dir / "manifests"
            if not manifests_dir.is_dir():
                continue

            if sub_parts:
                # e.g. apache::vhost → manifests/vhost.pp
                # e.g. apache::config::ssl → manifests/config/ssl.pp
                candidate = manifests_dir / Path(*sub_parts).with_suffix(".pp")
                if candidate.exists():
                    return candidate
            else:
                # Top-level class → init.pp
                init = manifests_dir / "init.pp"
                if init.exists():
                    return init

        # Flat layout fallback: check same directory as referencing file
        if sub_parts:
            flat = referencing_file.parent / f"{sub_parts[-1]}.pp"
        else:
            flat = referencing_file.parent / f"{module_name}.pp"
        if flat.exists():
            return flat

        return None

    def _find_module_root(self, path: Path) -> Path | None:
        """Walk up from *path* to find the module root directory.

        A module root is a directory that contains a ``manifests/`` subdirectory.
        """
        current = path.parent
        for _ in range(10):  # Max 10 levels up
            if (current / "manifests").is_dir():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    def _read_safe(self, path: Path) -> str | None:
        """Read *path* returning None (and logging a warning) on any error."""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            self._result.missing_files.append(str(path))
            self._result.warnings.append(f"Cannot read {path}: {exc}")
            return None


def resolve_manifest_deps(
    root: str | Path,
    module_paths: Sequence[str | Path] | None = None,
    puppet_version: int = 4,
) -> PreprocessorResult:
    """Convenience function: resolve dependencies of *root* manifest."""
    pp = ManifestPreprocessor(module_paths=module_paths, puppet_version=puppet_version)
    return pp.resolve(root)


def resolve_module_deps(
    module_dir: str | Path,
    module_paths: Sequence[str | Path] | None = None,
    puppet_version: int = 4,
) -> PreprocessorResult:
    """Convenience function: resolve all manifests in a Puppet module."""
    pp = ManifestPreprocessor(module_paths=module_paths, puppet_version=puppet_version)
    return pp.resolve_module(module_dir)
