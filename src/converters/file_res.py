"""file → copy / template / file / lineinfile depending on parameters."""
from __future__ import annotations

import os
from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody, StringLiteral

_ENSURE_TO_STATE = {
    "present": None,      # handled separately
    "file":    None,      # copy or template
    "directory": "directory",
    "link":    "link",
    "absent":  "absent",
}


class FileConverter(BaseConverter):
    """Converts Puppet `file` resources to Ansible file/copy/template/lineinfile tasks."""

    puppet_type = "file"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        titles = self.resolve_title(body, context)
        # Puppet allows an array of paths as the resource title:
        #   file { [$dir1, $dir2]: ensure => directory }
        # Each path becomes its own Ansible task.
        if isinstance(titles, list):
            tasks = []
            for t in titles:
                tasks.extend(self._convert_single(str(t), body, context))
            return tasks
        return self._convert_single(str(titles), body, context)

    def _convert_single(
        self,
        title: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        content_node = body.get_attr("content")
        source_node  = body.get_attr("source")
        target_node  = body.get_attr("target")
        owner_node   = body.get_attr("owner")
        group_node   = body.get_attr("group")
        mode_node    = body.get_attr("mode")
        recurse_node = body.get_attr("recurse")

        ensure  = str(self.resolve(ensure_node, context)) if ensure_node else "file"
        content = self.resolve(content_node, context) if content_node else None
        source  = self.resolve(source_node, context) if source_node else None
        target  = self.resolve(target_node, context) if target_node else None
        owner   = str(self.resolve(owner_node, context)) if owner_node else None
        group   = str(self.resolve(group_node, context)) if group_node else None
        mode    = str(self.resolve(mode_node, context)) if mode_node else None

        # ansible.builtin.file uses 'path:', copy/template use 'dest:'
        meta: dict[str, Any] = {}
        if owner:
            meta["owner"] = owner
        if group:
            meta["group"] = group
        if mode:
            meta["mode"] = mode

        file_attrs: dict[str, Any] = {"path": title, **meta}   # for ansible.builtin.file
        xfer_attrs: dict[str, Any] = {"dest": title, **meta}   # for copy / template

        # Determine which module to use based on ensure + content/source
        if ensure in ("absent",):
            file_attrs["state"] = "absent"
            return [self.make_task(
                name=f"Remove {title}",
                module="ansible.builtin.file",
                params=file_attrs,
                notify=notify or None,
                when=when,
            )]

        if ensure == "directory":
            file_attrs["state"] = "directory"
            if recurse_node:
                recurse = self.resolve(recurse_node, context)
                if str(recurse).lower() in ("true", "remote"):
                    file_attrs["recurse"] = True
            return [self.make_task(
                name=f"Create directory {title}",
                module="ansible.builtin.file",
                params=file_attrs,
                notify=notify or None,
                when=when,
            )]

        if ensure == "link":
            file_attrs["state"] = "link"
            if target:
                file_attrs["src"] = target
            return [self.make_task(
                name=f"Create symlink {title}",
                module="ansible.builtin.file",
                params=file_attrs,
                notify=notify or None,
                when=when,
            )]

        # ensure == 'file' or 'present'
        if content is not None:
            content_str = str(content)
            # Template reference from template() function call
            if content_str.startswith("__template__"):
                template_name = content_str.replace("__template__", "")
                template_params = {**xfer_attrs, "src": template_name}
                return [self.make_task(
                    name=f"Template {title}",
                    module="ansible.builtin.template",
                    params=template_params,
                    notify=notify or None,
                    when=when,
                )]
            if content_str == "__inline_template__":
                context.warn(f"file[{title}]: inline_template() not auto-converted — manual review needed")
                return [self.make_task(
                    name=f"[TODO] Template {title} (inline_template)",
                    module="ansible.builtin.template",
                    params={**xfer_attrs, "src": f"{os.path.basename(title)}.j2"},
                    notify=notify or None,
                    when=when,
                )]
            # Literal content → copy with content:
            copy_params = {**xfer_attrs, "content": content_str}
            return [self.make_task(
                name=f"Write {title}",
                module="ansible.builtin.copy",
                params=copy_params,
                notify=notify or None,
                when=when,
            )]

        if source is not None:
            source_str = str(source)
            # puppet:///modules/mod/file → files/file
            src = _puppet_source_to_ansible(source_str)
            copy_params = {**xfer_attrs, "src": src}
            return [self.make_task(
                name=f"Copy {title}",
                module="ansible.builtin.copy",
                params=copy_params,
                notify=notify or None,
                when=when,
            )]

        # No content/source — just manage ownership/permissions
        file_attrs["state"] = "file"
        return [self.make_task(
            name=f"Manage file {title}",
            module="ansible.builtin.file",
            params=file_attrs,
            notify=notify or None,
            when=when,
        )]


def _puppet_source_to_ansible(source: str) -> str:
    """Convert puppet:///modules/mod/path → files/path."""
    if source.startswith("puppet:///modules/"):
        # puppet:///modules/nginx/conf/nginx.conf → nginx/conf/nginx.conf (role-relative)
        rest = source.replace("puppet:///modules/", "")
        parts = rest.split("/", 1)
        return parts[1] if len(parts) > 1 else rest
    return source
