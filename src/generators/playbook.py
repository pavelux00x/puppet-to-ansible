"""Playbook generator — converts a ConversionResult into a single Ansible playbook YAML."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.converters.manifest_converter import ConversionResult


def _clean_task(task: dict[str, Any]) -> dict[str, Any]:
    """Remove internal keys (prefixed with __) from task dicts."""
    return {k: v for k, v in task.items() if not k.startswith("__")}


def _deduplicate_names(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """L1: Ensure all task `name` fields are unique within the list.

    ansible-lint W306 flags duplicate names in the same play/task file.
    Duplicates get a numeric suffix: 'Install nginx', 'Install nginx (2)', …
    """
    seen: dict[str, int] = {}
    result = []
    for task in tasks:
        name = task.get("name", "")
        if name in seen:
            seen[name] += 1
            task = dict(task)
            task["name"] = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        result.append(task)
    return result


class PlaybookGenerator:
    """Generates an Ansible playbook from a ConversionResult."""

    def generate(
        self,
        result: ConversionResult,
        hosts: str = "all",
        become: bool = True,
    ) -> str:
        """Return the playbook as a YAML string."""
        tasks    = _deduplicate_names([_clean_task(t) for t in result.tasks])
        handlers = _deduplicate_names([_clean_task(h) for h in result.handlers])

        play: dict[str, Any] = {
            "name": f"Converted from {Path(result.source_file).name or 'Puppet manifest'}",
            "hosts": hosts,
        }
        if become:
            play["become"] = True

        if tasks:
            play["tasks"] = tasks
        if handlers:
            play["handlers"] = handlers

        if result.variables:
            play["vars"] = result.variables

        playbook = [play]

        header = _header(result.source_file)
        body = yaml.dump(playbook, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return header + body

    def write(
        self,
        result: ConversionResult,
        output_path: str | Path,
        hosts: str = "all",
        become: bool = True,
    ) -> Path:
        """Write the playbook to a file and return the path."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(self.generate(result, hosts=hosts, become=become), encoding="utf-8")
        return out


class RoleGenerator:
    """Generates an Ansible role directory structure from a ConversionResult."""

    def generate(
        self,
        result: ConversionResult,
        role_dir: str | Path,
        role_name: str = "",
    ) -> Path:
        """Write the full role structure to disk and return the role directory path."""
        base = Path(role_dir)
        base.mkdir(parents=True, exist_ok=True)

        source_name = Path(result.source_file).stem if result.source_file else "converted"
        if not role_name:
            role_name = source_name

        # tasks/main.yml — top-level tasks + include_tasks for each class/defined type
        tasks_dir = base / "tasks"
        tasks_dir.mkdir(exist_ok=True)

        main_tasks: list[dict] = _deduplicate_names([_clean_task(t) for t in result.tasks])

        # Write per-class task files and wire them into main.yml via include_tasks
        for cls in result.classes:
            if not cls["tasks"]:
                continue
            safe_name = cls["name"].replace("::", "_")
            cls_file = tasks_dir / f"{safe_name}.yml"
            _write_yaml_file(cls_file, _deduplicate_names([_clean_task(t) for t in cls["tasks"]]), result.source_file)
            main_tasks.append({
                "name": f"Include tasks for {cls['name']}",
                "ansible.builtin.include_tasks": f"{safe_name}.yml",
            })

        # Write per-defined-type task files and wire them into main.yml
        for dt in result.defined_types:
            if not dt["tasks"]:
                continue
            safe_name = dt["name"].replace("::", "_")
            dt_file = tasks_dir / f"{safe_name}.yml"
            _write_yaml_file(dt_file, _deduplicate_names([_clean_task(t) for t in dt["tasks"]]), result.source_file)
            main_tasks.append({
                "name": f"Include tasks for defined type {dt['name']}",
                "ansible.builtin.include_tasks": f"{safe_name}.yml",
            })

        _write_yaml_file(tasks_dir / "main.yml", main_tasks, result.source_file)

        # handlers/main.yml
        if result.handlers:
            handlers_dir = base / "handlers"
            handlers_dir.mkdir(exist_ok=True)
            _write_yaml_file(
                handlers_dir / "main.yml",
                [_clean_task(h) for h in result.handlers],
                result.source_file,
            )

        # defaults/main.yml — class parameters + extracted variables
        defaults: dict[str, Any] = {}
        for cls in result.classes:
            defaults.update(cls.get("vars", {}))
        defaults.update(result.variables)
        if defaults:
            defaults_dir = base / "defaults"
            defaults_dir.mkdir(exist_ok=True)
            _write_yaml_file(defaults_dir / "main.yml", defaults, result.source_file)

        # meta/main.yml
        meta_dir = base / "meta"
        meta_dir.mkdir(exist_ok=True)
        meta: dict[str, Any] = {
            "galaxy_info": {
                "role_name": role_name,
                "author": "p2a-converted",
                "description": f"Converted from Puppet by p2a (source: {result.source_file})",
                "license": "Apache-2.0",
                "min_ansible_version": "2.14",
            },
            "dependencies": [],
        }
        _write_yaml_file(meta_dir / "main.yml", meta, result.source_file)

        return base

    def write_requirements(
        self,
        result: ConversionResult,
        output_path: str | Path,
    ) -> Path | None:
        """Write requirements.yml if collections are needed."""
        if not result.collections:
            return None
        # Minimum tested versions per collection
        _MIN_VERSIONS: dict[str, str] = {
            "ansible.posix":        ">=1.5.0",
            "community.general":    ">=9.0.0",
            "community.docker":     ">=3.0.0",
            "community.mysql":      ">=3.0.0",
            "community.postgresql": ">=3.0.0",
            "community.crypto":     ">=2.0.0",
            "kubernetes.core":      ">=3.0.0",
        }
        req: dict[str, Any] = {
            "collections": [
                {"name": col, "version": _MIN_VERSIONS.get(col, ">=1.0.0")}
                for col in sorted(result.collections)
            ]
        }
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        header = _header("")
        body = yaml.dump(req, default_flow_style=False, allow_unicode=True, sort_keys=False)
        out.write_text(header + body, encoding="utf-8")
        return out


class InventoryGenerator:
    """Generates an Ansible inventory from node definitions in a ConversionResult."""

    def generate(self, result: ConversionResult) -> str:
        """Return inventory YAML string."""
        if not result.node_definitions:
            return ""

        groups: dict[str, list[str]] = {}
        host_vars: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []

        for node_def in result.node_definitions:
            if node_def["is_default"]:
                continue
            for matcher in node_def["matchers"]:
                if matcher["type"] == "exact":
                    hostname = matcher["value"]
                    # Determine group from task content (include_role names)
                    group = _infer_group(node_def["tasks"])
                    groups.setdefault(group, []).append(hostname)
                elif matcher["type"] == "regex":
                    pattern = matcher["pattern"]
                    warnings.append(
                        f"Regex node definition '/{pattern}/' — "
                        f"add matching hosts manually to the inventory."
                    )
                    # L4: add a placeholder group so the operator has a clear anchor
                    safe_group = re.sub(r"[^a-zA-Z0-9_]", "_", pattern).strip("_")[:40] or "regex_nodes"
                    placeholder_group = f"regex_{safe_group}"
                    if placeholder_group not in groups:
                        groups[placeholder_group] = []  # empty — operator must fill it in

        inventory: dict[str, Any] = {"all": {"children": {}}}
        for group, hosts in groups.items():
            inventory["all"]["children"][group] = {
                "hosts": {h: None for h in hosts}
            }

        header = "# Generated by p2a — review before use\n"
        if warnings:
            header += "".join(f"# WARNING: {w}\n" for w in warnings)
        header += "---\n"

        return header + yaml.dump(inventory, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _header(source_file: str) -> str:
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    src = source_file or "unknown"
    return (
        f"# Generated by p2a (Puppet to Ansible Converter)\n"
        f"# Source: {src}\n"
        f"# Date: {ts}\n"
        f"# Review this file before deploying — automated conversion may need manual adjustments\n"
        f"---\n"
    )


def _write_yaml_file(path: Path, data: Any, source_file: str) -> None:
    header = _header(source_file)
    body = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
    path.write_text(header + body, encoding="utf-8")


def _infer_group(tasks: list[dict[str, Any]]) -> str:
    """Infer a group name from task content (e.g., include_role names)."""
    for task in tasks:
        role_task = task.get("ansible.builtin.include_role", {})
        if isinstance(role_task, dict):
            role_name = role_task.get("name", "")
            if role_name:
                # role::webserver → webservers
                parts = role_name.replace("::", ".").split(".")
                return f"{parts[-1]}s"
    return "ungrouped"
