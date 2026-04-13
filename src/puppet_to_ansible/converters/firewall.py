"""firewall (puppetlabs/firewall) → ansible.posix.firewalld or community.general.ufw."""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ArrayLiteral, ResourceBody


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
        jump_node    = body.get_attr("jump")
        proto_node   = body.get_attr("proto")
        dport_node   = body.get_attr("dport")
        sport_node   = body.get_attr("sport")
        source_node  = body.get_attr("source")
        dest_node    = body.get_attr("destination")
        state_node   = body.get_attr("state")       # connection state (ESTABLISHED etc.)
        ensure_node  = body.get_attr("ensure")
        iniface_node = body.get_attr("iniface")
        chain_node   = body.get_attr("chain")
        icmp_node    = body.get_attr("icmp")
        limit_node   = body.get_attr("limit")

        action   = str(self.resolve(action_node, context)).lower() if action_node else "accept"
        jump     = str(self.resolve(jump_node, context)).upper() if jump_node else None
        proto    = str(self.resolve(proto_node, context)).lower() if proto_node else "tcp"
        ensure_v = str(self.resolve(ensure_node, context)).lower() if ensure_node else "present"
        chain    = str(self.resolve(chain_node, context)).upper() if chain_node else "INPUT"

        context.require_collection("ansible.posix")

        fw_state = "disabled" if ensure_v == "absent" else "enabled"
        module   = "ansible.posix.firewalld"

        # ── Loopback (iniface: lo) ───────────────────────────────────────────
        # firewalld trusts the loopback zone by default — no rule needed, but
        # we generate a comment task to document the intent.
        if iniface_node:
            iniface = str(self.resolve(iniface_node, context))
            if iniface == "lo":
                return [self.make_task(
                    name=f"Firewall rule: {title} (loopback — trusted by default in firewalld)",
                    module="ansible.builtin.debug",
                    params={"msg": (
                        "firewalld trusts the loopback interface by default via the 'lo' zone. "
                        "No explicit rule needed."
                    )},
                    when=when,
                )]
            # Non-loopback iniface — use rich_rule
            rich = f"rule family='ipv4' source not address='0.0.0.0/0' accept"
            context.warn(
                f"firewall[{title}]: iniface='{iniface}' has no direct firewalld mapping; "
                f"rich_rule generated — review before deploying."
            )
            return [self.make_task(
                name=f"Firewall rule: {title}",
                module=module,
                params={"rich_rule": rich, "state": fw_state, "permanent": True, "immediate": True},
                when=when,
            )]

        # ── Stateful (state: [ESTABLISHED, RELATED]) ────────────────────────
        # firewalld is stateful by default; this rule is implicit.
        if state_node and not dport_node and not source_node:
            states = self.resolve(state_node, context)
            state_list = states if isinstance(states, list) else [states]
            state_strs = [str(s).upper() for s in state_list]
            if set(state_strs) <= {"ESTABLISHED", "RELATED", "INVALID"}:
                return [self.make_task(
                    name=f"Firewall rule: {title} (stateful — implicit in firewalld)",
                    module="ansible.builtin.debug",
                    params={"msg": (
                        "firewalld is stateful by default; ESTABLISHED/RELATED traffic is "
                        "automatically allowed. No explicit rule needed."
                    )},
                    when=when,
                )]

        # ── ICMP ─────────────────────────────────────────────────────────────
        if proto == "icmp":
            icmp_type = str(self.resolve(icmp_node, context)) if icmp_node else "echo-request"
            # Map common Puppet icmp names → firewalld icmp-type names
            _icmp_map = {
                "echo-request": "echo-request",
                "echo-reply":   "echo-reply",
                "any":          None,  # allow all ICMP
            }
            fw_icmp = _icmp_map.get(icmp_type, icmp_type)
            if fw_icmp:
                return [self.make_task(
                    name=f"Firewall rule: {title}",
                    module=module,
                    params={
                        "icmp_block": fw_icmp,
                        "icmp_block_inversion": action == "accept",
                        "state": fw_state,
                        "permanent": True,
                        "immediate": True,
                    },
                    when=when,
                )]
            else:
                # Allow all ICMP — use service 'icmp' or rich_rule
                return [self.make_task(
                    name=f"Firewall rule: {title} (allow all ICMP)",
                    module=module,
                    params={"service": "icmp", "state": fw_state, "permanent": True, "immediate": True},
                    when=when,
                )]

        # ── LOG jump ─────────────────────────────────────────────────────────
        # firewalld has no direct LOG target; nftables/iptables logging is
        # out of scope for firewalld.  Generate an informative TODO.
        if jump == "LOG":
            log_prefix = ""
            log_level_node = body.get_attr("log_prefix")
            if log_level_node:
                log_prefix = str(self.resolve(log_level_node, context))
            return [{
                "name": f"[TODO] Firewall LOG rule: {title}",
                "ansible.builtin.debug": {
                    "msg": (
                        f"TODO: Puppet firewall rule '{title}' uses jump=LOG "
                        f"(prefix: '{log_prefix}'). firewalld does not support LOG targets. "
                        "Consider using 'nftables' with ansible.builtin.template, or "
                        "auditd/rsyslog for packet logging."
                    ),
                },
            }]

        # ── Default DROP / REJECT policy ─────────────────────────────────────
        # firewalld manages default policies per zone.  A blanket INPUT DROP
        # maps to setting the zone's default target to DROP.
        if action in ("drop", "reject") and not dport_node and not source_node:
            zone = "public"  # default zone — adjust if needed
            return [self.make_task(
                name=f"Firewall rule: {title} (set zone '{zone}' default target to {action.upper()})",
                module=module,
                params={
                    "zone": zone,
                    "target": action.upper(),
                    "state": fw_state,
                    "permanent": True,
                },
                when=when,
            )]

        # ── Port-based rules ─────────────────────────────────────────────────
        params: dict[str, Any] = {"state": fw_state}

        if dport_node:
            dport_val = self.resolve(dport_node, context)
            if isinstance(dport_val, list):
                tasks = []
                for port in dport_val:
                    p = str(port)
                    port_params = {**params, "port": f"{p}/{proto}", "permanent": True, "immediate": True}
                    if source_node:
                        port_params["source"] = str(self.resolve(source_node, context))
                    tasks.append(self.make_task(
                        name=f"Firewall rule: {title} port {p}/{proto}",
                        module=module,
                        params=port_params,
                        when=when,
                    ))
                return tasks
            else:
                params["port"] = f"{dport_val}/{proto}"
                params["permanent"] = True
                params["immediate"] = True

        # ── Source-based rules ────────────────────────────────────────────────
        if source_node:
            src = self.resolve(source_node, context)
            if isinstance(src, list):
                tasks = []
                for s in src:
                    s_params = {**params, "source": str(s), "permanent": True, "immediate": True}
                    tasks.append(self.make_task(
                        name=f"Firewall rule: {title} from {s}",
                        module=module,
                        params=s_params,
                        when=when,
                    ))
                return tasks
            else:
                params["source"] = str(src)
                params["permanent"] = True
                params["immediate"] = True

        # ── Fallback: rich_rule for anything else ────────────────────────────
        if not dport_node and not source_node:
            context.warn(
                f"firewall[{title}]: rule has no port/source and no recognised pattern; "
                f"rich_rule TODO generated — manual conversion needed."
            )
            return [{
                "name": f"[TODO] Firewall rule: {title}",
                "ansible.builtin.debug": {
                    "msg": (
                        f"TODO: Manual firewall rule needed — Puppet rule '{title}'. "
                        "Convert to ansible.posix.firewalld rich_rule or use "
                        "ansible.builtin.template with nftables/iptables."
                    ),
                },
            }]

        return [self.make_task(
            name=f"Firewall rule: {title}",
            module=module,
            params=params,
            when=when,
        )]
