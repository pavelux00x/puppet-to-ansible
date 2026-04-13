"""augeas → context-aware conversion.

Strategy:
1. Detect the target file from context
2. Use lineinfile for simple set key=value patterns
3. Use community.general.ini_file for INI files
4. Use community.general.xml for XML files
5. Fall back to TODO task for complex cases
"""
from __future__ import annotations

import re
from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ArrayLiteral, ResourceBody, StringLiteral

# Augeas lens → file type mapping
_LENS_TYPE: dict[str, str] = {
    "Sshd":        "lineinfile",
    "Sudoers":     "lineinfile",
    "Hosts":       "lineinfile",
    "Resolv":      "lineinfile",
    "Syslog":      "lineinfile",
    "Cron":        "lineinfile",
    "Interfaces":  "lineinfile",
    "Properties":  "ini",
    "Php":         "ini",
    "MySQL":       "ini",
    "Puppet":      "ini",
    "Xml":         "xml",
    "Json":        "template",
    "Nginx":       "template",
    "Apache":      "template",
}

# Simple: /files/etc/ssh/sshd_config → sshd_config
_CONTEXT_RE = re.compile(r"/files(/.*)")


class AugeasConverter(BaseConverter):
    puppet_type = "augeas"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title    = str(self.resolve_title(body, context))
        when     = self.get_when(body, context)
        notify   = self.get_notify(body, context)

        ctx_node     = body.get_attr("context")
        changes_node = body.get_attr("changes")
        lens_node    = body.get_attr("lens")
        onlyif_node  = body.get_attr("onlyif")

        lens    = str(self.resolve(lens_node, context)) if lens_node else None
        ctx_str = str(self.resolve(ctx_node, context)) if ctx_node else ""

        # Extract target file path from context
        file_path = ""
        m = _CONTEXT_RE.match(ctx_str)
        if m:
            file_path = m.group(1)

        changes: list[str] = []
        if changes_node:
            ch = self.resolve(changes_node, context)
            if isinstance(ch, list):
                changes = [str(c) for c in ch]
            else:
                changes = [str(ch)]

        # Determine conversion strategy
        strategy = self._detect_strategy(lens, file_path, changes)

        if strategy == "lineinfile":
            return self._to_lineinfile(title, file_path, changes, notify, when)
        elif strategy == "ini":
            context.require_collection("community.general")
            return self._to_ini_file(title, file_path, changes, notify, when)
        else:
            context.warn(
                f"augeas[{title}]: context='{ctx_str}' — too complex for auto-conversion. "
                f"Manual review needed. Consider a Jinja2 template."
            )
            context.add_unconverted("augeas", title, "complex augeas expression")
            return [self.todo_task(
                "augeas", title,
                f"Original context: {ctx_str}. Changes: {'; '.join(changes[:3])}"
            )]

    def _detect_strategy(self, lens: str | None, file_path: str, changes: list[str]) -> str:
        if lens:
            lens_base = lens.split(".")[0].replace("@", "")
            t = _LENS_TYPE.get(lens_base)
            if t:
                return t

        if not changes:
            return "todo"

        # All changes are simple "set key value" → lineinfile
        if all(re.match(r"^set\s+\S+\s+", c) for c in changes):
            # If file ends in .ini, .cfg, .conf → try ini
            if file_path and any(file_path.endswith(ext) for ext in (".ini", ".cfg")):
                return "ini"
            return "lineinfile"

        return "todo"

    def _to_lineinfile(
        self,
        title: str,
        file_path: str,
        changes: list[str],
        notify: list[str],
        when: str | None,
    ) -> list[dict[str, Any]]:
        tasks = []
        for change in changes:
            # Parse "set PermitRootLogin no" → key=PermitRootLogin, value=no
            m = re.match(r"^set\s+(\S+)\s+(.*)", change)
            if m:
                key   = m.group(1).strip()
                value = m.group(2).strip().strip("'\"")
                # Build lineinfile that sets "Key value"
                line   = f"{key} {value}"
                regexp = rf"^{re.escape(key)}\s+"
                params: dict[str, Any] = {
                    "path":   file_path or "/etc/unknown",
                    "regexp": regexp,
                    "line":   line,
                }
                tasks.append(self.make_task(
                    name=f"Configure {key} in {file_path or title}",
                    module="ansible.builtin.lineinfile",
                    params=params,
                    notify=notify or None,
                    when=when,
                ))
        return tasks or [self.todo_task("augeas", title)]

    def _to_ini_file(
        self,
        title: str,
        file_path: str,
        changes: list[str],
        notify: list[str],
        when: str | None,
    ) -> list[dict[str, Any]]:
        tasks = []
        for change in changes:
            m = re.match(r"^set\s+(\S+)\s+(.*)", change)
            if m:
                key_path = m.group(1).strip()
                value    = m.group(2).strip().strip("'\"")
                # key_path may be "section/key" or just "key"
                parts = key_path.split("/")
                section = parts[0] if len(parts) > 1 else "DEFAULT"
                key     = parts[-1]
                params: dict[str, Any] = {
                    "path":    file_path,
                    "section": section,
                    "option":  key,
                    "value":   value,
                }
                tasks.append(self.make_task(
                    name=f"Set {section}/{key} in {file_path}",
                    module="community.general.ini_file",
                    params=params,
                    notify=notify or None,
                    when=when,
                ))
        return tasks or [self.todo_task("augeas", title)]
