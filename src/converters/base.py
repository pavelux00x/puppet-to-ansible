"""Base converter class — all resource converters inherit from this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from src.parser.ast_nodes import (
    ArrayLiteral,
    BoolLiteral,
    FunctionCall,
    HashLiteral,
    NumberLiteral,
    PuppetNode,
    ResourceBody,
    ResourceReference,
    SelectorExpression,
    StringInterpolation,
    StringLiteral,
    TypeCast,
    UndefLiteral,
    Variable,
    VariableAssignment,
)
from src.utils.facts_mapper import map_fact


class BaseConverter(ABC):
    """Abstract base class for Puppet-to-Ansible resource converters.

    Every converter must:
    1. Set `puppet_type` to the Puppet resource type it handles (lowercase, e.g. 'package')
    2. Implement `convert()` to transform a parsed ResourceBody into Ansible task(s)
    """

    puppet_type: str = ""  # Must be overridden by subclass

    @abstractmethod
    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        """Convert a Puppet resource body to a list of Ansible tasks.

        Args:
            resource_type:  The Puppet resource type (e.g. 'package', 'file').
            body:           The parsed ResourceBody AST node.
            context:        Conversion context (variable scope, puppet version, etc.).

        Returns:
            List of Ansible task dicts. Each must have at minimum:
              - 'name': Descriptive task name (str)
              - '<module_fqcn>': Module parameters (dict)
        """
        ...

    # ── Value resolution helpers ─────────────────────────────────────────────

    def resolve(self, node: PuppetNode, context: ConversionContext) -> Any:
        """Recursively resolve a PuppetNode to a plain Python value.

        Variables are resolved from the context scope.
        Unresolvable variables are rendered as Jinja2 placeholders.
        """
        if isinstance(node, StringLiteral):
            return node.value
        if isinstance(node, StringInterpolation):
            return self._resolve_interpolation(node, context)
        if isinstance(node, NumberLiteral):
            return node.value
        if isinstance(node, BoolLiteral):
            return node.value
        if isinstance(node, UndefLiteral):
            return None
        if isinstance(node, Variable):
            return self._resolve_variable(node, context)
        if isinstance(node, ArrayLiteral):
            return [self.resolve(e, context) for e in node.elements]
        if isinstance(node, HashLiteral):
            return {self.resolve(k, context): self.resolve(v, context) for k, v in node.pairs}
        if isinstance(node, ResourceReference):
            return f"{node.type_name}[{self.resolve(node.titles[0], context) if node.titles else '?'}]"
        if isinstance(node, FunctionCall):
            return self._resolve_function(node, context)
        if isinstance(node, SelectorExpression):
            return self._resolve_selector(node, context)
        if isinstance(node, TypeCast):
            return self.resolve(node.value, context)
        # Fallback
        return str(node)

    def _resolve_variable(self, var: Variable, context: ConversionContext) -> Any:
        bare = var.bare_name
        # Check local scope first
        if bare in context.variables:
            return context.variables[bare]
        # Check if it's a known Puppet fact → Ansible fact
        fact = map_fact(f"${var.name}")
        if not fact.startswith("UNMAPPED"):
            return f"{{{{ {fact} }}}}"
        # Render as Jinja2 variable
        clean = bare.replace("::", "_")
        return f"{{{{ {clean} }}}}"

    def _resolve_interpolation(self, node: StringInterpolation, context: ConversionContext) -> str:
        parts = []
        for part in node.parts:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, Variable):
                resolved = self._resolve_variable(part, context)
                # If already a jinja2 expression, embed directly
                if isinstance(resolved, str) and resolved.startswith("{{"):
                    parts.append(resolved)
                else:
                    parts.append(str(resolved))
            else:
                val = self.resolve(part, context)
                parts.append(str(val))
        result = "".join(parts)
        # Wrap in quotes if the result contains jinja2 expressions
        return result

    def _resolve_function(self, fn: FunctionCall, context: ConversionContext) -> Any:
        name = fn.name
        if name in ("hiera", "lookup", "hiera_include"):
            if fn.arguments:
                key = self.resolve(fn.arguments[0], context)
                key_str = str(key)

                # --- Hiera live resolution (when a HieraAwareScope is available) ---
                if getattr(context, "hiera_scope", None) is not None:
                    # Determine merge strategy from lookup()'s 3rd arg
                    merge = "first"
                    if name == "lookup" and len(fn.arguments) >= 3:
                        merge_arg = self.resolve(fn.arguments[2], context)
                        if isinstance(merge_arg, str):
                            merge = merge_arg

                    # Determine default from hiera(key, default) or lookup(key, _, _, default)
                    hiera_default = None
                    if name == "hiera" and len(fn.arguments) >= 2:
                        hiera_default = self.resolve(fn.arguments[1], context)
                    elif name == "lookup" and len(fn.arguments) >= 4:
                        hiera_default = self.resolve(fn.arguments[3], context)

                    resolved = context.hiera_scope.get(key_str, default=hiera_default, merge=merge)
                    if resolved is not None:
                        return resolved

                # --- Fallback: emit as Jinja2 variable reference ---
                var_name = key_str.replace("::", "_").replace("-", "_").replace(".", "_")
                return f"{{{{ {var_name} }}}}"
            return "{{ undefined }}"
        if name == "template":
            # template('module/file.erb') → reference to .j2 equivalent
            if fn.arguments:
                path = self.resolve(fn.arguments[0], context)
                # Convert 'module/file.erb' → 'file.j2'
                base = str(path).split("/")[-1]
                j2name = base.replace(".erb", ".j2").replace(".epp", ".j2")
                return f"__template__{j2name}"
            return "__template__unknown.j2"
        if name == "inline_template":
            return "__inline_template__"
        if name in ("hiera_array", "hiera_hash"):
            if fn.arguments:
                key = self.resolve(fn.arguments[0], context)
                var_name = str(key).replace("::", "_")
                return f"{{{{ {var_name} }}}}"
            return "[]"
        if name == "fail":
            return None  # fail() calls are ignored in conversion (generate warning)
        # Generic function → Jinja2 filter-style representation
        args_repr = ", ".join(str(self.resolve(a, context)) for a in fn.arguments)
        return f"{{{{ {name}({args_repr}) }}}}"

    def _resolve_selector(self, sel: SelectorExpression, context: ConversionContext) -> Any:
        control_val = self.resolve(sel.control, context)
        for match, result in sel.cases:
            if isinstance(match, UndefLiteral):
                return self.resolve(result, context)  # default
        # Can't statically resolve — return as comment
        return f"# selector on {control_val}"

    def resolve_title(self, body: ResourceBody, context: ConversionContext) -> str | list[str]:
        """Resolve the resource title to a string or list of strings."""
        val = self.resolve(body.title, context)
        if isinstance(val, list):
            return [str(v) for v in val]
        return str(val)

    # ── Notify / require helpers ──────────────────────────────────────────────

    def get_notify(self, body: ResourceBody, context: ConversionContext) -> list[str]:
        """Get notify targets as Ansible handler names."""
        notify_attr = body.get_attr("notify")
        if notify_attr is None:
            return []
        refs = [notify_attr] if not isinstance(notify_attr, ArrayLiteral) else notify_attr.elements
        result = []
        for ref in refs:
            resolved = self.resolve(ref, context)
            result.append(_ref_to_handler_name(str(resolved)))
        return result

    def get_when(self, body: ResourceBody, context: ConversionContext) -> str | None:
        """Get the 'when' condition from the conversion context for this resource."""
        return context.current_when

    def ensure_to_state(self, ensure_val: str, mapping: dict[str, str]) -> str:
        """Map a Puppet ensure value to an Ansible state."""
        return mapping.get(ensure_val.lower(), ensure_val)

    def make_task(
        self,
        name: str,
        module: str,
        params: dict[str, Any],
        notify: list[str] | None = None,
        when: str | None = None,
        become: bool = False,
        become_user: str | None = None,
        register: str | None = None,
        loop: Any = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        """Build an Ansible task dict with optional metadata."""
        task: dict[str, Any] = {"name": name, module: params}
        if notify:
            task["notify"] = notify if len(notify) > 1 else notify[0]
        if when:
            task["when"] = when
        if become:
            task["become"] = True
        if become_user:
            task["become_user"] = become_user
        if register:
            task["register"] = register
        if loop is not None:
            task["loop"] = loop
        if tags:
            task["tags"] = tags
        return task

    def todo_task(self, resource_type: str, title: str, reason: str = "") -> dict[str, Any]:
        """Generate a TODO placeholder task for an unconvertible resource."""
        msg = f"TODO: Manual conversion needed — Puppet resource '{resource_type}' title '{title}'"
        if reason:
            msg += f" ({reason})"
        return {
            "name": f"[TODO] {resource_type}: {title}",
            "ansible.builtin.debug": {
                "msg": msg,
            },
        }


class ConversionContext:
    """Carries state during conversion of a single manifest.

    Includes:
    - Variable scope (from VariableAssignment nodes)
    - Puppet version
    - Current when-condition stack (for nested conditionals)
    - Collected handlers
    - Conversion warnings/errors
    """

    def __init__(self, puppet_version: int = 4) -> None:
        self.puppet_version:  int = puppet_version
        self.variables:       dict[str, Any] = {}
        self._when_stack:     list[str] = []
        self.handlers:        list[dict[str, Any]] = []
        self.warnings:        list[str] = []
        self.unconverted:     list[dict[str, str]] = []
        self.collections:     set[str] = set()
        # Track handler names we've already emitted to avoid duplicates
        self._handler_names:  set[str] = set()
        # Optional Hiera-aware scope (set by ManifestConverter after init)
        self.hiera_scope: Any = None

    @property
    def current_when(self) -> str | None:
        if not self._when_stack:
            return None
        return " and ".join(f"({c})" for c in self._when_stack) if len(self._when_stack) > 1 else self._when_stack[0]

    def push_when(self, condition: str) -> None:
        self._when_stack.append(condition)

    def pop_when(self) -> None:
        if self._when_stack:
            self._when_stack.pop()

    def set_variable(self, name: str, value: Any) -> None:
        self.variables[name] = value

    def add_handler(self, name: str, module: str, params: dict[str, Any]) -> None:
        if name not in self._handler_names:
            self._handler_names.add(name)
            self.handlers.append({"name": name, module: params})

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_unconverted(self, resource_type: str, title: str, reason: str = "") -> None:
        self.unconverted.append({
            "type": resource_type,
            "title": title,
            "reason": reason or "no converter available",
        })

    def require_collection(self, collection: str) -> None:
        self.collections.add(collection)


def _ref_to_handler_name(ref: str) -> str:
    """Convert 'Service[nginx]' → 'Restart nginx'."""
    if "[" in ref:
        _type, rest = ref.split("[", 1)
        title = rest.strip("]'\"")
        return f"Restart {title}"
    return f"Restart {ref}"
