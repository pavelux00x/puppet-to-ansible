"""notify → ansible.builtin.debug.

Puppet's ``notify`` resource type logs a message during the agent run.
The Ansible equivalent is ``ansible.builtin.debug``.
"""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody


class NotifyConverter(BaseConverter):
    """Converts Puppet ``notify`` resources to ``ansible.builtin.debug`` tasks."""

    puppet_type = "notify"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title = self.resolve_title(body, context)
        when  = self.get_when(body, context)

        message_node = body.get_attr("message")
        msg = str(self.resolve(message_node, context)) if message_node else str(title)

        task: dict[str, Any] = {
            "name": f"Notify: {msg[:60]}",
            "ansible.builtin.debug": {"msg": msg},
        }
        if when:
            task["when"] = when
        return [task]
