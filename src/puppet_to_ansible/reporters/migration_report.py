"""Migration Report Generator.

Produces a detailed, human-readable Markdown report of everything that will
be (or was) migrated from Puppet to Ansible. The report covers:

- Executive summary (counts, collections, output mode)
- Every converted resource with its Ansible equivalent
- Every class and defined type discovered
- Node definitions → inventory mapping
- Hiera keys resolved at conversion time
- Warnings with context
- Unconverted resources with TODO guidance
- ERB templates converted
- File-by-file breakdown for multi-file conversions
"""
from __future__ import annotations

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from puppet_to_ansible.converters.manifest_converter import ConversionResult


# ── Report data model ──────────────────────────────────────────────────────────

@dataclass
class FileReport:
    """Report for a single source .pp file."""
    source_path: str
    tasks: list[dict[str, Any]] = field(default_factory=list)
    handlers: list[dict[str, Any]] = field(default_factory=list)
    classes: list[dict[str, Any]] = field(default_factory=list)
    defined_types: list[dict[str, Any]] = field(default_factory=list)
    node_definitions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unconverted: list[dict[str, str]] = field(default_factory=list)
    converted_counts: dict[str, int] = field(default_factory=dict)
    parse_error: str = ""


@dataclass
class MigrationReport:
    """Full migration report for one conversion run."""

    # Metadata
    generated_at: str = ""
    source_root: str = ""
    output_dir: str = ""
    puppet_version: int = 4
    output_mode: str = "auto"
    hiera_config: str = ""
    module_paths: list[str] = field(default_factory=list)

    # Per-file breakdown
    file_reports: list[FileReport] = field(default_factory=list)

    # Aggregated totals
    total_result: ConversionResult | None = None

    # ERB templates converted
    templates_converted: list[dict[str, str]] = field(default_factory=list)

    # Hiera keys that were resolved to real values
    hiera_resolved: dict[str, Any] = field(default_factory=dict)

    # Parse errors (files that could not be parsed)
    parse_errors: list[dict[str, str]] = field(default_factory=list)


# ── Report builder ─────────────────────────────────────────────────────────────

class MigrationReportBuilder:
    """Incrementally builds a MigrationReport during conversion."""

    def __init__(
        self,
        source_root: str = "",
        output_dir: str = "",
        puppet_version: int = 4,
        output_mode: str = "auto",
        hiera_config: str = "",
        module_paths: list[str] | None = None,
    ) -> None:
        self.report = MigrationReport(
            generated_at=datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
            source_root=source_root,
            output_dir=output_dir,
            puppet_version=puppet_version,
            output_mode=output_mode,
            hiera_config=hiera_config,
            module_paths=module_paths or [],
        )

    def add_file_result(self, source_path: str, result: ConversionResult) -> None:
        fr = FileReport(
            source_path=source_path,
            tasks=result.tasks,
            handlers=result.handlers,
            classes=result.classes,
            defined_types=result.defined_types,
            node_definitions=result.node_definitions,
            warnings=result.warnings,
            unconverted=result.unconverted,
            converted_counts=dict(result.converted_counts),
        )
        self.report.file_reports.append(fr)

    def add_parse_error(self, source_path: str, error: str) -> None:
        self.report.parse_errors.append({"file": source_path, "error": error})
        fr = FileReport(source_path=source_path, parse_error=error)
        self.report.file_reports.append(fr)

    def add_template(self, src: str, dest: str, warnings: list[str] | None = None) -> None:
        self.report.templates_converted.append({
            "src": src,
            "dest": dest,
            "warnings": "; ".join(warnings or []),
        })

    def add_hiera_resolved(self, key: str, value: Any) -> None:
        self.report.hiera_resolved[key] = value

    def set_total_result(self, result: ConversionResult) -> None:
        self.report.total_result = result

    def build(self) -> MigrationReport:
        return self.report


# ── Markdown renderer ──────────────────────────────────────────────────────────

class MarkdownReportRenderer:
    """Renders a MigrationReport as a Markdown document."""

    def render(self, report: MigrationReport) -> str:
        lines: list[str] = []
        total = report.total_result

        # ── Header ──────────────────────────────────────────────────────────
        lines += [
            "# p2a Migration Report",
            "",
            f"**Generated:** {report.generated_at}  ",
            f"**Source:** `{report.source_root or 'N/A'}`  ",
            f"**Output:** `{report.output_dir or 'N/A'}`  ",
            f"**Puppet version:** {report.puppet_version}  ",
            f"**Output mode:** {report.output_mode}  ",
        ]
        if report.hiera_config:
            lines.append(f"**Hiera config:** `{report.hiera_config}`  ")
        if report.module_paths:
            lines.append(f"**Module paths:** `{', '.join(report.module_paths)}`  ")
        lines.append("")

        # ── Executive Summary ────────────────────────────────────────────────
        lines += ["---", "", "## Executive Summary", ""]

        if total:
            conv_total = total.total_converted
            lines += [
                f"| Metric | Value |",
                f"|--------|-------|",
                f"| Resources converted | **{conv_total}** |",
                f"| Tasks generated | {len(total.tasks)} |",
                f"| Handlers generated | {len(total.handlers)} |",
                f"| Classes discovered | {len(total.classes)} |",
                f"| Defined types discovered | {len(total.defined_types)} |",
                f"| Node definitions | {len(total.node_definitions)} |",
                f"| ERB templates converted | {len(report.templates_converted)} |",
                f"| Hiera keys resolved | {len(report.hiera_resolved)} |",
                f"| Warnings | {len(total.warnings)} |",
                f"| Unconverted resources | {len(total.unconverted)} |",
                f"| Files with parse errors | {len(report.parse_errors)} |",
                "",
            ]

            if total.collections:
                lines += [
                    "### Ansible Collections Required",
                    "",
                    "```bash",
                    "ansible-galaxy collection install -r requirements.yml",
                    "```",
                    "",
                    "| Collection | Purpose |",
                    "|-----------|---------|",
                ]
                collection_notes = {
                    "ansible.posix": "mount, authorized_key, seboolean, firewalld",
                    "community.general": "ini_file, xml, ufw",
                    "community.docker": "docker containers and images",
                    "community.mysql": "MySQL databases and users",
                    "kubernetes.core": "Kubernetes resources",
                }
                for col in sorted(total.collections):
                    note = collection_notes.get(col, "")
                    lines.append(f"| `{col}` | {note} |")
                lines.append("")

            if total.converted_counts:
                lines += [
                    "### Converted Resources by Type",
                    "",
                    "| Puppet Resource | Count | Ansible Module |",
                    "|----------------|-------|----------------|",
                ]
                module_map = {
                    "package": "ansible.builtin.package / apt / yum / pip",
                    "service": "ansible.builtin.service / systemd",
                    "file": "ansible.builtin.copy / template / file",
                    "exec": "ansible.builtin.command / shell",
                    "user": "ansible.builtin.user",
                    "group": "ansible.builtin.group",
                    "cron": "ansible.builtin.cron",
                    "mount": "ansible.posix.mount",
                    "ssh_authorized_key": "ansible.posix.authorized_key",
                    "host": "ansible.builtin.lineinfile (/etc/hosts)",
                    "yumrepo": "ansible.builtin.yum_repository",
                    "firewall": "ansible.posix.firewalld / community.general.ufw",
                    "augeas": "ansible.builtin.lineinfile / community.general.ini_file",
                    "selboolean": "ansible.posix.seboolean",
                    "ini_setting": "community.general.ini_file",
                    "file_line": "ansible.builtin.lineinfile",
                }
                for rtype, count in sorted(total.converted_counts.items()):
                    ansible_mod = module_map.get(rtype, "ansible.builtin.*")
                    lines.append(f"| `{rtype}` | {count} | `{ansible_mod}` |")
                lines.append("")

        # ── File-by-file breakdown ───────────────────────────────────────────
        if report.file_reports:
            lines += ["---", "", "## File-by-File Breakdown", ""]

            for fr in report.file_reports:
                src_short = Path(fr.source_path).name if fr.source_path else "unknown"
                lines += [f"### `{src_short}`", ""]
                if fr.source_path:
                    lines.append(f"**Source:** `{fr.source_path}`  ")

                if fr.parse_error:
                    lines += [
                        "",
                        f"> ❌ **Parse error:** {fr.parse_error}",
                        "",
                    ]
                    continue

                total_conv = sum(fr.converted_counts.values())
                lines.append(f"**Converted:** {total_conv} resources | "
                              f"**Tasks:** {len(fr.tasks)} | "
                              f"**Warnings:** {len(fr.warnings)} | "
                              f"**Unconverted:** {len(fr.unconverted)}")
                lines.append("")

                # Tasks
                if fr.tasks:
                    lines += ["#### Tasks Generated", ""]
                    for task in fr.tasks:
                        name = task.get("name", "unnamed")
                        module = next((k for k in task if k not in ("name", "when", "notify",
                                       "register", "loop", "become", "become_user", "tags")), "?")
                        when = task.get("when", "")
                        notify = task.get("notify", "")
                        row = f"- **{name}** → `{module}`"
                        if when:
                            row += f"  *(when: `{when}`)*"
                        if notify:
                            row += f"  → notify: `{notify}`"
                        lines.append(row)
                    lines.append("")

                # Handlers
                if fr.handlers:
                    lines += ["#### Handlers", ""]
                    for h in fr.handlers:
                        hname = h.get("name", "unnamed")
                        module = next((k for k in h if k != "name"), "?")
                        lines.append(f"- **{hname}** → `{module}`")
                    lines.append("")

                # Classes
                if fr.classes:
                    lines += ["#### Classes Discovered", ""]
                    for cls in fr.classes:
                        cname = cls.get("name", "?")
                        params = cls.get("parameters", [])
                        ntasks = len(cls.get("tasks", []))
                        lines.append(f"- **`{cname}`** — {len(params)} parameter(s), {ntasks} task(s)")
                    lines.append("")

                # Defined types
                if fr.defined_types:
                    lines += ["#### Defined Types Discovered", ""]
                    for dt in fr.defined_types:
                        dtname = dt.get("name", "?")
                        params = dt.get("parameters", [])
                        lines.append(f"- **`{dtname}`** — {len(params)} parameter(s)")
                    lines.append("")

                # Node definitions
                if fr.node_definitions:
                    lines += ["#### Node Definitions → Inventory", ""]
                    for nd in fr.node_definitions:
                        hosts = nd.get("hosts", "?")
                        roles = nd.get("roles", [])
                        lines.append(f"- `{hosts}` → roles: {', '.join(f'`{r}`' for r in roles) or 'none'}")
                    lines.append("")

                # Warnings
                if fr.warnings:
                    lines += ["#### Warnings", ""]
                    for w in fr.warnings:
                        lines.append(f"> ⚠️  {w}")
                    lines.append("")

                # Unconverted
                if fr.unconverted:
                    lines += ["#### Unconverted Resources", ""]
                    lines += [
                        "These resources have no automatic converter. "
                        "A `# TODO` task has been generated in the output.",
                        "",
                        "| Type | Title | Reason |",
                        "|------|-------|--------|",
                    ]
                    for u in fr.unconverted:
                        lines.append(f"| `{u['type']}` | `{u['title']}` | {u.get('reason', '')} |")
                    lines.append("")

        # ── ERB Templates ────────────────────────────────────────────────────
        if report.templates_converted:
            lines += ["---", "", "## ERB Templates Converted", ""]
            lines += [
                "| Source (ERB) | Destination (Jinja2) | Notes |",
                "|-------------|---------------------|-------|",
            ]
            for t in report.templates_converted:
                src  = Path(t["src"]).name
                dest = Path(t["dest"]).name
                warn = t.get("warnings", "")
                lines.append(f"| `{src}` | `{dest}` | {warn} |")
            lines.append("")

        # ── Hiera Keys Resolved ──────────────────────────────────────────────
        if report.hiera_resolved:
            lines += ["---", "", "## Hiera Keys Resolved at Conversion Time", ""]
            lines += [
                "These Puppet `lookup()` / `hiera()` calls were resolved to real values "
                "from your Hiera data. The values are embedded directly in the generated "
                "Ansible `defaults/main.yml` or task parameters.",
                "",
                "| Hiera Key | Resolved Value |",
                "|-----------|---------------|",
            ]
            for key, val in sorted(report.hiera_resolved.items()):
                val_str = json.dumps(val) if not isinstance(val, str) else val
                lines.append(f"| `{key}` | `{val_str}` |")
            lines.append("")

        # ── Parse Errors ─────────────────────────────────────────────────────
        if report.parse_errors:
            lines += ["---", "", "## Parse Errors", ""]
            lines += [
                "> These files could not be parsed and were skipped. "
                "Fix the syntax errors and re-run p2a.",
                "",
            ]
            for pe in report.parse_errors:
                lines += [
                    f"### `{Path(pe['file']).name}`",
                    "",
                    f"**File:** `{pe['file']}`",
                    "",
                    f"```",
                    pe["error"],
                    f"```",
                    "",
                ]

        # ── What Was NOT Converted ───────────────────────────────────────────
        if total and total.unconverted:
            lines += ["---", "", "## What Was NOT Converted (Action Required)", ""]
            lines += [
                "The following resources could not be automatically converted. "
                "Each has a `# TODO: Manual conversion needed` task in the output. "
                "Review and replace each TODO with the appropriate Ansible task.",
                "",
                "| # | Type | Title | Reason | Suggested Action |",
                "|---|------|-------|--------|-----------------|",
            ]
            actions = {
                "no converter available": "Write a custom task or use `ansible.builtin.command`",
                "custom type": "Rewrite as a custom Ansible module in Python",
                "too complex": "Use `ansible.builtin.template` with a Jinja2 template",
            }
            for i, u in enumerate(total.unconverted, 1):
                reason = u.get("reason", "")
                action = next((v for k, v in actions.items() if k in reason.lower()), "Manual review required")
                lines.append(f"| {i} | `{u['type']}` | `{u['title']}` | {reason} | {action} |")
            lines.append("")

        # ── Next Steps ───────────────────────────────────────────────────────
        lines += [
            "---",
            "",
            "## Recommended Next Steps",
            "",
            "1. **Install required collections:**",
            "   ```bash",
            "   ansible-galaxy collection install -r requirements.yml",
            "   ```",
            "",
            "2. **Review TODO tasks** in the generated files — search for `# TODO:`",
            "",
            "3. **Validate with ansible-lint:**",
            "   ```bash",
            "   ansible-lint roles/",
            "   ```",
            "",
            "4. **Test in dry-run mode:**",
            "   ```bash",
            "   ansible-playbook site.yml --check --diff -i inventory/hosts.yml",
            "   ```",
            "",
            "5. **Review Hiera variables** not resolved at conversion time "
            "(they appear as `{{ variable_name }}` in the output).",
            "",
        ]

        if total and total.unconverted:
            lines += [
                "6. **Fix unconverted resources** — see the table above.",
                "",
            ]

        lines += [
            "---",
            "",
            f"*Generated by [p2a](https://github.com/your-org/puppet-to-ansible) — "
            f"Puppet to Ansible Converter*",
            "",
        ]

        return "\n".join(lines)


# ── JSON renderer ──────────────────────────────────────────────────────────────

class JsonReportRenderer:
    """Renders a MigrationReport as a structured JSON document."""

    def render(self, report: MigrationReport) -> str:
        total = report.total_result
        data = {
            "meta": {
                "generated_at": report.generated_at,
                "source_root": report.source_root,
                "output_dir": report.output_dir,
                "puppet_version": report.puppet_version,
                "output_mode": report.output_mode,
                "hiera_config": report.hiera_config,
                "module_paths": report.module_paths,
            },
            "summary": {
                "resources_converted": total.total_converted if total else 0,
                "tasks_generated": len(total.tasks) if total else 0,
                "handlers_generated": len(total.handlers) if total else 0,
                "classes_discovered": len(total.classes) if total else 0,
                "defined_types_discovered": len(total.defined_types) if total else 0,
                "node_definitions": len(total.node_definitions) if total else 0,
                "templates_converted": len(report.templates_converted),
                "hiera_keys_resolved": len(report.hiera_resolved),
                "warnings": len(total.warnings) if total else 0,
                "unconverted": len(total.unconverted) if total else 0,
                "parse_errors": len(report.parse_errors),
                "collections_required": sorted(total.collections) if total else [],
                "converted_by_type": total.converted_counts if total else {},
            },
            "files": [
                {
                    "source": fr.source_path,
                    "parse_error": fr.parse_error,
                    "converted": sum(fr.converted_counts.values()),
                    "converted_by_type": fr.converted_counts,
                    "tasks": len(fr.tasks),
                    "handlers": len(fr.handlers),
                    "classes": [c.get("name") for c in fr.classes],
                    "defined_types": [d.get("name") for d in fr.defined_types],
                    "nodes": [n.get("hosts") for n in fr.node_definitions],
                    "warnings": fr.warnings,
                    "unconverted": fr.unconverted,
                }
                for fr in report.file_reports
            ],
            "templates": report.templates_converted,
            "hiera_resolved": report.hiera_resolved,
            "parse_errors": report.parse_errors,
            "unconverted": total.unconverted if total else [],
            "warnings": total.warnings if total else [],
        }
        return json.dumps(data, indent=2, ensure_ascii=False)


# ── Public API ─────────────────────────────────────────────────────────────────

def write_report(
    report: MigrationReport,
    output_path: str | Path,
    fmt: str = "markdown",
) -> Path:
    """Write a MigrationReport to a file.

    Args:
        report:      The report to write.
        output_path: Destination file path (.md or .json).
        fmt:         'markdown' or 'json'.

    Returns:
        The path where the report was written.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "json":
        content = JsonReportRenderer().render(report)
    else:
        content = MarkdownReportRenderer().render(report)

    path.write_text(content, encoding="utf-8")
    return path
