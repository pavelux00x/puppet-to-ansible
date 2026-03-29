"""AST node definitions for the Puppet DSL parser.

Every construct in a Puppet manifest is represented as a dataclass node.
The parser produces a tree of these nodes; converters consume them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


# ── Enums ─────────────────────────────────────────────────────────────────────

class EnsureValue(str, Enum):
    """Puppet ensure parameter values."""
    PRESENT   = "present"
    INSTALLED = "installed"
    LATEST    = "latest"
    ABSENT    = "absent"
    PURGED    = "purged"
    RUNNING   = "running"
    STOPPED   = "stopped"
    FILE      = "file"
    DIRECTORY = "directory"
    LINK      = "link"


class ResourceVirtuality(Enum):
    NORMAL   = auto()  # package { 'foo': }
    VIRTUAL  = auto()  # @package { 'foo': }
    EXPORTED = auto()  # @@package { 'foo': }


# ── Base ──────────────────────────────────────────────────────────────────────

@dataclass
class PuppetNode:
    """Base class for all AST nodes."""
    line: int = 0
    col:  int = 0

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(line={self.line})"


# ── Literal values ─────────────────────────────────────────────────────────────

@dataclass
class StringLiteral(PuppetNode):
    value: str = ""
    interpolated: bool = False  # True for double-quoted strings with ${var}


@dataclass
class NumberLiteral(PuppetNode):
    value: int | float = 0


@dataclass
class BoolLiteral(PuppetNode):
    value: bool = False


@dataclass
class UndefLiteral(PuppetNode):
    pass


@dataclass
class ArrayLiteral(PuppetNode):
    elements: list[PuppetNode] = field(default_factory=list)


@dataclass
class HashLiteral(PuppetNode):
    pairs: list[tuple[PuppetNode, PuppetNode]] = field(default_factory=list)

    def to_dict(self) -> dict[Any, Any]:
        """Convert to plain Python dict (for simple literal hashes)."""
        result = {}
        for key, val in self.pairs:
            k = key.value if hasattr(key, "value") else str(key)
            v = val.value if hasattr(val, "value") else val
            result[k] = v
        return result


@dataclass
class RegexLiteral(PuppetNode):
    pattern: str = ""


# ── Variables & References ────────────────────────────────────────────────────

@dataclass
class Variable(PuppetNode):
    """A Puppet variable like $foo, $::osfamily, $module::param."""
    name: str = ""

    @property
    def is_scoped(self) -> bool:
        return "::" in self.name

    @property
    def is_top_scope(self) -> bool:
        return self.name.startswith("::")

    @property
    def bare_name(self) -> str:
        """Remove leading :: if present."""
        return self.name.lstrip(":")


@dataclass
class FactAccess(PuppetNode):
    """Puppet 4 fact access: $facts['os']['family']."""
    keys: list[PuppetNode] = field(default_factory=list)


@dataclass
class ResourceReference(PuppetNode):
    """A reference to another resource: Package['nginx'], File['/etc/hosts']."""
    type_name: str = ""
    titles: list[PuppetNode] = field(default_factory=list)


# ── Expressions ───────────────────────────────────────────────────────────────

@dataclass
class BinaryOp(PuppetNode):
    operator: str = ""  # ==, !=, <, >, <=, >=, and, or, in, =~, !~, +, -, *, /
    left:  PuppetNode = field(default_factory=PuppetNode)
    right: PuppetNode = field(default_factory=PuppetNode)


@dataclass
class UnaryOp(PuppetNode):
    operator: str = ""  # not, -, !
    operand: PuppetNode = field(default_factory=PuppetNode)


@dataclass
class SelectorExpression(PuppetNode):
    """The ? { } selector operator (Puppet ternary-like)."""
    control: PuppetNode = field(default_factory=PuppetNode)
    cases:   list[tuple[PuppetNode, PuppetNode]] = field(default_factory=list)
    # cases: list of (match_value, result_value); use UndefLiteral for 'default'


@dataclass
class FunctionCall(PuppetNode):
    """A function call: template('foo'), hiera('key'), lookup('key', ...)."""
    name:      str = ""
    arguments: list[PuppetNode] = field(default_factory=list)
    # For method-style calls: $array.each |$x| { ... }
    block:     LambdaBlock | None = None


@dataclass
class MethodCall(PuppetNode):
    """Method call on an expression: $list.each |$x| { ... }."""
    receiver:   PuppetNode = field(default_factory=PuppetNode)
    method:     str = ""
    arguments:  list[PuppetNode] = field(default_factory=list)
    block:      LambdaBlock | None = None


@dataclass
class LambdaBlock(PuppetNode):
    """Lambda block: |$x| { ... } or |$k, $v| { ... }."""
    parameters: list[str] = field(default_factory=list)
    body:       list[PuppetNode] = field(default_factory=list)


@dataclass
class StringInterpolation(PuppetNode):
    """A double-quoted string with embedded expressions: "hello ${name}!"."""
    parts: list[PuppetNode | str] = field(default_factory=list)


@dataclass
class TypeCast(PuppetNode):
    """Puppet type cast: Integer($str), String($num)."""
    type_name: str = ""
    value:     PuppetNode = field(default_factory=PuppetNode)


# ── Resource Declarations ──────────────────────────────────────────────────────

@dataclass
class ResourceAttribute(PuppetNode):
    """A single attribute in a resource body: ensure => present."""
    name:  str = ""
    value: PuppetNode = field(default_factory=PuppetNode)


@dataclass
class ResourceBody(PuppetNode):
    """One title + its attributes inside a resource declaration."""
    title:      PuppetNode = field(default_factory=PuppetNode)
    attributes: list[ResourceAttribute] = field(default_factory=list)

    def get_attr(self, name: str) -> PuppetNode | None:
        for attr in self.attributes:
            if attr.name == name:
                return attr.value
        return None

    def get_str(self, name: str, default: str = "") -> str:
        val = self.get_attr(name)
        if val is None:
            return default
        if isinstance(val, StringLiteral):
            return val.value
        if isinstance(val, Variable):
            return f"{{{{ {val.bare_name} }}}}"  # Jinja2 placeholder
        return str(val)


@dataclass
class ResourceDeclaration(PuppetNode):
    """A Puppet resource declaration block.

    package { 'nginx': ensure => installed }
    """
    type_name:   str = ""
    bodies:      list[ResourceBody] = field(default_factory=list)
    virtuality:  ResourceVirtuality = ResourceVirtuality.NORMAL
    # Resource defaults (no title): Package { provider => yum }
    is_default:  bool = False


@dataclass
class ResourceCollector(PuppetNode):
    """<| |> or <<| |>> — realize virtual/exported resources."""
    type_name:  str = ""
    query:      PuppetNode | None = None  # None = collect all
    exported:   bool = False


@dataclass
class ResourceChain(PuppetNode):
    """Ordering/notification chains: A -> B, A ~> B."""
    operator:  str = ""  # '->' or '~>'
    left:      PuppetNode = field(default_factory=PuppetNode)
    right:     PuppetNode = field(default_factory=PuppetNode)


# ── Classes & Defined Types ───────────────────────────────────────────────────

@dataclass
class ClassParameter(PuppetNode):
    """A parameter in a class or defined-type signature."""
    name:         str = ""
    type_expr:    PuppetNode | None = None  # String, Integer[1,10], Optional[String], ...
    default_value: PuppetNode | None = None


@dataclass
class ClassDefinition(PuppetNode):
    """class foo ( ... ) inherits bar { ... }"""
    name:       str = ""
    parameters: list[ClassParameter] = field(default_factory=list)
    parent:     str | None = None          # inherits
    body:       list[PuppetNode] = field(default_factory=list)


@dataclass
class ClassDeclaration(PuppetNode):
    """class { 'foo': param => value } or include foo."""
    name:       str = ""
    parameters: list[ResourceAttribute] = field(default_factory=list)
    # True for `include foo` / `include foo, bar`
    is_include: bool = False


@dataclass
class DefinedTypeDefinition(PuppetNode):
    """define nginx::vhost ( ... ) { ... }"""
    name:       str = ""
    parameters: list[ClassParameter] = field(default_factory=list)
    body:       list[PuppetNode] = field(default_factory=list)


# ── Node Definitions ───────────────────────────────────────────────────────────

@dataclass
class NodeDefinition(PuppetNode):
    """node 'web01.example.com' { ... } or node /regex/ { ... }."""
    matchers: list[PuppetNode] = field(default_factory=list)  # StringLiteral or RegexLiteral
    is_default: bool = False
    body:       list[PuppetNode] = field(default_factory=list)


# ── Conditionals ──────────────────────────────────────────────────────────────

@dataclass
class IfStatement(PuppetNode):
    condition: PuppetNode = field(default_factory=PuppetNode)
    body:      list[PuppetNode] = field(default_factory=list)
    elsif_clauses: list[ElsifClause] = field(default_factory=list)
    else_body: list[PuppetNode] = field(default_factory=list)


@dataclass
class ElsifClause(PuppetNode):
    condition: PuppetNode = field(default_factory=PuppetNode)
    body:      list[PuppetNode] = field(default_factory=list)


@dataclass
class UnlessStatement(PuppetNode):
    condition: PuppetNode = field(default_factory=PuppetNode)
    body:      list[PuppetNode] = field(default_factory=list)
    else_body: list[PuppetNode] = field(default_factory=list)


@dataclass
class CaseStatement(PuppetNode):
    control: PuppetNode = field(default_factory=PuppetNode)
    cases:   list[CaseWhen] = field(default_factory=list)


@dataclass
class CaseWhen(PuppetNode):
    matchers: list[PuppetNode] = field(default_factory=list)  # values or 'default'
    body:     list[PuppetNode] = field(default_factory=list)
    is_default: bool = False


# ── Variable Assignment ───────────────────────────────────────────────────────

@dataclass
class VariableAssignment(PuppetNode):
    """$foo = expr"""
    name:  str = ""
    value: PuppetNode = field(default_factory=PuppetNode)


# ── Realize ───────────────────────────────────────────────────────────────────

@dataclass
class RealizeStatement(PuppetNode):
    """realize Package['foo']"""
    references: list[ResourceReference] = field(default_factory=list)


# ── Top-level manifest ────────────────────────────────────────────────────────

@dataclass
class Manifest(PuppetNode):
    """The root node of a parsed Puppet manifest."""
    statements: list[PuppetNode] = field(default_factory=list)
    source_file: str = ""
    puppet_version: int = 4  # 3 or 4
