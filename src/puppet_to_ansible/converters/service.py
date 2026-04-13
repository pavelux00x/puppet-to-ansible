"""service → ansible.builtin.service / ansible.builtin.systemd."""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody

_ENSURE_MAP = {
    "running": "started",
    "stopped": "stopped",
    "true":    "started",
    "false":   "stopped",
}


class ServiceConverter(BaseConverter):
    """Converts Puppet `service` resources to Ansible service tasks."""

    puppet_type = "service"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        enable_node  = body.get_attr("enable")
        provider_node = body.get_attr("provider")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else None
        enable_raw = self.resolve(enable_node, context) if enable_node else None
        provider   = str(self.resolve(provider_node, context)) if provider_node else None

        # Choose module
        module = "ansible.builtin.systemd" if provider == "systemd" else "ansible.builtin.service"

        params: dict[str, Any] = {"name": title}

        if ensure_raw is not None:
            state = _ENSURE_MAP.get(str(ensure_raw).lower(), str(ensure_raw))
            params["state"] = state

        if enable_raw is not None:
            if isinstance(enable_raw, bool):
                params["enabled"] = enable_raw
            else:
                params["enabled"] = str(enable_raw).lower() not in ("false", "no", "0")

        # systemd extras
        if module == "ansible.builtin.systemd":
            daemon_reload = body.get_attr("daemon_reload")
            if daemon_reload:
                params["daemon_reload"] = True

        # Auto-register handler for notify/subscribe patterns
        # (the handler is the "Restart <service>" handler)
        handler_name = f"Restart {title}"
        handler_params: dict[str, Any] = {"name": title, "state": "restarted"}
        context.add_handler(handler_name, module, handler_params)

        action = "Manage" if ensure_raw is None else ("Start" if params.get("state") == "started" else "Stop")
        task_name = f"{action} {title} service"

        return [self.make_task(
            name=task_name,
            module=module,
            params=params,
            notify=notify or None,
            when=when,
        )]
