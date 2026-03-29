"""yumrepo → ansible.builtin.yum_repository."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class YumrepoConverter(BaseConverter):
    puppet_type = "yumrepo"

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
        ensure_raw  = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() in ("absent", "purged") else "present"

        params: dict[str, Any] = {"name": title, "state": state}

        _map = {
            "descr":       "description",
            "baseurl":     "baseurl",
            "mirrorlist":  "mirrorlist",
            "enabled":     "enabled",
            "gpgcheck":    "gpgcheck",
            "gpgkey":      "gpgkey",
            "priority":    "priority",
            "exclude":     "exclude",
            "includepkg":  "includepkgs",
            "proxy":       "proxy",
            "metadata_expire": "metadata_expire",
            "skip_if_unavailable": "skip_if_unavailable",
        }

        for puppet_attr, ansible_attr in _map.items():
            node = body.get_attr(puppet_attr)
            if node is not None:
                val = self.resolve(node, context)
                if isinstance(val, bool):
                    params[ansible_attr] = val
                elif str(val).lower() in ("true", "false"):
                    params[ansible_attr] = str(val).lower() == "true"
                else:
                    params[ansible_attr] = val

        action = "Remove" if state == "absent" else "Configure"
        return [self.make_task(
            name=f"{action} YUM repository: {title}",
            module="ansible.builtin.yum_repository",
            params=params,
            notify=notify or None,
            when=when,
        )]
