"""selboolean → ansible.posix.seboolean."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class SelbooleanConverter(BaseConverter):
    puppet_type = "selboolean"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        context.require_collection("ansible.posix")
        title      = str(self.resolve_title(body, context))
        when       = self.get_when(body, context)

        value_node      = body.get_attr("value")
        persistent_node = body.get_attr("persistent")

        val_raw    = self.resolve(value_node, context) if value_node else "on"
        persistent = True  # Puppet default is persistent=true

        if persistent_node:
            pv = self.resolve(persistent_node, context)
            persistent = bool(pv) if isinstance(pv, bool) else str(pv).lower() == "true"

        state = "yes" if str(val_raw).lower() in ("on", "true", "1", "yes") else "no"

        params: dict[str, Any] = {
            "name":       title,
            "state":      state,
            "persistent": persistent,
        }

        return [self.make_task(
            name=f"Set SELinux boolean {title}={state}",
            module="ansible.posix.seboolean",
            params=params,
            when=when,
        )]
