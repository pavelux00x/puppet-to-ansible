"""apt::source → ansible.builtin.apt_repository
   apt::key    → ansible.builtin.apt_key (deprecated) or get_url + apt_repository.
"""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody


class AptSourceConverter(BaseConverter):
    puppet_type = "apt::source"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node   = body.get_attr("ensure")
        location_node = body.get_attr("location")
        release_node  = body.get_attr("release")
        repos_node    = body.get_attr("repos")
        key_node      = body.get_attr("key")
        comment_node  = body.get_attr("comment")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        params: dict[str, Any] = {"state": state}

        if location_node:
            location = str(self.resolve(location_node, context))
            release = str(self.resolve(release_node, context)) if release_node else "{{ ansible_distribution_release }}"
            repos = str(self.resolve(repos_node, context)) if repos_node else "main"
            params["repo"] = f"deb {location} {release} {repos}"
        if comment_node:
            params["filename"] = title

        tasks = []

        # Handle GPG key
        if key_node:
            key_val = self.resolve(key_node, context)
            if isinstance(key_val, dict):
                key_id  = key_val.get("id", "")
                key_src = key_val.get("source", "")
                if key_src:
                    tasks.append(self.make_task(
                        name=f"Add APT GPG key for {title}",
                        module="ansible.builtin.apt_key",
                        params={"url": str(key_src), "state": state},
                        when=when,
                    ))
                elif key_id:
                    tasks.append(self.make_task(
                        name=f"Add APT GPG key for {title}",
                        module="ansible.builtin.apt_key",
                        params={"id": str(key_id), "state": state},
                        when=when,
                    ))

        tasks.append(self.make_task(
            name=f"{'Remove' if state == 'absent' else 'Add'} APT repository: {title}",
            module="ansible.builtin.apt_repository",
            params=params,
            notify=notify or None,
            when=when,
        ))
        return tasks


class AptKeyConverter(BaseConverter):
    puppet_type = "apt::key"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        when   = self.get_when(body, context)

        ensure_node = body.get_attr("ensure")
        source_node = body.get_attr("source")
        id_node     = body.get_attr("id") or body.get_attr("key")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        params: dict[str, Any] = {"state": state}
        if source_node:
            params["url"] = str(self.resolve(source_node, context))
        if id_node:
            params["id"] = str(self.resolve(id_node, context))

        return [self.make_task(
            name=f"{'Remove' if state == 'absent' else 'Add'} APT key: {title}",
            module="ansible.builtin.apt_key",
            params=params,
            when=when,
        )]
