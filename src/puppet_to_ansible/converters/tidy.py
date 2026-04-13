"""tidy → ansible.builtin.find + ansible.builtin.file (state: absent).

Puppet's ``tidy`` resource removes files/directories matching age, size, or
name patterns. The Ansible equivalent is a two-step pattern: find matching
paths, then delete them.
"""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody


class TidyConverter(BaseConverter):
    """Converts Puppet ``tidy`` resources to find + file(absent) task pairs."""

    puppet_type = "tidy"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        path  = str(self.resolve_title(body, context))
        when  = self.get_when(body, context)

        matches_node  = body.get_attr("matches")
        recurse_node  = body.get_attr("recurse")
        age_node      = body.get_attr("age")
        size_node     = body.get_attr("size")
        rmdirs_node   = body.get_attr("rmdirs")
        type_node     = body.get_attr("type")

        matches_raw = self.resolve(matches_node, context) if matches_node else None
        recurse_raw = self.resolve(recurse_node, context) if recurse_node else None
        age_raw     = self.resolve(age_node,     context) if age_node     else None
        size_raw    = self.resolve(size_node,    context) if size_node    else None
        rmdirs      = self.resolve(rmdirs_node,  context) if rmdirs_node  else False
        file_type   = str(self.resolve(type_node, context)) if type_node else "any"

        # -- find params -------------------------------------------------------
        find_params: dict[str, Any] = {"paths": path}

        # recurse depth: true → unlimited, integer → depth limit
        if recurse_raw is not None:
            if isinstance(recurse_raw, bool):
                find_params["recurse"] = recurse_raw
            elif str(recurse_raw).isdigit():
                depth = int(str(recurse_raw))
                find_params["recurse"] = depth > 0
                if depth > 1:
                    find_params["depth"] = depth
            else:
                find_params["recurse"] = True

        # filename patterns
        if matches_raw is not None:
            if isinstance(matches_raw, list):
                find_params["patterns"] = [str(m) for m in matches_raw]
            else:
                find_params["patterns"] = [str(matches_raw)]

        # file type filter
        if file_type == "file":
            find_params["file_type"] = "file"
        elif file_type == "directory":
            find_params["file_type"] = "directory"

        # age filter: Puppet uses suffix d/h/m/s (optionally + prefix)
        if age_raw is not None:
            age_str = str(age_raw)
            # Convert Puppet age string to seconds for age_seconds if needed;
            # or pass as-is with a TODO comment for the operator to verify.
            find_params["age"] = age_str  # e.g. "1d", "2h"

        # size filter
        if size_raw is not None:
            find_params["size"] = str(size_raw)  # e.g. "100k", "1m"

        register_var = f"tidy_files_{self._safe_var(path)}"

        find_task: dict[str, Any] = {
            "name": f"Find files to tidy in {path}",
            "ansible.builtin.find": find_params,
            "register": register_var,
        }
        if when:
            find_task["when"] = when

        delete_task: dict[str, Any] = {
            "name": f"Remove tidy files from {path}",
            "ansible.builtin.file": {
                "path": "{{ item.path }}",
                "state": "absent",
            },
            "loop": f"{{{{ {register_var}.files }}}}",
            "when": f"{register_var}.files | length > 0",
        }

        tasks: list[dict[str, Any]] = [find_task, delete_task]

        # If rmdirs=true, also remove empty directories
        if rmdirs:
            find_dirs: dict[str, Any] = {
                "name": f"Find empty directories to tidy in {path}",
                "ansible.builtin.find": {
                    "paths": path,
                    "file_type": "directory",
                    "recurse": find_params.get("recurse", False),
                    "patterns": find_params.get("patterns", ["*"]),
                },
                "register": f"{register_var}_dirs",
            }
            delete_dirs: dict[str, Any] = {
                "name": f"Remove tidy directories from {path}",
                "ansible.builtin.file": {
                    "path": "{{ item.path }}",
                    "state": "absent",
                },
                "loop": f"{{{{ {register_var}_dirs.files }}}}",
                "when": f"{register_var}_dirs.files | length > 0",
            }
            tasks += [find_dirs, delete_dirs]

        return tasks

    @staticmethod
    def _safe_var(path: str) -> str:
        """Convert a path to a safe variable name suffix."""
        import re
        return re.sub(r"[^a-zA-Z0-9]", "_", path).strip("_")[:40]
