"""package → ansible.builtin.package / apt / yum / pip."""
from __future__ import annotations

from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody, StringLiteral


# Puppet ensure → Ansible state
_ENSURE_MAP = {
    "present":   "present",
    "installed": "present",
    "latest":    "latest",
    "absent":    "absent",
    "purged":    "absent",
}

# Provider → module override
_PROVIDER_MODULE = {
    "apt":     "ansible.builtin.apt",
    "yum":     "ansible.builtin.yum",
    "dnf":     "ansible.builtin.dnf",
    "pip":     "ansible.builtin.pip",
    "pip3":    "ansible.builtin.pip",
    "gem":     "community.general.gem",
    "homebrew": "community.general.homebrew",
    "chocolatey": "chocolatey.chocolatey.win_chocolatey",
}


class PackageConverter(BaseConverter):
    """Converts Puppet `package` resources to Ansible package tasks."""

    puppet_type = "package"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        title = self.resolve_title(body, context)
        titles = [title] if isinstance(title, str) else title

        ensure_node = body.get_attr("ensure")
        ensure_raw  = self.resolve(ensure_node, context) if ensure_node else "present"
        ensure_str  = str(ensure_raw)

        provider_node = body.get_attr("provider")
        provider = str(self.resolve(provider_node, context)) if provider_node else None

        # Determine module
        module = _PROVIDER_MODULE.get(provider or "", "ansible.builtin.package")
        if module != "ansible.builtin.package":
            context.require_collection(_collection_for_module(module))

        # Map ensure to state / version
        state, version = self._map_ensure(ensure_str)

        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        tasks = []
        for pkg_name in titles if isinstance(titles, list) else [titles]:
            pkg_name_str = str(pkg_name)
            # If version is pinned, append to name for apt/yum style
            if version and module in ("ansible.builtin.apt", "ansible.builtin.yum", "ansible.builtin.dnf"):
                pkg_name_str = f"{pkg_name_str}={version}" if "apt" in module else f"{pkg_name_str}-{version}"
            elif version and module == "ansible.builtin.package":
                pkg_name_str = f"{pkg_name_str}={version}"

            action_word = "Remove" if state == "absent" else "Install"
            task_name = f"{action_word} {pkg_name}"

            params: dict[str, Any] = {"name": pkg_name_str, "state": state}

            # Provider-specific extras
            if module == "ansible.builtin.pip":
                params = self._pip_params(body, context, pkg_name_str, state)
            elif module == "ansible.builtin.apt":
                update_cache = body.get_attr("update_cache")
                if update_cache is None:
                    params["update_cache"] = True  # idiomatic for apt

            tasks.append(self.make_task(
                name=task_name,
                module=module,
                params=params,
                notify=notify or None,
                when=when,
            ))

        return tasks

    def _map_ensure(self, ensure: str) -> tuple[str, str | None]:
        """Return (state, version_pin_or_None)."""
        mapped = _ENSURE_MAP.get(ensure.lower())
        if mapped:
            return mapped, None
        # Version string like '2.4.6-1.el7'
        return "present", ensure

    def _pip_params(
        self,
        body: ResourceBody,
        context: ConversionContext,
        name: str,
        state: str,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "state": state}
        venv = body.get_attr("install_options")
        if venv:
            params["virtualenv"] = self.resolve(venv, context)
        return params


def _collection_for_module(module: str) -> str:
    if module.startswith("community.general"):
        return "community.general"
    if module.startswith("chocolatey"):
        return "chocolatey.chocolatey"
    return ""
