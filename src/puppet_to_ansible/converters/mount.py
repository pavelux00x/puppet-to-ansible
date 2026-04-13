"""mount → ansible.posix.mount."""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody

_ENSURE_MAP = {
    "defined":   "present",
    "present":   "present",
    "mounted":   "mounted",
    "unmounted": "unmounted",
    "absent":    "absent",
    "ghost":     "absent",
}


class MountConverter(BaseConverter):
    puppet_type = "mount"

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
        device_node  = body.get_attr("device")
        fstype_node  = body.get_attr("fstype")
        options_node = body.get_attr("options")
        dump_node    = body.get_attr("dump")
        pass_node    = body.get_attr("pass")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "mounted"
        state = _ENSURE_MAP.get(str(ensure_raw).lower(), "mounted")

        params: dict[str, Any] = {"path": title, "state": state}

        if device_node:
            params["src"] = str(self.resolve(device_node, context))
        if fstype_node:
            params["fstype"] = str(self.resolve(fstype_node, context))
        if options_node:
            opts = self.resolve(options_node, context)
            if isinstance(opts, list):
                params["opts"] = ",".join(str(o) for o in opts)
            else:
                params["opts"] = str(opts)
        if dump_node:
            params["dump"] = str(self.resolve(dump_node, context))
        if pass_node:
            params["passno"] = str(self.resolve(pass_node, context))

        return [self.make_task(
            name=f"Mount {title}",
            module="ansible.posix.mount",
            params=params,
            notify=notify or None,
            when=when,
        )]
