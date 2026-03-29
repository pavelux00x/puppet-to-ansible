"""cron → ansible.builtin.cron."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ArrayLiteral, ResourceBody


class CronConverter(BaseConverter):
    puppet_type = "cron"

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
        command_node = body.get_attr("command")
        user_node    = body.get_attr("user")
        hour_node    = body.get_attr("hour")
        minute_node  = body.get_attr("minute")
        month_node   = body.get_attr("month")
        monthday_node = body.get_attr("monthday")
        weekday_node = body.get_attr("weekday")
        special_node = body.get_attr("special")  # reboot, hourly, etc.
        env_node     = body.get_attr("environment")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        ensure = "absent" if str(ensure_raw).lower() == "absent" else "present"

        params: dict[str, Any] = {"name": title, "state": ensure}

        if command_node:
            params["job"] = str(self.resolve(command_node, context))
        if user_node:
            params["user"] = str(self.resolve(user_node, context))

        # Time fields — Ansible uses '*' as wildcard (same as cron)
        for puppet_field, ansible_field in [
            (hour_node, "hour"),
            (minute_node, "minute"),
            (month_node, "month"),
            (monthday_node, "day"),
            (weekday_node, "weekday"),
        ]:
            if puppet_field is not None:
                val = self.resolve(puppet_field, context)
                if isinstance(val, list):
                    params[ansible_field] = ",".join(str(v) for v in val)
                else:
                    params[ansible_field] = str(val)

        if special_node:
            special = str(self.resolve(special_node, context)).lower()
            # Map Puppet special values to Ansible special_time
            _special_map = {
                "reboot":   "reboot",
                "yearly":   "yearly",
                "annually": "annually",
                "monthly":  "monthly",
                "weekly":   "weekly",
                "daily":    "daily",
                "midnight": "daily",
                "hourly":   "hourly",
            }
            ansible_special = _special_map.get(special, special)
            params["special_time"] = ansible_special
            # Remove individual time fields if special_time is set
            for f in ("hour", "minute", "month", "day", "weekday"):
                params.pop(f, None)

        action = "Remove" if ensure == "absent" else "Manage"
        return [self.make_task(
            name=f"{action} cron job: {title}",
            module="ansible.builtin.cron",
            params=params,
            notify=notify or None,
            when=when,
        )]
