"""host → ansible.builtin.lineinfile (writes /etc/hosts entries)."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ArrayLiteral, ResourceBody


class HostConverter(BaseConverter):
    puppet_type = "host"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))  # hostname
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        ip_node      = body.get_attr("ip")
        aliases_node = body.get_attr("host_aliases")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        ip = str(self.resolve(ip_node, context)) if ip_node else ""

        aliases = []
        if aliases_node:
            al = self.resolve(aliases_node, context)
            aliases = al if isinstance(al, list) else [str(al)]

        if state == "absent":
            params: dict[str, Any] = {
                "path":   "/etc/hosts",
                "regexp": rf"^[^#]*\s+{title}\b",
                "state":  "absent",
            }
            return [self.make_task(
                name=f"Remove /etc/hosts entry for {title}",
                module="ansible.builtin.lineinfile",
                params=params,
                notify=notify or None,
                when=when,
            )]

        # Build the hosts line: "192.168.1.1 hostname alias1 alias2"
        line_parts = [ip, title] + aliases
        line = " ".join(p for p in line_parts if p)

        params = {
            "path":   "/etc/hosts",
            "line":   line,
            "regexp": rf"^[^#]*\s+{title}\b",
            "state":  "present",
        }
        return [self.make_task(
            name=f"Manage /etc/hosts entry for {title}",
            module="ansible.builtin.lineinfile",
            params=params,
            notify=notify or None,
            when=when,
        )]
