"""ini_setting → community.general.ini_file
   file_line   → ansible.builtin.lineinfile
"""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody


class IniSettingConverter(BaseConverter):
    puppet_type = "ini_setting"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        context.require_collection("community.general")
        title = str(self.resolve_title(body, context))
        when  = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        path_node    = body.get_attr("path")
        section_node = body.get_attr("section")
        setting_node = body.get_attr("setting")
        value_node   = body.get_attr("value")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        params: dict[str, Any] = {"state": state}

        if path_node:
            params["path"] = str(self.resolve(path_node, context))
        if section_node:
            params["section"] = str(self.resolve(section_node, context))
        if setting_node:
            params["option"] = str(self.resolve(setting_node, context))
        if value_node and state == "present":
            params["value"] = self.resolve(value_node, context)

        return [self.make_task(
            name=f"INI setting {title}",
            module="community.general.ini_file",
            params=params,
            when=when,
        )]


class FileLineConverter(BaseConverter):
    puppet_type = "file_line"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title  = str(self.resolve_title(body, context))
        when   = self.get_when(body, context)
        notify = self.get_notify(body, context)

        ensure_node  = body.get_attr("ensure")
        path_node    = body.get_attr("path")
        line_node    = body.get_attr("line")
        match_node   = body.get_attr("match")
        after_node   = body.get_attr("after")
        replace_node = body.get_attr("replace_all_matches_not_matching_line")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state = "absent" if str(ensure_raw).lower() == "absent" else "present"

        params: dict[str, Any] = {"state": state}

        if path_node:
            params["path"] = str(self.resolve(path_node, context))
        if line_node:
            params["line"] = str(self.resolve(line_node, context))
        if match_node:
            params["regexp"] = str(self.resolve(match_node, context))
        if after_node:
            params["insertafter"] = str(self.resolve(after_node, context))

        return [self.make_task(
            name=f"Manage line in file: {title}",
            module="ansible.builtin.lineinfile",
            params=params,
            notify=notify or None,
            when=when,
        )]
