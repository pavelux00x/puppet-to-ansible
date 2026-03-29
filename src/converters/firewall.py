"""firewall (puppetlabs/firewall) → ansible.posix.firewalld or community.general.ufw."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ArrayLiteral, ResourceBody


class FirewallConverter(BaseConverter):
    puppet_type = "firewall"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        when   = self.get_when(body, context)

        action_node  = body.get_attr("action")
        proto_node   = body.get_attr("proto")
        dport_node   = body.get_attr("dport")
        sport_node   = body.get_attr("sport")
        source_node  = body.get_attr("source")
        dest_node    = body.get_attr("destination")
        state_node   = body.get_attr("state")
        ensure_node  = body.get_attr("ensure")
        iniface_node = body.get_attr("iniface")
        chain_node   = body.get_attr("chain")

        action   = str(self.resolve(action_node, context)) if action_node else "accept"
        proto    = str(self.resolve(proto_node, context)) if proto_node else "tcp"
        ensure_v = str(self.resolve(ensure_node, context)).lower() if ensure_node else "present"

        context.require_collection("ansible.posix")

        params: dict[str, Any] = {
            "state": "disabled" if ensure_v == "absent" else "enabled",
        }

        # Determine firewall type based on action/chain usage
        # Use firewalld as default (most common on RHEL/CentOS 7+)
        # TODO: detect ufw vs firewalld from context
        module = "ansible.posix.firewalld"

        if dport_node:
            dport_val = self.resolve(dport_node, context)
            if isinstance(dport_val, list):
                # Multiple ports — create one task per port
                tasks = []
                for port in dport_val:
                    p = str(port)
                    port_params = {**params, "port": f"{p}/{proto}", "permanent": True, "immediate": True}
                    tasks.append(self.make_task(
                        name=f"Firewall rule '{title}' port {p}/{proto}",
                        module=module,
                        params=port_params,
                        when=when,
                    ))
                return tasks
            else:
                params["port"] = f"{dport_val}/{proto}"
                params["permanent"] = True
                params["immediate"] = True

        if source_node:
            src = self.resolve(source_node, context)
            if isinstance(src, list):
                # Multiple sources — create one rule per source
                tasks = []
                for s in src:
                    s_params = {**params, "source": str(s), "permanent": True, "immediate": True}
                    tasks.append(self.make_task(
                        name=f"Firewall rule '{title}' from {s}",
                        module=module,
                        params=s_params,
                        when=when,
                    ))
                return tasks
            else:
                params["source"] = str(src)
                params["permanent"] = True
                params["immediate"] = True

        if not dport_node and not source_node:
            # No specific port/source — use rich_rule
            context.warn(
                f"firewall[{title}]: complex rule could not be auto-converted to firewalld. "
                f"Manual conversion needed."
            )
            params = {
                "msg": f"TODO: Manual firewall rule needed — Puppet rule '{title}'"
            }
            return [{"name": f"[TODO] Firewall rule: {title}", "ansible.builtin.debug": params}]

        return [self.make_task(
            name=f"Firewall rule: {title}",
            module=module,
            params=params,
            when=when,
        )]
