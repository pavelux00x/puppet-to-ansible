"""exec → ansible.builtin.command / ansible.builtin.shell."""
from __future__ import annotations

import re
from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody

_SHELL_CHARS = re.compile(r'[|><;]|&&|\|\|')


class ExecConverter(BaseConverter):
    """Converts Puppet `exec` resources to Ansible command/shell tasks.

    Key behaviours:
    - exec with refreshonly => true → Ansible handler
    - exec with unless/onlyif/creates → command with creates: or register+when
    - exec with pipe/redirect/shell operators → shell module
    - exec with user => → become_user
    """

    puppet_type = "exec"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title = str(self.resolve_title(body, context))

        command_node     = body.get_attr("command")
        unless_node      = body.get_attr("unless")
        onlyif_node      = body.get_attr("onlyif")
        creates_node     = body.get_attr("creates")
        refreshonly_node = body.get_attr("refreshonly")
        cwd_node         = body.get_attr("cwd")
        user_node        = body.get_attr("user")
        env_node         = body.get_attr("environment")
        timeout_node     = body.get_attr("timeout")
        returns_node     = body.get_attr("returns")

        # The command to run (may be title if no command param)
        command_raw = self.resolve(command_node, context) if command_node else title
        command = str(command_raw)

        refreshonly = False
        if refreshonly_node:
            rv = self.resolve(refreshonly_node, context)
            refreshonly = bool(rv) if isinstance(rv, bool) else str(rv).lower() == "true"

        # Choose module: shell if command contains special characters
        module = "ansible.builtin.shell" if _SHELL_CHARS.search(command) else "ansible.builtin.command"

        params: dict[str, Any] = {"cmd": command}

        if cwd_node:
            params["chdir"] = str(self.resolve(cwd_node, context))

        if creates_node:
            creates = str(self.resolve(creates_node, context))
            params["creates"] = creates

        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        # become_user from user param
        become      = False
        become_user = None
        if user_node:
            become_user = str(self.resolve(user_node, context))
            become = True

        # environment
        if env_node:
            env_val = self.resolve(env_node, context)
            if isinstance(env_val, list):
                env_dict = {}
                for item in env_val:
                    if "=" in str(item):
                        k, v = str(item).split("=", 1)
                        env_dict[k] = v
                params["environment"] = env_dict
            elif isinstance(env_val, dict):
                params["environment"] = env_val

        # timeout
        if timeout_node:
            params["timeout"] = int(self.resolve(timeout_node, context))

        # Return codes
        if returns_node:
            rv = self.resolve(returns_node, context)
            if isinstance(rv, list):
                valid_codes = [int(c) for c in rv]
            else:
                valid_codes = [int(rv)]
            # We'll add failed_when below

        tasks = []

        if refreshonly:
            # Register as a handler instead of a regular task
            handler_name = _exec_to_handler_name(title, command)
            context.add_handler(handler_name, module, params)
            # Return nothing — the handler is registered, no task needed
            context.warn(
                f"exec[{title}]: refreshonly=true converted to handler '{handler_name}'. "
                f"Make sure a task notifies this handler."
            )
            return []

        # unless → register + when (idempotency guard)
        if unless_node:
            unless_cmd = str(self.resolve(unless_node, context))
            register_var = _safe_var_name(title) + "_check"
            check_task = self.make_task(
                name=f"Check if {title} should run",
                module="ansible.builtin.command",
                params={"cmd": unless_cmd},
                register=register_var,
            )
            check_task["failed_when"] = False
            check_task["changed_when"] = False
            tasks.append(check_task)
            # Main task runs only when check failed (rc != 0)
            unless_when = f"{register_var}.rc != 0"
            if when:
                when = f"({when}) and ({unless_when})"
            else:
                when = unless_when

        if onlyif_node:
            onlyif_cmd = str(self.resolve(onlyif_node, context))
            register_var = _safe_var_name(title) + "_check"
            check_task = self.make_task(
                name=f"Check condition for {title}",
                module="ansible.builtin.command",
                params={"cmd": onlyif_cmd},
                register=register_var,
            )
            check_task["failed_when"] = False
            check_task["changed_when"] = False
            tasks.append(check_task)
            onlyif_when = f"{register_var}.rc == 0"
            if when:
                when = f"({when}) and ({onlyif_when})"
            else:
                when = onlyif_when

        main_task = self.make_task(
            name=f"Run {title}",
            module=module,
            params=params,
            notify=notify or None,
            when=when,
            become=become,
            become_user=become_user,
        )

        # Handle returns (non-zero accepted codes)
        if returns_node:
            rv = self.resolve(returns_node, context)
            codes = [int(c) for c in rv] if isinstance(rv, list) else [int(rv)]
            if codes != [0]:
                main_task["failed_when"] = f"result.rc not in {codes}"
                main_task["register"] = "result"

        tasks.append(main_task)
        return tasks


def _exec_to_handler_name(title: str, command: str) -> str:
    """Generate a handler name for a refreshonly exec."""
    return f"Run {title}"


def _safe_var_name(s: str) -> str:
    """Convert an arbitrary string into a valid Ansible variable name."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", s).strip("_").lower()
