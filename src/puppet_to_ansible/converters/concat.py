"""concat / concat::fragment → ansible.builtin.assemble pattern.

Puppet's ``concat`` + ``concat::fragment`` build a file from ordered fragments.
The Ansible equivalent uses ``ansible.builtin.copy`` to write each fragment
to a staging directory, then ``ansible.builtin.assemble`` to merge them into
the final destination file.

Staging directory convention (mirrors Puppet's internal path):
  /tmp/.p2a_concat_fragments/<dest_path_sanitised>/

Fragment filenames are zero-padded order prefixes so ``assemble`` sorts them
correctly, e.g. ``01_header``, ``10_database``.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody

_STAGING_BASE = "/tmp/.p2a_concat_fragments"


def _staging_dir(dest: str) -> str:
    """Derive a stable staging directory path from the destination file path."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", dest.lstrip("/"))[:60]
    # Append a short hash to avoid collisions if two paths sanitise identically
    short_hash = hashlib.md5(dest.encode()).hexdigest()[:6]
    return f"{_STAGING_BASE}/{safe}_{short_hash}"


class ConcatConverter(BaseConverter):
    """Converts Puppet ``concat`` resources to an ``assemble`` task."""

    puppet_type = "concat"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        dest    = str(self.resolve_title(body, context))
        when    = self.get_when(body, context)
        notify  = self.get_notify(body, context)

        owner_node = body.get_attr("owner")
        group_node = body.get_attr("group")
        mode_node  = body.get_attr("mode")

        owner = str(self.resolve(owner_node, context)) if owner_node else "root"
        group = str(self.resolve(group_node, context)) if group_node else "root"
        mode  = str(self.resolve(mode_node,  context)) if mode_node  else "0644"

        staging = _staging_dir(dest)

        # Task 1: ensure staging directory exists
        mkdir_task: dict[str, Any] = {
            "name": f"Ensure concat staging dir for {dest}",
            "ansible.builtin.file": {
                "path": staging,
                "state": "directory",
                "mode": "0700",
            },
        }

        # Task 2: assemble fragments into the final file
        assemble_params: dict[str, Any] = {
            "src": staging,
            "dest": dest,
            "owner": owner,
            "group": group,
            "mode": mode,
        }
        assemble_task: dict[str, Any] = {
            "name": f"Assemble {dest} from concat fragments",
            "ansible.builtin.assemble": assemble_params,
        }
        if notify:
            assemble_task["notify"] = notify
        if when:
            mkdir_task["when"] = when
            assemble_task["when"] = when

        return [mkdir_task, assemble_task]


class ConcatFragmentConverter(BaseConverter):
    """Converts Puppet ``concat::fragment`` resources to a ``copy`` task."""

    puppet_type = "concat::fragment"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title = str(self.resolve_title(body, context))
        when  = self.get_when(body, context)

        target_node  = body.get_attr("target")
        content_node = body.get_attr("content")
        source_node  = body.get_attr("source")
        order_node   = body.get_attr("order")

        target  = str(self.resolve(target_node,  context)) if target_node  else ""
        content = self.resolve(content_node, context)      if content_node  else None
        source  = str(self.resolve(source_node,  context)) if source_node  else None
        order   = self.resolve(order_node, context)        if order_node   else "50"

        # Zero-pad order for correct lexicographic sort by assemble
        try:
            order_str = str(int(str(order))).zfill(2)
        except (ValueError, TypeError):
            order_str = str(order)

        safe_title = re.sub(r"[^a-zA-Z0-9_.-]", "_", title)[:40]
        fragment_name = f"{order_str}_{safe_title}"

        if target:
            staging = _staging_dir(target)
        else:
            staging = _STAGING_BASE + "/unknown"
            context.warn(
                f"concat::fragment '{title}' has no 'target' attribute — "
                f"staging dir cannot be determined"
            )

        fragment_path = f"{staging}/{fragment_name}"

        if content is not None:
            copy_params: dict[str, Any] = {
                "content": str(content),
                "dest": fragment_path,
                "mode": "0600",
            }
        elif source:
            copy_params = {
                "src": source,
                "dest": fragment_path,
                "mode": "0600",
            }
        else:
            copy_params = {
                "content": f"# TODO: concat::fragment '{title}' has no content or source\n",
                "dest": fragment_path,
                "mode": "0600",
            }

        task: dict[str, Any] = {
            "name": f"Write concat fragment '{title}' to staging",
            "ansible.builtin.copy": copy_params,
        }
        if when:
            task["when"] = when
        return [task]
