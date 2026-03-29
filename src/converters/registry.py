"""Converter registry — maps Puppet resource types to converter instances."""
from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from src.converters.base import BaseConverter, ConversionContext
from src.parser.ast_nodes import ResourceBody

logger = logging.getLogger(__name__)


class ConverterRegistry:
    """Registry that maps Puppet resource type names to converter instances."""

    def __init__(self) -> None:
        self._converters: dict[str, BaseConverter] = {}

    def register(self, converter: BaseConverter) -> None:
        key = converter.puppet_type.lower()
        self._converters[key] = converter
        logger.debug("Registered converter for '%s'", key)

    def get(self, puppet_type: str) -> BaseConverter | None:
        return self._converters.get(puppet_type.lower())

    def has(self, puppet_type: str) -> bool:
        return puppet_type.lower() in self._converters

    def list_supported(self) -> list[str]:
        return sorted(self._converters.keys())

    def convert_resource(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        """Route a resource to its converter and return Ansible tasks."""
        converter = self.get(resource_type)
        title_val = str(getattr(body.title, "value", body.title))

        if converter is None:
            context.add_unconverted(resource_type, title_val)
            logger.warning("No converter for '%s' (title: %s)", resource_type, title_val)
            return [_todo_task(resource_type, body)]

        try:
            return converter.convert(resource_type, body, context)
        except Exception as exc:
            logger.exception("Converter for '%s' failed for title '%s'", resource_type, title_val)
            context.warn(f"Converter error for {resource_type}[{title_val}]: {exc}")
            context.add_unconverted(resource_type, title_val, reason=str(exc))
            return [_todo_task(resource_type, body, reason=str(exc))]

    def auto_discover(self) -> None:
        """Import all modules in src.converters and register BaseConverter subclasses."""
        import src.converters as pkg
        for _finder, module_name, _ispkg in pkgutil.iter_modules(pkg.__path__):
            if module_name in ("base", "registry"):
                continue
            try:
                mod = importlib.import_module(f"src.converters.{module_name}")
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseConverter)
                        and attr is not BaseConverter
                        and attr.puppet_type
                    ):
                        self.register(attr())
            except Exception as exc:
                logger.warning("Failed to import converter module '%s': %s", module_name, exc)

    @property
    def registered_types(self) -> list[str]:
        return sorted(self._converters.keys())


def _todo_task(resource_type: str, body: ResourceBody, reason: str = "") -> dict[str, Any]:
    title = str(getattr(body.title, "value", body.title))
    params_repr = ", ".join(f"{a.name} => ..." for a in body.attributes[:5])
    msg = f"TODO: Manual conversion needed — Puppet resource '{resource_type}' title '{title}'"
    if reason:
        msg += f" | Error: {reason}"
    return {
        "name": f"[TODO] {resource_type}: {title}",
        "ansible.builtin.debug": {"msg": msg},
        "__puppet_original__": f"{resource_type} {{ '{title}': {params_repr} }}",
    }


_registry: ConverterRegistry | None = None


def get_registry() -> ConverterRegistry:
    global _registry
    if _registry is None:
        _registry = ConverterRegistry()
        _registry.auto_discover()
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
