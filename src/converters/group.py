"""group → ansible.builtin.group."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class GroupConverter(BaseConverter):
    puppet_type = "group"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node = body.get_attr("ensure")
        gid_node    = body.get_attr("gid")
        system_node = body.get_attr("system")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        ensure = "absent" if str(ensure_raw).lower() in ("absent", "purged") else "present"

        params: dict[str, Any] = {"name": title, "state": ensure}

        if gid_node:
            params["gid"] = self.resolve(gid_node, context)
        if system_node:
            sv = self.resolve(system_node, context)
            params["system"] = bool(sv) if isinstance(sv, bool) else str(sv).lower() == "true"

        action = "Remove" if ensure == "absent" else "Manage"
        return [self.make_task(
            name=f"{action} group {title}",
            module="ansible.builtin.group",
            params=params,
            notify=notify or None,
            when=when,
        )]
