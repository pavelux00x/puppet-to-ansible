"""ssh_authorized_key → ansible.posix.authorized_key."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class SshAuthorizedKeyConverter(BaseConverter):
    puppet_type = "ssh_authorized_key"

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
        user_node    = body.get_attr("user")
        key_node     = body.get_attr("key")
        type_node    = body.get_attr("type")
        options_node = body.get_attr("options")
        target_node  = body.get_attr("target")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        key_val  = str(self.resolve(key_node, context)) if key_node else ""
        key_type = str(self.resolve(type_node, context)) if type_node else "ssh-rsa"

        params: dict[str, Any] = {
            "user":  str(self.resolve(user_node, context)) if user_node else title,
            "key":   f"{key_type} {key_val}" if key_val and not key_val.startswith(key_type) else key_val,
            "state": state,
        }

        if options_node:
            opts = self.resolve(options_node, context)
            if isinstance(opts, list):
                params["key_options"] = ",".join(str(o) for o in opts)
            else:
                params["key_options"] = str(opts)

        if target_node:
            params["path"] = str(self.resolve(target_node, context))

        action = "Remove" if state == "absent" else "Add"
        return [self.make_task(
            name=f"{action} SSH authorized key for {params['user']}: {title}",
            module="ansible.posix.authorized_key",
            params=params,
            notify=notify or None,
            when=when,
        )]
