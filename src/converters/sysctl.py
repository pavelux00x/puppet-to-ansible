"""sysctl → ansible.posix.sysctl."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class SysctlConverter(BaseConverter):
    """Converts Puppet `sysctl` resources (herculesteam/augeasproviders_sysctl)
    to Ansible ansible.posix.sysctl tasks.

    Mapping:
        sysctl { 'net.ipv4.ip_forward': val => '1', persist => true }
        → ansible.posix.sysctl: name: net.ipv4.ip_forward, value: '1', sysctl_set: true, state: present
    """

    puppet_type = "sysctl"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        context.require_collection("ansible.posix")

        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        val_node     = body.get_attr("val") or body.get_attr("value")
        persist_node = body.get_attr("persist")
        comment_node = body.get_attr("comment")

        ensure_raw  = self.resolve(ensure_node, context) if ensure_node else "present"
        state       = "present" if str(ensure_raw).lower() != "absent" else "absent"

        params: dict[str, Any] = {"name": title, "state": state}

        if val_node is not None:
            params["value"] = str(self.resolve(val_node, context))

        # persist => true means write to /etc/sysctl.d — maps to sysctl_set
        if persist_node is not None:
            persist_val = self.resolve(persist_node, context)
            if isinstance(persist_val, bool):
                params["sysctl_set"] = persist_val
            else:
                params["sysctl_set"] = str(persist_val).lower() not in ("false", "no", "0")
        else:
            # Puppet default is persist => true → always persist
            params["sysctl_set"] = True

        # reload: apply immediately (Puppet default is also true)
        reload_node = body.get_attr("apply")
        if reload_node is not None:
            reload_val = self.resolve(reload_node, context)
            params["reload"] = bool(reload_val) if isinstance(reload_val, bool) else str(reload_val).lower() not in ("false", "no", "0")
        else:
            params["reload"] = True

        if comment_node is not None:
            # ansible.posix.sysctl has no comment param; skip silently
            pass

        return [self.make_task(
            name=f"Set sysctl {title}",
            module="ansible.posix.sysctl",
            params=params,
            notify=notify or None,
            when=when,
        )]
