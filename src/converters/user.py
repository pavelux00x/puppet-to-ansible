"""user → ansible.builtin.user."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ArrayLiteral, ResourceBody

_ENSURE_MAP = {
    "present": "present",
    "absent":  "absent",
}


class UserConverter(BaseConverter):
    puppet_type = "user"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node     = body.get_attr("ensure")
        uid_node        = body.get_attr("uid")
        gid_node        = body.get_attr("gid")
        groups_node     = body.get_attr("groups")
        home_node       = body.get_attr("home")
        shell_node      = body.get_attr("shell")
        comment_node    = body.get_attr("comment")
        managehome_node = body.get_attr("managehome")
        password_node   = body.get_attr("password")
        system_node     = body.get_attr("system")
        expiry_node     = body.get_attr("expiry")

        ensure = _ENSURE_MAP.get(
            str(self.resolve(ensure_node, context)).lower() if ensure_node else "present",
            "present",
        )

        params: dict[str, Any] = {"name": title, "state": ensure}

        if uid_node:
            params["uid"] = self.resolve(uid_node, context)
        if gid_node:
            gid_val = self.resolve(gid_node, context)
            params["group"] = str(gid_val)
        if groups_node:
            groups_val = self.resolve(groups_node, context)
            if isinstance(groups_val, list):
                params["groups"] = groups_val
            else:
                params["groups"] = [str(groups_val)]
        if home_node:
            params["home"] = str(self.resolve(home_node, context))
        if shell_node:
            params["shell"] = str(self.resolve(shell_node, context))
        if comment_node:
            params["comment"] = str(self.resolve(comment_node, context))
        if managehome_node:
            mh = self.resolve(managehome_node, context)
            params["create_home"] = bool(mh) if isinstance(mh, bool) else str(mh).lower() == "true"
        if password_node:
            params["password"] = str(self.resolve(password_node, context))
        if system_node:
            sv = self.resolve(system_node, context)
            params["system"] = bool(sv) if isinstance(sv, bool) else str(sv).lower() == "true"
        if expiry_node:
            params["expires"] = self.resolve(expiry_node, context)

        action = "Remove" if ensure == "absent" else "Manage"
        return [self.make_task(
            name=f"{action} user {title}",
            module="ansible.builtin.user",
            params=params,
            notify=notify or None,
            when=when,
        )]
