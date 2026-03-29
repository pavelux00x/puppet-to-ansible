"""Hiera variable resolution engine for p2a.

Loads a Hiera hierarchy (hiera.yaml v3/v4/v5) and resolves ``hiera()`` /
``lookup()`` calls at conversion time, substituting concrete values into the
AST before the Ansible generator runs.

Supports:
- Hiera v3 (`:hierarchy:`, ``:datadir:``)
- Hiera v4/v5 (``version: 4/5``, ``hierarchy:``, ``defaults:``)
- Merge strategies: ``first`` (default), ``hash``, ``deep``, ``unique``
- Interpolation tokens ``%{variable}``, ``%{::variable}``, ``%{facts.key}``
- Module-level Hiera data (``data/`` directory inside a module)
- Lookup from a ConversionContext variable scope
"""
from __future__ import annotations

import logging
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Sentinel for "no default provided" (avoids None ambiguity)
class _NoDefault:
    pass
_SENTINEL = _NoDefault()

# Interpolation token: %{variable}, %{::variable}, %{facts.key.subkey}
_INTERP_RE = re.compile(r"%\{([^}]+)\}")


class HieraResolverError(Exception):
    """Raised when Hiera configuration is malformed or unresolvable."""


class HieraResolver:
    """Resolves Hiera keys against a hierarchy of YAML data files.

    Args:
        hiera_config:   Path to ``hiera.yaml`` (v3, v4, or v5).
        module_data_dir: Path to a module's ``data/`` directory for
                          module-level Hiera data (optional).
        facts:          A dict of fact name → value used for hierarchy
                        interpolation (e.g. ``{'osfamily': 'Debian'}``).
        variables:      Additional Puppet variables used for interpolation.
    """

    def __init__(
        self,
        hiera_config: str | Path | None = None,
        module_data_dir: str | Path | None = None,
        facts: dict[str, Any] | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        if yaml is None:
            raise HieraResolverError(
                "PyYAML is not installed; cannot resolve Hiera data. "
                "Install it with: pip install pyyaml"
            )

        self.facts: dict[str, Any] = facts or {}
        self.variables: dict[str, Any] = variables or {}

        self._data_layers: list[dict[str, Any]] = []  # ordered: most specific → least

        if hiera_config:
            self._load_hiera_config(Path(hiera_config))

        if module_data_dir:
            self._load_module_data(Path(module_data_dir))

    # ── Public API ────────────────────────────────────────────────────────────

    def lookup(
        self,
        key: str,
        merge: str = "first",
        default: Any = _SENTINEL,
    ) -> Any:
        """Look up *key* in the Hiera hierarchy.

        Args:
            key:     Hiera key (e.g. ``'apache::port'`` or ``'apache_port'``).
            merge:   Merge strategy: ``'first'``, ``'hash'``, ``'deep'``,
                     ``'unique'``.  ``'first'`` returns the first match.
            default: Returned when the key is not found.  If omitted a
                     ``KeyError`` is raised for missing keys.

        Returns:
            The resolved value (Python object: str, int, bool, list, dict).
        """
        matches: list[Any] = []

        for layer in self._data_layers:
            val = layer.get(key)
            if val is None:
                # Try :: → _ normalisation (Puppet module::key → module_key)
                normalised = key.replace("::", "_")
                val = layer.get(normalised)

            if val is not None:
                val = self._interpolate(val)
                if merge == "first":
                    return val
                matches.append(val)

        if not matches:
            if not isinstance(default, _NoDefault):
                return default
            raise KeyError(f"Hiera key not found: {key!r}")

        return self._merge(matches, strategy=merge)

    def lookup_all(self) -> dict[str, Any]:
        """Return a merged view of all keys across the entire hierarchy.

        Later layers (less specific) are overridden by earlier layers.
        """
        merged: dict[str, Any] = {}
        for layer in reversed(self._data_layers):
            merged.update(layer)
        # Interpolate all values
        return {k: self._interpolate(v) for k, v in merged.items()}

    def update_facts(self, facts: dict[str, Any]) -> None:
        """Update the fact scope used for hierarchy interpolation."""
        self.facts.update(facts)

    def update_variables(self, variables: dict[str, Any]) -> None:
        """Update the variable scope used for hierarchy interpolation."""
        self.variables.update(variables)

    # ── Hiera config loading ──────────────────────────────────────────────────

    def _load_hiera_config(self, config_path: Path) -> None:
        if not config_path.exists():
            logger.warning("hiera.yaml not found at %s", config_path)
            return

        try:
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error("Failed to parse %s: %s", config_path, exc)
            return

        version = raw.get("version", 3)

        if version == 3 or isinstance(raw.get(":hierarchy"), list):
            self._load_v3(raw, config_path.parent)
        elif version in (4, 5):
            self._load_v5(raw, config_path.parent)
        else:
            logger.warning("Unknown hiera.yaml version %s, trying v5 parser", version)
            self._load_v5(raw, config_path.parent)

    def _load_v3(self, raw: dict, base_dir: Path) -> None:
        """Parse Hiera v3 config (Ruby-symbol keys like ``:hierarchy:``)."""
        hierarchy: list[str] = raw.get(":hierarchy", raw.get("hierarchy", ["common"]))
        datadir_key = ":datadir" if ":datadir" in raw else "datadir"
        datadir = Path(raw.get(datadir_key, str(base_dir / "hieradata")))
        if not datadir.is_absolute():
            datadir = base_dir / datadir

        for level in hierarchy:
            expanded = self._expand_interpolation(str(level))
            # Multiple expansions possible if the fact has several values?
            # (Puppet uses only the first match per level)
            candidate = datadir / f"{expanded}.yaml"
            if not candidate.exists():
                candidate = datadir / f"{expanded}.yml"
            if candidate.exists():
                self._load_yaml_layer(candidate)
            else:
                logger.debug("Hiera v3: no data file for level '%s' (%s)", level, candidate)

    def _load_v5(self, raw: dict, base_dir: Path) -> None:
        """Parse Hiera v4/v5 config."""
        defaults = raw.get("defaults", {})
        default_datadir = base_dir / defaults.get("datadir", "data")
        default_backend = defaults.get("data_hash", "yaml_data")

        for entry in raw.get("hierarchy", [{"name": "common", "path": "common.yaml"}]):
            if not isinstance(entry, dict):
                continue

            # Resolve data directory for this entry
            datadir = Path(entry.get("datadir", str(default_datadir)))
            if not datadir.is_absolute():
                datadir = base_dir / datadir

            # 'path' → single file; 'paths' → list of files; 'glob' → glob pattern
            paths: list[str] = []
            if "path" in entry:
                paths.append(entry["path"])
            elif "paths" in entry:
                paths.extend(entry["paths"])
            elif "glob" in entry:
                import glob as glob_mod
                matched = glob_mod.glob(str(datadir / entry["glob"]))
                paths.extend(str(Path(p).relative_to(datadir)) for p in sorted(matched))

            for p in paths:
                expanded = self._expand_interpolation(p)
                candidate = datadir / expanded
                if not candidate.suffix:
                    candidate = candidate.with_suffix(".yaml")
                if candidate.exists():
                    self._load_yaml_layer(candidate)
                else:
                    logger.debug("Hiera v5: no data file '%s'", candidate)

    def _load_module_data(self, data_dir: Path) -> None:
        """Load module-level Hiera data from a ``data/`` directory."""
        if not data_dir.is_dir():
            return

        # common.yaml (or common.yml) is the module default
        for name in ("common.yaml", "common.yml"):
            common = data_dir / name
            if common.exists():
                self._load_yaml_layer(common)
                break

        # OS-specific overrides (os/<family>.yaml, os/<name>.yaml)
        os_dir = data_dir / "os"
        if os_dir.is_dir():
            os_family = self.facts.get("osfamily", self.facts.get("os_family", ""))
            os_name = self.facts.get("operatingsystem", self.facts.get("ansible_distribution", ""))
            for candidate_name in [os_name, os_family]:
                if candidate_name:
                    for ext in (".yaml", ".yml"):
                        f = os_dir / f"{candidate_name}{ext}"
                        if f.exists():
                            self._load_yaml_layer(f)
                            break

    def _load_yaml_layer(self, path: Path) -> None:
        """Load a single YAML data file as one Hiera layer."""
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                self._data_layers.append(data)
                logger.debug("Loaded Hiera layer: %s (%d keys)", path, len(data))
            else:
                logger.warning("Hiera data file %s is not a YAML mapping, skipping", path)
        except yaml.YAMLError as exc:
            logger.warning("Failed to parse Hiera data %s: %s", path, exc)
        except OSError as exc:
            logger.warning("Cannot read Hiera data %s: %s", path, exc)

    # ── Interpolation ─────────────────────────────────────────────────────────

    def _expand_interpolation(self, template: str) -> str:
        """Expand ``%{variable}`` tokens in a hierarchy *path template*.

        Unknown variables expand to ``_unknown_`` so the path just misses
        (no file found) rather than crashing.
        """
        def replace(m: re.Match) -> str:
            token = m.group(1).lstrip(":").strip()
            # facts.key.subkey → drill into facts dict
            if token.startswith("facts."):
                parts = token[6:].split(".")
                val = self.facts
                for part in parts:
                    if isinstance(val, dict):
                        val = val.get(part, "")
                    else:
                        val = ""
                        break
                return str(val) if val else "_unknown_"
            # Plain variable
            val = (
                self.facts.get(token)
                or self.facts.get(token.replace("::", "_"))
                or self.variables.get(token)
                or self.variables.get(token.replace("::", "_"))
            )
            return str(val) if val is not None else "_unknown_"

        return _INTERP_RE.sub(replace, template)

    def _interpolate(self, value: Any) -> Any:
        """Recursively interpolate ``%{token}`` inside a Hiera value."""
        if isinstance(value, str):
            return self._expand_interpolation(value)
        if isinstance(value, list):
            return [self._interpolate(v) for v in value]
        if isinstance(value, dict):
            return {k: self._interpolate(v) for k, v in value.items()}
        return value

    # ── Merge strategies ──────────────────────────────────────────────────────

    @staticmethod
    def _merge(matches: list[Any], strategy: str) -> Any:
        if strategy == "unique":
            seen: list[Any] = []
            for m in matches:
                if isinstance(m, list):
                    for item in m:
                        if item not in seen:
                            seen.append(item)
                elif m not in seen:
                    seen.append(m)
            return seen

        if strategy in ("hash", "deep"):
            merged: dict[str, Any] = {}
            for m in reversed(matches):  # least specific first → most specific wins
                if isinstance(m, dict):
                    if strategy == "deep":
                        merged = _deep_merge(merged, m)
                    else:
                        merged.update(m)
            return merged

        # 'first' — return first match (already handled in lookup())
        return matches[0] if matches else None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    result = deepcopy(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = deepcopy(val)
    return result


# ── Hiera-aware variable resolver (used from ManifestConverter) ───────────────

class HieraAwareScope:
    """Wraps a ConversionContext variable scope with Hiera fallback lookup.

    When a variable is not found in the Puppet scope, this resolver
    tries Hiera.  This eliminates the need to run Puppet to resolve
    hiera() / lookup() calls in class parameter defaults.

    Usage::

        resolver = HieraAwareScope(hiera_resolver, puppet_vars)
        value = resolver.get('apache::port', default=80)
    """

    def __init__(
        self,
        hiera: HieraResolver | None,
        scope: dict[str, Any] | None = None,
    ) -> None:
        self._hiera = hiera
        self._scope = scope or {}

    def get(self, key: str, default: Any = None, merge: str = "first") -> Any:
        """Resolve *key* from scope first, then Hiera, then *default*."""
        # 1. Check Puppet variable scope (strip leading $)
        clean_key = key.lstrip("$").lstrip(":")
        if clean_key in self._scope:
            return self._scope[clean_key]
        # Also try :: → _ normalisation
        norm_key = clean_key.replace("::", "_")
        if norm_key in self._scope:
            return self._scope[norm_key]

        # 2. Try Hiera lookup
        if self._hiera is not None:
            try:
                return self._hiera.lookup(clean_key, merge=merge)
            except KeyError:
                pass
            try:
                return self._hiera.lookup(norm_key, merge=merge)
            except KeyError:
                pass

        return default

    def set(self, key: str, value: Any) -> None:
        """Set a variable in the local scope."""
        self._scope[key.lstrip("$").lstrip(":")] = value

    def all_vars(self) -> dict[str, Any]:
        """Return all variables in scope (does NOT include Hiera fallbacks)."""
        return dict(self._scope)


def build_hiera_resolver(
    manifest_path: str | Path | None = None,
    module_paths: Sequence[str | Path] | None = None,
    facts: dict[str, Any] | None = None,
    variables: dict[str, Any] | None = None,
) -> HieraResolver | None:
    """Best-effort: find hiera.yaml near *manifest_path* and build a resolver.

    Returns None if neither hiera.yaml is found nor any data directory is
    reachable (so callers can proceed without Hiera support).
    """
    if yaml is None:
        logger.warning("PyYAML not installed — Hiera resolution disabled")
        return None

    search_dirs: list[Path] = []

    if manifest_path:
        p = Path(manifest_path).resolve()
        # Walk up looking for hiera.yaml (stop at filesystem root or after 8 levels)
        current = p.parent
        for _ in range(8):
            search_dirs.append(current)
            parent = current.parent
            if parent == current:
                break
            current = parent

    for mod_path in (module_paths or []):
        search_dirs.append(Path(mod_path).parent)

    for d in search_dirs:
        for name in ("hiera.yaml", "hiera.yml"):
            candidate = d / name
            if candidate.exists():
                logger.info("Using Hiera config: %s", candidate)
                return HieraResolver(
                    hiera_config=candidate,
                    facts=facts,
                    variables=variables,
                )

    logger.debug("No hiera.yaml found near %s — Hiera resolution disabled", manifest_path)
    return None
