"""ManifestConverter — walks the AST and produces Ansible tasks + structure.

This is the central orchestrator:
1. Receives a parsed Manifest (AST)
2. Walks all statements
3. Calls the appropriate resource converter for each resource
4. Handles classes, defined types, conditionals, variable assignments
5. Returns a ConversionResult ready for the generators
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from puppet_to_ansible.converters.base import ConversionContext
from puppet_to_ansible.converters.registry import get_registry
from puppet_to_ansible.parser.ast_nodes import (
    ArrayLiteral,
    BinaryOp,
    BoolLiteral,
    CaseStatement,
    CaseWhen,
    ClassDeclaration,
    ClassDefinition,
    DefinedTypeDefinition,
    ElsifClause,
    FactAccess,
    FunctionCall,
    HashLiteral,
    IfStatement,
    Manifest,
    MethodCall,
    NodeDefinition,
    NumberLiteral,
    PuppetNode,
    RealizeStatement,
    RegexLiteral,
    ResourceAttribute,
    ResourceBody,
    ResourceChain,
    ResourceDeclaration,
    ResourceVirtuality,
    SelectorExpression,
    StringInterpolation,
    StringLiteral,
    TypeCast,
    UnaryOp,
    UndefLiteral,
    UnlessStatement,
    Variable,
    VariableAssignment,
)
from puppet_to_ansible.utils.facts_mapper import map_fact
from puppet_to_ansible.utils.hiera_resolver import HieraResolver, HieraAwareScope, build_hiera_resolver

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

# Puppet ordering/notification metaparameters that have no meaning in Ansible vars
_PUPPET_METAPARAMS: frozenset[str] = frozenset({
    "require", "before", "notify", "subscribe",
    "tag", "alias", "loglevel", "audit", "noop",
    "schedule", "stage",
})


def _is_array_type_node(node: PuppetNode) -> bool:
    """Return True if the AST node is known to produce an Array value.

    Used to decide whether to emit `loop: {{ var }}` (array) or
    `loop: {{ var | dict2items }}` (hash) for dynamic .each loops.
    """
    if isinstance(node, FunctionCall):
        if node.name == "hiera_array":
            return True
        if node.name in ("lookup", "hiera") and len(node.arguments) >= 2:
            # Second arg is the type hint: Array[String], Array, etc.
            type_arg = node.arguments[1]
            type_str = str(getattr(type_arg, "value", type_arg))
            if "Array" in type_str:
                return True
    if isinstance(node, ArrayLiteral):
        return True
    return False


# ── Conversion Result ──────────────────────────────────────────────────────────

@dataclass
class ConversionResult:
    """The output of converting a Puppet manifest."""

    tasks:            list[dict[str, Any]]  = field(default_factory=list)
    handlers:         list[dict[str, Any]]  = field(default_factory=list)
    variables:        dict[str, Any]        = field(default_factory=dict)
    node_definitions: list[dict[str, Any]]  = field(default_factory=list)
    classes:          list[dict[str, Any]]  = field(default_factory=list)
    defined_types:    list[dict[str, Any]]  = field(default_factory=list)
    collections:      set[str]              = field(default_factory=set)
    warnings:         list[str]             = field(default_factory=list)
    unconverted:      list[dict[str, str]]  = field(default_factory=list)
    converted_counts: dict[str, int]        = field(default_factory=dict)
    puppet_version:   int                   = 4
    source_file:      str                   = ""

    def record_converted(self, resource_type: str) -> None:
        self.converted_counts[resource_type] = self.converted_counts.get(resource_type, 0) + 1

    @property
    def total_converted(self) -> int:
        return sum(self.converted_counts.values())

    @property
    def has_classes(self) -> bool:
        return bool(self.classes)

    @property
    def has_defined_types(self) -> bool:
        return bool(self.defined_types)

    @property
    def suggested_output_mode(self) -> str:
        if self.has_classes or self.has_defined_types or self.node_definitions:
            return "role"
        return "playbook"


# ── Main converter ─────────────────────────────────────────────────────────────

class ManifestConverter:
    """Converts a parsed Puppet Manifest AST into a ConversionResult."""

    def __init__(
        self,
        puppet_version: int = 4,
        hiera_resolver: HieraResolver | None = None,
        module_paths: list[str] | None = None,
        known_defined_types: set[str] | None = None,
        shared_virtual_resources: dict | None = None,
    ) -> None:
        self.registry = get_registry()
        self.puppet_version = puppet_version
        self._hiera = hiera_resolver
        self._module_paths = module_paths or []
        # Defined type names known across all files in this module.
        # Used to generate include_tasks instead of TODO when a defined type is called.
        self.known_defined_types: set[str] = set(known_defined_types or [])
        # L5: shared dict for @virtual resources across file boundaries.
        # Inject a single dict from the CLI when converting multiple files so that
        # realize() in file B can find @virtual resources declared in file A.
        self._shared_virtual_resources: dict = (
            shared_virtual_resources if shared_virtual_resources is not None else {}
        )

    def convert(self, manifest: Manifest) -> ConversionResult:
        """Main entry point — convert a full manifest."""
        result = ConversionResult(
            puppet_version=manifest.puppet_version,
            source_file=manifest.source_file,
        )

        # Lazy Hiera setup: if no resolver was injected, try to find hiera.yaml
        if self._hiera is None and manifest.source_file:
            self._hiera = build_hiera_resolver(
                manifest_path=manifest.source_file,
                module_paths=self._module_paths,
            )

        context = ConversionContext(puppet_version=manifest.puppet_version)
        context.hiera_scope = HieraAwareScope(self._hiera, context.variables)

        try:
            self._walk_statements(manifest.statements, context, result)
        except Exception as exc:
            logger.exception("Fatal error walking manifest %s", manifest.source_file)
            result.warnings.append(f"Fatal conversion error: {exc}")

        # Flush context state into result
        result.handlers    = context.handlers
        result.warnings    = context.warnings
        result.unconverted = context.unconverted
        result.collections = context.collections
        # Keep only variables with resolvable (non-Jinja2-placeholder) values for defaults/
        result.variables = {
            k: v for k, v in context.variables.items()
            if not (isinstance(v, str) and v.startswith("{{"))
            and not k.startswith("__")
        }
        # Merge hiera() defaults — these are the fallback values declared inline in
        # hiera('key', default) calls. Only add them when no concrete value was already
        # resolved (so a real Hiera lookup always wins over the inline default).
        for k, v in context.hiera_defaults.items():
            if k not in result.variables:
                result.variables[k] = v
        return result

    # ── Statement dispatcher ──────────────────────────────────────────────────

    def _walk_statements(
        self,
        statements: list[PuppetNode],
        context: ConversionContext,
        result: ConversionResult,
        tasks_target: list[dict[str, Any]] | None = None,
    ) -> None:
        """Walk a list of statements and produce tasks into tasks_target."""
        target = tasks_target if tasks_target is not None else result.tasks

        for stmt in statements:
            if stmt is None:
                continue
            try:
                self._dispatch(stmt, context, result, target)
            except Exception as exc:
                logger.exception("Error processing statement %s", type(stmt).__name__)
                context.warn(f"Statement conversion error ({type(stmt).__name__}): {exc}")
                target.append({
                    "name": f"[TODO] Conversion error in {type(stmt).__name__}",
                    "ansible.builtin.debug": {"msg": f"Conversion error: {exc}"},
                })

    def _dispatch(
        self,
        stmt: PuppetNode,
        context: ConversionContext,
        result: ConversionResult,
        target: list[dict[str, Any]],
    ) -> None:
        """Dispatch a single statement to the correct handler."""

        if isinstance(stmt, VariableAssignment):
            self._handle_variable(stmt, context)

        elif isinstance(stmt, ResourceDeclaration):
            target.extend(self._handle_resource(stmt, context, result))

        elif isinstance(stmt, IfStatement):
            target.extend(self._handle_if(stmt, context, result))

        elif isinstance(stmt, UnlessStatement):
            target.extend(self._handle_unless(stmt, context, result))

        elif isinstance(stmt, CaseStatement):
            target.extend(self._handle_case(stmt, context, result))

        elif isinstance(stmt, ClassDefinition):
            self._handle_class_definition(stmt, context, result)

        elif isinstance(stmt, ClassDeclaration):
            self._handle_class_declaration(stmt, context, result, target)

        elif isinstance(stmt, DefinedTypeDefinition):
            self._handle_defined_type(stmt, context, result)

        elif isinstance(stmt, NodeDefinition):
            self._handle_node_definition(stmt, context, result)

        elif isinstance(stmt, ResourceChain):
            target.extend(self._handle_resource_chain(stmt, context, result))

        elif isinstance(stmt, RealizeStatement):
            target.extend(self._handle_realize(stmt, context))

        elif isinstance(stmt, FunctionCall):
            tasks = self._handle_function_call(stmt, context, result)
            target.extend(tasks)

        elif isinstance(stmt, MethodCall):
            self._handle_method_call(stmt, context, result, target)

        elif isinstance(stmt, list):
            # include_statement returns a list of ClassDeclarations
            for s in stmt:
                if s is not None:
                    self._dispatch(s, context, result, target)

        else:
            logger.debug("Unhandled statement type: %s", type(stmt).__name__)

    # ── Variable handling ─────────────────────────────────────────────────────

    def _handle_variable(self, stmt: VariableAssignment, context: ConversionContext) -> None:
        """Evaluate and store a variable assignment in the context scope."""
        try:
            value = self._resolve_node(stmt.value, context)
            context.set_variable(stmt.name, value)
            # Also store without module prefix for convenience
            bare = stmt.name.split("::")[-1]
            if bare != stmt.name:
                context.set_variable(bare, value)
        except Exception as exc:
            context.warn(f"Could not evaluate variable ${stmt.name}: {exc}")

    # ── Resource handling ─────────────────────────────────────────────────────

    def _handle_resource(
        self,
        decl: ResourceDeclaration,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        # Resource defaults (e.g. File { owner => root }) — store in context
        if decl.is_default:
            resource_type = decl.type_name.lower()
            if decl.bodies:
                for attr in decl.bodies[0].attributes:
                    try:
                        key = f"__default__{resource_type}__{attr.name}"
                        context.set_variable(key, self._resolve_node(attr.value, context))
                    except Exception as exc:
                        context.warn(f"Resource default for {resource_type}.{attr.name}: {exc}")
            return []

        # Virtual resources — stash for later realize()
        if decl.virtuality == ResourceVirtuality.VIRTUAL:
            for body in decl.bodies:
                try:
                    title = self._resolve_title_str(body, context)
                    key = f"__virtual__{decl.type_name.lower()}__{title}"
                    context.set_variable(key, body)
                    # L5: also store in shared dict so realize() in other files can find it
                    self._shared_virtual_resources[key] = body
                except Exception as exc:
                    context.warn(f"Virtual resource @{decl.type_name}: {exc}")
            context.warn(
                f"Virtual resource @{decl.type_name} stored — "
                f"will be converted only if realize() is called."
            )
            return []

        # Exported resources — no direct Ansible equivalent
        if decl.virtuality == ResourceVirtuality.EXPORTED:
            for body in decl.bodies:
                try:
                    title = self._resolve_title_str(body, context)
                except Exception:
                    title = str(getattr(body.title, "value", "?"))
                context.warn(
                    f"Exported resource @@{decl.type_name}[{title}]: "
                    f"no direct Ansible equivalent. "
                    f"Suggested pattern: hostvars[], dynamic inventory, or delegate_to."
                )
                context.add_unconverted(
                    f"@@{decl.type_name}", title,
                    "exported resources have no Ansible equivalent — manual conversion needed"
                )
                tasks.append({
                    "name": f"[TODO] Exported resource: @@{decl.type_name}[{title}]",
                    "ansible.builtin.debug": {
                        "msg": (
                            f"TODO: @@{decl.type_name}[{title}] — "
                            f"Use hostvars[], dynamic inventory, or delegate_to pattern. "
                            f"See: https://docs.ansible.com/ansible/latest/user_guide/playbooks_delegation.html"
                        )
                    },
                })
            return tasks

        # Normal resource — convert each body
        resource_type = decl.type_name.lower()
        for body in decl.bodies:
            try:
                self._apply_resource_defaults(body, resource_type, context)

                # Defined type instantiation — emit include_tasks with vars
                if resource_type in self.known_defined_types and not self.registry.has(resource_type):
                    dt_tasks = self._convert_defined_type_call(resource_type, body, context)
                    tasks.extend(dt_tasks)
                    result.record_converted(resource_type)
                    continue

                new_tasks = self.registry.convert_resource(resource_type, body, context)
                tasks.extend(new_tasks)
                if new_tasks and not any("__puppet_original__" in t for t in new_tasks):
                    result.record_converted(resource_type)
            except Exception as exc:
                title = self._resolve_title_str(body, context)
                logger.exception("Failed to convert %s[%s]", resource_type, title)
                context.warn(f"Converter crash for {resource_type}[{title}]: {exc}")
                context.add_unconverted(resource_type, title, str(exc))
                tasks.append({
                    "name": f"[TODO] {resource_type}: {title}",
                    "ansible.builtin.debug": {
                        "msg": f"TODO: Manual conversion needed — converter error: {exc}"
                    },
                })
        return tasks

    def _apply_resource_defaults(
        self,
        body: ResourceBody,
        resource_type: str,
        context: ConversionContext,
    ) -> None:
        """Inject resource-level defaults (from `File { owner => root }` blocks)."""
        prefix = f"__default__{resource_type}__"
        for key, val in list(context.variables.items()):
            if key.startswith(prefix):
                attr_name = key[len(prefix):]
                if body.get_attr(attr_name) is None:
                    node = (
                        StringLiteral(value=str(val))
                        if isinstance(val, (str, int, float, bool))
                        else StringLiteral(value=str(val))
                    )
                    body.attributes.append(ResourceAttribute(name=attr_name, value=node))

    # ── Conditionals ──────────────────────────────────────────────────────────

    def _handle_if(
        self,
        stmt: IfStatement,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        try:
            when_str = self._condition_to_when(stmt.condition, context)
        except Exception as exc:
            context.warn(f"Could not convert if-condition: {exc}")
            when_str = "true  # TODO: condition conversion failed"

        # if-body
        context.push_when(when_str)
        body_tasks: list[dict[str, Any]] = []
        self._walk_statements(stmt.body, context, result, body_tasks)
        context.pop_when()
        tasks.extend(body_tasks)

        # elsif clauses
        for elsif in stmt.elsif_clauses:
            try:
                elsif_when = self._condition_to_when(elsif.condition, context)
            except Exception as exc:
                context.warn(f"Could not convert elsif-condition: {exc}")
                elsif_when = "true  # TODO: condition conversion failed"
            context.push_when(elsif_when)
            elsif_tasks: list[dict[str, Any]] = []
            self._walk_statements(elsif.body, context, result, elsif_tasks)
            context.pop_when()
            tasks.extend(elsif_tasks)

        # else-body — negate the if condition
        if stmt.else_body:
            neg_when = self._negate_condition(when_str)
            context.push_when(neg_when)
            else_tasks: list[dict[str, Any]] = []
            self._walk_statements(stmt.else_body, context, result, else_tasks)
            context.pop_when()
            tasks.extend(else_tasks)

        return tasks

    def _handle_unless(
        self,
        stmt: UnlessStatement,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        try:
            pos_when = self._condition_to_when(stmt.condition, context)
            when_str = self._negate_condition(pos_when)
        except Exception as exc:
            context.warn(f"Could not convert unless-condition: {exc}")
            when_str = "true  # TODO: condition conversion failed"

        context.push_when(when_str)
        body_tasks: list[dict[str, Any]] = []
        self._walk_statements(stmt.body, context, result, body_tasks)
        context.pop_when()
        tasks.extend(body_tasks)

        if stmt.else_body:
            context.push_when(self._negate_condition(when_str))
            else_tasks: list[dict[str, Any]] = []
            self._walk_statements(stmt.else_body, context, result, else_tasks)
            context.pop_when()
            tasks.extend(else_tasks)

        return tasks

    def _handle_case(
        self,
        stmt: CaseStatement,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []

        try:
            control_str = self._condition_to_when(stmt.control, context)
        except Exception as exc:
            context.warn(f"Could not convert case control expression: {exc}")
            control_str = "unknown"

        for case in stmt.cases:
            try:
                if case.is_default:
                    # default case — no when guard
                    self._walk_statements(case.body, context, result, tasks)
                else:
                    matchers: list[str] = []
                    for m in case.matchers:
                        try:
                            if isinstance(m, StringLiteral):
                                matchers.append(f'{control_str} == "{m.value}"')
                            elif isinstance(m, RegexLiteral):
                                matchers.append(f"{control_str} is match('{m.pattern}')")
                            elif isinstance(m, ArrayLiteral):
                                # 'Ubuntu', 'Debian': — multiple match values
                                for elem in m.elements:
                                    val = self._resolve_node(elem, context)
                                    matchers.append(f'{control_str} == "{val}"')
                            else:
                                val = self._resolve_node(m, context)
                                matchers.append(f'{control_str} == "{val}"')
                        except Exception as exc:
                            context.warn(f"Case matcher conversion error: {exc}")
                            matchers.append(f"true  # TODO: {exc}")

                    when_str = " or ".join(matchers) if matchers else "true"
                    context.push_when(when_str)
                    case_tasks: list[dict[str, Any]] = []
                    self._walk_statements(case.body, context, result, case_tasks)
                    context.pop_when()
                    tasks.extend(case_tasks)
            except Exception as exc:
                context.warn(f"Case branch conversion error: {exc}")

        return tasks

    # ── Class definition ──────────────────────────────────────────────────────

    def _handle_class_definition(
        self,
        cls: ClassDefinition,
        context: ConversionContext,
        result: ConversionResult,
    ) -> None:
        """Convert a `class foo ( params ) { body }` definition."""
        class_context = ConversionContext(puppet_version=context.puppet_version)
        # Inherit parent scope and Hiera resolver
        class_context.variables = dict(context.variables)
        class_context.hiera_scope = context.hiera_scope

        # Set class parameters as variables using their defaults
        class_vars: dict[str, Any] = {}
        for param in cls.parameters:
            try:
                if param.default_value is not None:
                    val = self._resolve_node(param.default_value, class_context)
                    class_context.set_variable(param.name, val)
                    class_vars[param.name] = val
                else:
                    class_vars[param.name] = None
            except Exception as exc:
                context.warn(f"Class {cls.name} param ${param.name}: {exc}")

        # M4: If class inherits, merge parent vars into child vars (child wins on conflict)
        if cls.parent:
            parent_cls = next((c for c in result.classes if c["name"] == cls.parent), None)
            if parent_cls:
                merged_parent = dict(parent_cls.get("vars", {}))
                merged_parent.update(class_vars)  # child overrides parent
                class_vars = merged_parent
                for k, v in parent_cls.get("vars", {}).items():
                    class_context.variables.setdefault(k, v)
            else:
                class_context.warn(
                    f"Class {cls.name} inherits {cls.parent} — "
                    f"parent class not found in conversion scope; "
                    f"include parent role manually and verify vars."
                )

        class_tasks: list[dict[str, Any]] = []
        self._walk_statements(cls.body, class_context, result, class_tasks)

        # Propagate handlers and collections upward
        for h in class_context.handlers:
            if not any(x["name"] == h["name"] for x in context.handlers):
                context.handlers.append(h)
        for col in class_context.collections:
            context.require_collection(col)
        for w in class_context.warnings:
            context.warn(f"[{cls.name}] {w}")
        for u in class_context.unconverted:
            context.add_unconverted(u["type"], u["title"], u["reason"])
        # Propagate hiera() inline defaults upward so they land in defaults/main.yml
        for k, v in class_context.hiera_defaults.items():
            context.hiera_defaults.setdefault(k, v)

        result.classes.append({
            "name":   cls.name,
            "tasks":  class_tasks,
            "vars":   class_vars,
            "parent": cls.parent,
        })

    def _handle_class_declaration(
        self,
        decl: ClassDeclaration,
        context: ConversionContext,
        result: ConversionResult,
        target: list[dict[str, Any]],
    ) -> None:
        """Convert `class { 'foo': param => val }` or `include foo`."""
        role_name = decl.name.replace("::", ".")

        if decl.is_include:
            target.append({
                "name": f"Include role: {decl.name}",
                "ansible.builtin.include_role": {"name": role_name},
            })
        else:
            vars_dict: dict[str, Any] = {}
            for attr in decl.parameters:
                try:
                    vars_dict[attr.name] = self._resolve_node(attr.value, context)
                except Exception as exc:
                    context.warn(f"class declaration param {attr.name}: {exc}")
                    vars_dict[attr.name] = f"# TODO: {exc}"

            task: dict[str, Any] = {
                "name": f"Include role: {decl.name}",
                "ansible.builtin.include_role": {"name": role_name},
            }
            if vars_dict:
                task["vars"] = vars_dict
            target.append(task)

    # ── Defined type ──────────────────────────────────────────────────────────

    def _handle_defined_type(
        self,
        dt: DefinedTypeDefinition,
        context: ConversionContext,
        result: ConversionResult,
    ) -> None:
        """Convert `define nginx::vhost ( params ) { body }` to an include_tasks file."""
        dt_context = ConversionContext(puppet_version=context.puppet_version)
        dt_context.variables = dict(context.variables)

        dt_vars: dict[str, Any] = {}
        for param in dt.parameters:
            try:
                if param.default_value is not None:
                    val = self._resolve_node(param.default_value, dt_context)
                    dt_context.set_variable(param.name, val)
                    dt_vars[param.name] = val
                else:
                    dt_vars[param.name] = None
            except Exception as exc:
                context.warn(f"Defined type {dt.name} param ${param.name}: {exc}")

        dt_tasks: list[dict[str, Any]] = []
        self._walk_statements(dt.body, dt_context, result, dt_tasks)

        for h in dt_context.handlers:
            if not any(x["name"] == h["name"] for x in context.handlers):
                context.handlers.append(h)
        for col in dt_context.collections:
            context.require_collection(col)

        result.defined_types.append({
            "name":  dt.name,
            "tasks": dt_tasks,
            "vars":  dt_vars,
        })
        # Register so sibling manifests in the same module can emit include_tasks
        self.known_defined_types.add(dt.name.lower())

    def _convert_defined_type_call(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        """Convert a defined-type instantiation to include_tasks with vars.

        Example Puppet:
            webstack::vhost { 'myapp':
              docroot => '/var/www/myapp',
              port    => 8080,
            }

        Generates:
            - name: Include defined type webstack::vhost for myapp
              ansible.builtin.include_tasks:
                file: webstack_vhost.yml
              vars:
                name: myapp
                docroot: /var/www/myapp
                port: 8080
        """
        title = self._resolve_title_str(body, context)

        # Build vars dict from resource attributes, skipping Puppet metaparameters
        task_vars: dict[str, Any] = {"name": title}
        for attr in body.attributes:
            if attr.name in _PUPPET_METAPARAMS:
                continue
            try:
                val = self._resolve_node(attr.value, context)
                task_vars[attr.name] = val
            except Exception:
                task_vars[attr.name] = f"{{{{ {attr.name} }}}}"

        # Derive task file name: webstack::vhost → webstack_vhost.yml
        tasks_file = resource_type.replace("::", "_") + ".yml"

        when = context.current_when
        task: dict[str, Any] = {
            "name": f"Include defined type {resource_type} for {title}",
            "ansible.builtin.include_tasks": {"file": tasks_file},
            "vars": task_vars,
        }
        if when:
            task["when"] = when
        return [task]

    # ── Node definitions ──────────────────────────────────────────────────────

    def _handle_node_definition(
        self,
        node: NodeDefinition,
        context: ConversionContext,
        result: ConversionResult,
    ) -> None:
        matchers: list[dict[str, Any]] = []
        for m in node.matchers:
            if isinstance(m, StringLiteral):
                matchers.append({"type": "exact", "value": m.value})
            elif isinstance(m, RegexLiteral):
                matchers.append({"type": "regex", "pattern": m.pattern})
                context.warn(
                    f"Regex node definition '/{m.pattern}/' — "
                    f"add matching hosts manually to the Ansible inventory."
                )
            elif isinstance(m, UndefLiteral):
                matchers.append({"type": "default"})

        node_context = ConversionContext(puppet_version=context.puppet_version)
        node_context.variables = dict(context.variables)
        node_tasks: list[dict[str, Any]] = []
        self._walk_statements(node.body, node_context, result, node_tasks)

        result.node_definitions.append({
            "matchers":   matchers,
            "is_default": node.is_default,
            "tasks":      node_tasks,
        })

    # ── Resource chains ───────────────────────────────────────────────────────

    def _handle_resource_chain(
        self,
        chain: ResourceChain,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        """A -> B ~> C — convert both sides and preserve order (Ansible is sequential)."""
        tasks: list[dict[str, Any]] = []
        for side in (chain.left, chain.right):
            if isinstance(side, ResourceDeclaration):
                tasks.extend(self._handle_resource(side, context, result))
            elif isinstance(side, ResourceChain):
                tasks.extend(self._handle_resource_chain(side, context, result))
            elif isinstance(side, ClassDeclaration):
                self._handle_class_declaration(side, context, result, tasks)
            # ResourceReference in a chain is just an ordering hint — skip
        return tasks

    # ── Realize ───────────────────────────────────────────────────────────────

    def _handle_realize(
        self,
        stmt: RealizeStatement,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for ref in stmt.references:
            try:
                title_node = ref.titles[0] if ref.titles else StringLiteral(value="?")
                title = str(self._resolve_node(title_node, context))
                key = f"__virtual__{ref.type_name.lower()}__{title}"
                # L5: check local scope first, then shared cross-file dict
                virtual_body = context.variables.get(key) or self._shared_virtual_resources.get(key)
                if isinstance(virtual_body, ResourceBody):
                    new_tasks = self.registry.convert_resource(
                        ref.type_name.lower(), virtual_body, context
                    )
                    tasks.extend(new_tasks)
                else:
                    context.warn(
                        f"realize(): virtual resource {ref.type_name}[{title}] not found in scope"
                    )
            except Exception as exc:
                context.warn(f"realize() error for {ref.type_name}: {exc}")
        return tasks

    # ── Function calls ────────────────────────────────────────────────────────

    def _handle_function_call(
        self,
        fn: FunctionCall,
        context: ConversionContext,
        result: ConversionResult,
    ) -> list[dict[str, Any]]:
        try:
            if fn.name == "__import__":
                path = fn.arguments[0].value if fn.arguments else "?"
                context.warn(
                    f"Puppet 3 'import \"{path}\"' found. "
                    f"In Ansible use include_tasks or separate role files. "
                    f"The imported file must be converted separately."
                )
                return []

            if fn.name == "create_resources":
                return self._handle_create_resources(fn, context)

            if fn.name == "fail":
                # M3: convert fail() to ansible.builtin.fail with when: preserved
                msg = self._resolve_node(fn.arguments[0], context) if fn.arguments else "Puppet fail() — manual review needed"
                msg_str = str(msg).strip('"\'')
                task: dict[str, Any] = {
                    "name": f"Assert: {msg_str[:60]}",
                    "ansible.builtin.fail": {"msg": msg_str},
                }
                if context.current_when:
                    task["when"] = context.current_when
                return [task]

            if fn.name in ("hiera", "lookup"):
                # Called as a statement (unusual) — ignore
                return []

        except Exception as exc:
            context.warn(f"Function call {fn.name}(): {exc}")

        return []

    def _handle_create_resources(
        self,
        fn: FunctionCall,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        """create_resources('type', $hash) → loop or per-instance tasks."""
        if len(fn.arguments) < 2:
            context.warn("create_resources() called with fewer than 2 arguments")
            return []

        try:
            type_arg = self._resolve_node(fn.arguments[0], context)
            hash_arg = self._resolve_node(fn.arguments[1], context)
        except Exception as exc:
            context.warn(f"create_resources() argument resolution error: {exc}")
            return []

        type_name  = str(type_arg)
        tasks_file = type_name.replace("::", "_") + ".yml"

        if isinstance(hash_arg, dict):
            tasks: list[dict[str, Any]] = []
            for title, params in hash_arg.items():
                task: dict[str, Any] = {
                    "name": f"Create {type_name}: {title}",
                    "ansible.builtin.include_tasks": {"file": tasks_file},
                    "vars": {
                        "resource_title": title,
                        **(params if isinstance(params, dict) else {}),
                    },
                }
                tasks.append(task)
            return tasks

        # Dynamic hash — use a loop
        var_repr = str(hash_arg).strip("{} ")
        return [{
            "name": f"Create {type_name} resources",
            "ansible.builtin.include_tasks": {"file": tasks_file},
            "loop": f"{{{{ {type_name.replace('::', '_')}_list | dict2items }}}}",
            "loop_control": {"loop_var": "resource_item"},
            "vars": {
                "resource_title": "{{ resource_item.key }}",
                "resource_params": "{{ resource_item.value }}",
            },
        }]

    # ── Method calls ──────────────────────────────────────────────────────────

    def _handle_method_call(
        self,
        stmt: MethodCall,
        context: ConversionContext,
        result: ConversionResult,
        target: list[dict[str, Any]],
    ) -> None:
        """$collection.each |$k, $v| { ... } — unroll if static, warn if dynamic."""
        if stmt.method != "each" or not stmt.block:
            return

        try:
            receiver_val = self._resolve_node(stmt.receiver, context)
            block = stmt.block
            params = block.parameters

            if isinstance(receiver_val, dict):
                for k, v in receiver_val.items():
                    iter_ctx = ConversionContext(puppet_version=context.puppet_version)
                    iter_ctx.variables = dict(context.variables)
                    if len(params) >= 1:
                        iter_ctx.set_variable(params[0], k)
                    if len(params) >= 2:
                        iter_ctx.set_variable(params[1], v)
                    self._walk_statements(block.body, iter_ctx, result, target)
                    # Propagate handlers/collections
                    for h in iter_ctx.handlers:
                        if not any(x["name"] == h["name"] for x in context.handlers):
                            context.handlers.append(h)
                    for col in iter_ctx.collections:
                        context.require_collection(col)

            elif isinstance(receiver_val, list):
                # Puppet supports two array .each signatures:
                #   $arr.each |$val| { ... }            — 1 param
                #   $arr.each |Integer $idx, Type $val| — 2 params (index, value)
                for idx, item in enumerate(receiver_val):
                    iter_ctx = ConversionContext(puppet_version=context.puppet_version)
                    iter_ctx.variables = dict(context.variables)
                    if len(params) == 1:
                        iter_ctx.set_variable(params[0], item)
                    elif len(params) >= 2:
                        # First param is the index, second is the value
                        iter_ctx.set_variable(params[0], idx)
                        iter_ctx.set_variable(params[1], item)
                    self._walk_statements(block.body, iter_ctx, result, target)
                    for h in iter_ctx.handlers:
                        if not any(x["name"] == h["name"] for x in context.handlers):
                            context.handlers.append(h)
                    for col in iter_ctx.collections:
                        context.require_collection(col)

            else:
                # Dynamic receiver — emit a loop-based task
                receiver_str = self._node_to_str(stmt.receiver, context)
                context.warn(
                    f".each loop on dynamic value '{receiver_str}' — "
                    f"inner body converted with loop variable. Manual review needed."
                )

                # Detect whether the receiver is array- or hash-typed so we
                # can emit the correct loop style.
                is_array_receiver = _is_array_type_node(stmt.receiver)

                loop_ctx = ConversionContext(puppet_version=context.puppet_version)
                loop_ctx.variables = dict(context.variables)
                if is_array_receiver:
                    # Array iteration: |$val| or |Integer $idx, Type $val|
                    if len(params) == 1:
                        loop_ctx.set_variable(params[0], "{{ item }}")
                    elif len(params) >= 2:
                        loop_ctx.set_variable(params[0], "{{ loop.index0 }}")
                        loop_ctx.set_variable(params[1], "{{ item }}")
                else:
                    # Hash/dict iteration: |$key, $val|
                    if params:
                        loop_ctx.set_variable(params[0], "{{ item.key }}")
                    if len(params) >= 2:
                        loop_ctx.set_variable(params[1], "{{ item.value }}")

                inner_tasks: list[dict[str, Any]] = []
                self._walk_statements(block.body, loop_ctx, result, inner_tasks)

                # Strip outer {{ }} from receiver_str — _node_to_str already
                # wraps variables in Jinja2 braces, so we must not wrap again.
                loop_var = receiver_str.strip()
                if loop_var.startswith("{{") and loop_var.endswith("}}"):
                    loop_var = loop_var[2:-2].strip()

                if is_array_receiver:
                    loop_expr = f"{{{{ {loop_var} }}}}"
                else:
                    loop_expr = f"{{{{ {loop_var} | dict2items }}}}"

                for t in inner_tasks:
                    t["loop"] = loop_expr
                    target.append(t)

        except Exception as exc:
            context.warn(f".each method call conversion error: {exc}")

    # ── Condition conversion ──────────────────────────────────────────────────

    def _strip_jinja_for_when(self, val: Any) -> str:
        """Strip {{ }} wrapper from a resolved value for embedding in a when: expression.

        when: values are already in a Jinja2 expression context, so inner {{ }} are invalid.
        """
        s = str(val)
        if s.startswith("{{") and s.endswith("}}"):
            return s[2:-2].strip()
        return s

    def _condition_to_when(self, node: PuppetNode, context: ConversionContext) -> str:
        """Recursively convert a Puppet condition node to an Ansible 'when' string."""

        if isinstance(node, BinaryOp):
            op = node.operator

            # H4: $var == undef  →  var is none  (idiomatic Jinja2)
            if op == "==" and isinstance(node.right, UndefLiteral):
                left = self._condition_to_when(node.left, context)
                return f"{left} is none"
            if op == "==" and isinstance(node.left, UndefLiteral):
                right = self._condition_to_when(node.right, context)
                return f"{right} is none"
            if op == "!=" and isinstance(node.right, UndefLiteral):
                left = self._condition_to_when(node.left, context)
                return f"{left} is not none"
            if op == "!=" and isinstance(node.left, UndefLiteral):
                right = self._condition_to_when(node.right, context)
                return f"{right} is not none"

            left  = self._condition_to_when(node.left,  context)
            right = self._condition_to_when(node.right, context)

            _op_map = {
                "==": "==", "!=": "!=",
                "<":  "<",  ">":  ">",
                "<=": "<=", ">=": ">=",
                "and": "and", "or": "or",
                "=~": "is match", "!~": "is not match",
                "in": "in",
            }
            ansible_op = _op_map.get(op, op)

            if op in ("=~", "!~"):
                pattern = (
                    node.right.pattern if isinstance(node.right, RegexLiteral)
                    else self._strip_jinja_for_when(self._resolve_node(node.right, context))
                )
                return f"{left} {ansible_op}('{pattern}')"
            return f"{left} {ansible_op} {right}"

        if isinstance(node, UnaryOp):
            # M2: simplify double-negation: not (a == b) → a != b, not (a != b) → a == b
            if isinstance(node.operand, BinaryOp):
                inner_op = node.operand.operator
                if inner_op in ("==", "!="):
                    flipped = "!=" if inner_op == "==" else "=="
                    left  = self._condition_to_when(node.operand.left,  context)
                    right = self._condition_to_when(node.operand.right, context)
                    return f"{left} {flipped} {right}"
            operand = self._condition_to_when(node.operand, context)
            return f"not ({operand})"

        if isinstance(node, Variable):
            return self._var_to_when(node)

        if isinstance(node, FactAccess):
            return self._fact_access_to_str(node, context)

        if isinstance(node, StringLiteral):
            return f'"{node.value}"'

        if isinstance(node, NumberLiteral):
            return str(node.value)

        if isinstance(node, BoolLiteral):
            return "true" if node.value else "false"

        if isinstance(node, UndefLiteral):
            return "none"

        if isinstance(node, TypeCast):
            inner = self._condition_to_when(node.value, context)
            return f"{inner} | {node.type_name.lower()}"

        if isinstance(node, FunctionCall):
            if node.name in ("hiera", "lookup") and node.arguments:
                key = self._resolve_node(node.arguments[0], context)
                return str(key).replace("::", "_")
            return self._strip_jinja_for_when(self._resolve_node(node, context))

        if isinstance(node, MethodCall):
            # e.g. $array.empty? → variable | length == 0
            receiver = self._condition_to_when(node.receiver, context)
            if node.method in ("empty", "empty?"):
                return f"{receiver} | length == 0"
            if node.method in ("nil?", "nil"):
                return f"{receiver} is not defined"
            return f"{receiver}"

        if isinstance(node, ArrayLiteral):
            items = [self._condition_to_when(e, context) for e in node.elements]
            return "[" + ", ".join(items) + "]"

        # Fallback — strip {{ }} since when: is already a Jinja2 expression context
        return self._strip_jinja_for_when(self._node_to_str(node, context))

    def _var_to_when(self, var: Variable) -> str:
        """Convert a Puppet variable reference to an Ansible fact or variable name."""
        fact = map_fact(f"${var.name}")
        if not fact.startswith("UNMAPPED"):
            return fact
        # Convert $module::param → module_param
        return var.bare_name.replace("::", "_")

    def _fact_access_to_str(self, node: FactAccess, context: ConversionContext) -> str:
        """$facts['os']['family'] → ansible_os_family."""
        keys = [str(self._resolve_node(k, context)) for k in node.keys]
        # Try direct fact lookup first
        puppet_key = ".".join(keys)
        fact = map_fact(f"$facts['{puppet_key}']")
        if not fact.startswith("UNMAPPED"):
            return fact
        # Build ansible fact name from keys
        return "ansible_" + "_".join(keys)

    def _negate_condition(self, when: str) -> str:
        # M2: simplify negation of simple equality/inequality at the string level.
        # This handles unless-generated negations that can't go through AST UnaryOp.
        # Pattern: "left == right" → "left != right" (and vice-versa).
        import re as _re
        eq_m  = _re.fullmatch(r'(.+?) == (.+)', when.strip())
        neq_m = _re.fullmatch(r'(.+?) != (.+)', when.strip())
        if eq_m:
            return f"{eq_m.group(1)} != {eq_m.group(2)}"
        if neq_m:
            return f"{neq_m.group(1)} == {neq_m.group(2)}"
        return f"not ({when})"

    # ── Node resolution ───────────────────────────────────────────────────────

    def _resolve_node(self, node: PuppetNode, context: ConversionContext) -> Any:
        """Resolve any AST node to a plain Python value.

        Delegates to BaseConverter.resolve() which handles all node types.
        Creates a lightweight resolver instance (cached via module-level singleton).
        """
        return _RESOLVER.resolve(node, context)

    def _resolve_title_str(self, body: ResourceBody, context: ConversionContext) -> str:
        """Resolve the resource title to a plain string."""
        try:
            val = self._resolve_node(body.title, context)
            if isinstance(val, list):
                return ", ".join(str(v) for v in val)
            return str(val)
        except Exception:
            return str(getattr(body.title, "value", "?"))

    def _node_to_str(self, node: PuppetNode, context: ConversionContext) -> str:
        try:
            return str(self._resolve_node(node, context))
        except Exception:
            return str(getattr(node, "value", repr(node)))


# ── Lightweight resolver singleton (avoids re-instantiating on every call) ────

class _ResolverConverter:
    """Minimal BaseConverter subclass used only for node resolution."""
    puppet_type = "__resolver__"

    def convert(self, *args: Any) -> list[Any]:  # type: ignore[override]
        return []

    # Inherit resolve() and helpers from BaseConverter
    from puppet_to_ansible.converters.base import BaseConverter as _BC
    resolve              = _BC.resolve
    _resolve_variable    = _BC._resolve_variable
    _resolve_interpolation = _BC._resolve_interpolation
    _resolve_function    = _BC._resolve_function
    _resolve_selector    = _BC._resolve_selector


# Import BaseConverter properly to avoid class-body import issues
from puppet_to_ansible.converters.base import BaseConverter as _BaseConverter  # noqa: E402

class _ActualResolver(_BaseConverter):
    puppet_type = "__resolver__"

    def convert(self, *args: Any) -> list[Any]:  # type: ignore[override]
        return []


_RESOLVER = _ActualResolver()
