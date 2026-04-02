"""Lark transformer: converts the parse tree into AST nodes.

Each method name matches a rule in puppet.lark.
The transformer is called bottom-up — leaf nodes are processed first.
"""
from __future__ import annotations

import re
from typing import Any

from lark import Token, Transformer, Tree, v_args

from src.parser.ast_nodes import (
    ArrayLiteral,
    BinaryOp,
    BoolLiteral,
    CaseStatement,
    CaseWhen,
    ClassDeclaration,
    ClassDefinition,
    ClassParameter,
    DefinedTypeDefinition,
    ElsifClause,
    FactAccess,
    FunctionCall,
    HashLiteral,
    IfStatement,
    LambdaBlock,
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
    ResourceCollector,
    ResourceDeclaration,
    ResourceReference,
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


def _tok_line(token: Token | Tree | Any) -> int:
    if isinstance(token, Token):
        return token.line or 0
    return 0


def _parse_dq_string(raw: str) -> StringLiteral | StringInterpolation:
    """Parse a double-quoted string — detect ${var} / $var interpolations."""
    inner = raw[1:-1]  # strip surrounding "..."
    # Unescape common sequences
    inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t")

    pattern = re.compile(r'\$\{([^}]+)\}|\$([a-zA-Z_][a-zA-Z0-9_]*(?:::[a-zA-Z_][a-zA-Z0-9_]*)*)')
    parts: list[PuppetNode | str] = []
    last = 0
    for m in pattern.finditer(inner):
        if m.start() > last:
            parts.append(inner[last:m.start()])
        var_name = m.group(1) or m.group(2)
        parts.append(Variable(name=var_name))
        last = m.end()
    if last < len(inner):
        parts.append(inner[last:])

    if not any(isinstance(p, PuppetNode) for p in parts):
        return StringLiteral(value=inner, interpolated=False)
    return StringInterpolation(parts=parts)


class PuppetTransformer(Transformer):
    """Transforms a lark parse tree into Puppet AST nodes."""

    # ── Top-level ──────────────────────────────────────────────────────────────

    def start(self, stmts: list[Any]) -> Manifest:
        flat = []
        for s in stmts:
            if isinstance(s, list):
                flat.extend(s)
            elif s is not None:
                flat.append(s)
        return Manifest(statements=flat)

    def statement(self, items: list[Any]) -> Any:
        return items[0] if items else None

    # ── Resource Declaration ────────────────────────────────────────────────────

    def resource_declaration(self, items: list[Any]) -> ResourceDeclaration:
        virtuality, rtype, bodies = items[0], items[1], items[2]
        return ResourceDeclaration(type_name=rtype, bodies=bodies, virtuality=virtuality)

    def virtuality(self, items: list[Any]) -> ResourceVirtuality:
        if items and str(items[0]) == "@@":
            return ResourceVirtuality.EXPORTED
        if items and str(items[0]).startswith("@"):
            return ResourceVirtuality.VIRTUAL
        return ResourceVirtuality.NORMAL

    def resource_type(self, items: list[Any]) -> str:
        return "::".join(str(i) for i in items)

    def resource_bodies(self, items: list[Any]) -> list[ResourceBody]:
        return [i for i in items if isinstance(i, ResourceBody)]

    def resource_body(self, items: list[Any]) -> ResourceBody:
        title = items[0]
        attrs: list[ResourceAttribute] = []
        if len(items) > 1 and isinstance(items[1], list):
            attrs = items[1]
        return ResourceBody(title=title, attributes=attrs)

    def resource_title(self, items: list[Any]) -> Any:
        return items[0]

    def resource_attributes(self, items: list[Any]) -> list[ResourceAttribute]:
        return [i for i in items if isinstance(i, ResourceAttribute)]

    def resource_attribute(self, items: list[Any]) -> ResourceAttribute:
        name = str(items[0])
        value = items[1]
        return ResourceAttribute(name=name, value=value, line=_tok_line(items[0]))

    def resource_attr_unless(self, items: list[Any]) -> ResourceAttribute:
        # `unless => expr` — the keyword "unless" is a valid exec attribute
        return ResourceAttribute(name="unless", value=items[0], line=0)

    def resource_default(self, items: list[Any]) -> ResourceDeclaration:
        rtype = items[0]
        attrs = items[1] if len(items) > 1 and isinstance(items[1], list) else []
        body = ResourceBody(title=StringLiteral(value="__default__"), attributes=attrs)
        return ResourceDeclaration(type_name=rtype, bodies=[body], is_default=True)

    def resource_type_cap(self, items: list[Any]) -> str:
        return "::".join(str(i) for i in items)

    # ── Resource Collector ──────────────────────────────────────────────────────

    def resource_collector(self, items: list[Any]) -> ResourceCollector:
        rtype = items[0]
        query = items[1] if len(items) > 1 else None
        exported = False  # <<| |>> handled separately in grammar
        return ResourceCollector(type_name=rtype, query=query, exported=exported)

    def collection_query(self, items: list[Any]) -> Any:
        return items[0]

    # ── Resource Chain ───────────────────────────────────────────────────────────

    def resource_chain(self, items: list[Any]) -> ResourceChain:
        # items: [expr, op, expr, op, expr ...]
        result = items[0]
        i = 1
        while i < len(items) - 1:
            op  = str(items[i])
            rhs = items[i + 1]
            result = ResourceChain(operator=op, left=result, right=rhs)
            i += 2
        return result

    def chainable(self, items: list[Any]) -> Any:
        return items[0]

    # ── Class Definition ─────────────────────────────────────────────────────────

    def class_definition(self, items: list[Any]) -> ClassDefinition:
        name    = items[0]
        params: list[ClassParameter] = []
        parent: str | None = None
        body:   list[PuppetNode] = []
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], ClassParameter):
                params = item
            elif isinstance(item, str) and "::" in item or (isinstance(item, str) and re.match(r"^[a-z]", item)):
                parent = item
            elif isinstance(item, list):
                body = [s for s in item if s is not None]
            elif isinstance(item, PuppetNode):
                body.append(item)
        return ClassDefinition(name=name, parameters=params, parent=parent, body=body)

    def class_name(self, items: list[Any]) -> str:
        return "::".join(str(i) for i in items)

    def class_params(self, items: list[Any]) -> list[ClassParameter]:
        if items and isinstance(items[0], list):
            return items[0]
        return []

    def class_parameter_list(self, items: list[Any]) -> list[ClassParameter]:
        return [i for i in items if isinstance(i, ClassParameter)]

    def class_parameter(self, items: list[Any]) -> ClassParameter:
        type_expr = None
        var_name  = None
        default   = None
        for item in items:
            if isinstance(item, Token) and str(item).startswith("$"):
                var_name = str(item)[1:]
            elif isinstance(item, StringLiteral) and item.value and item.value[0].isupper():
                type_expr = item
            elif isinstance(item, PuppetNode):
                if var_name is None and isinstance(item, StringLiteral):
                    type_expr = item
                else:
                    default = item
        return ClassParameter(name=var_name or "", type_expr=type_expr, default_value=default)

    def class_inherits(self, items: list[Any]) -> str:
        return items[0] if items else ""

    # ── Defined Type ──────────────────────────────────────────────────────────────

    def defined_type_definition(self, items: list[Any]) -> DefinedTypeDefinition:
        name = items[0]
        params: list[ClassParameter] = []
        body: list[PuppetNode] = []
        for item in items[1:]:
            if isinstance(item, list) and item and isinstance(item[0], ClassParameter):
                params = item
            elif isinstance(item, list):
                body = [s for s in item if s is not None]
            elif isinstance(item, PuppetNode):
                body.append(item)
        return DefinedTypeDefinition(name=name, parameters=params, body=body)

    # ── Class Declaration ─────────────────────────────────────────────────────────

    def class_declaration(self, items: list[Any]) -> ClassDeclaration:
        name_expr = items[0]
        attrs: list[ResourceAttribute] = []
        if len(items) > 1 and isinstance(items[1], list):
            attrs = items[1]
        name = name_expr.value if isinstance(name_expr, StringLiteral) else str(getattr(name_expr, "value", name_expr))
        return ClassDeclaration(name=name, parameters=attrs)

    def string_expr(self, items: list[Any]) -> Any:
        return items[0]

    def include_statement(self, items: list[Any]) -> list[ClassDeclaration]:
        refs = items[0] if items else []
        return [ClassDeclaration(name=r, is_include=True) for r in refs]

    def contain_statement(self, items: list[Any]) -> list[ClassDeclaration]:
        # `contain` behaves like `include` for conversion purposes
        refs = items[0] if items else []
        return [ClassDeclaration(name=r, is_include=True) for r in refs]

    def include_list(self, items: list[Any]) -> list[str]:
        return items

    def include_item(self, items: list[Any]) -> str:
        return "::".join(str(i) for i in items)

    # ── Node Definition ───────────────────────────────────────────────────────────

    def node_definition(self, items: list[Any]) -> NodeDefinition:
        matchers = items[0]
        body = [s for s in items[1:] if s is not None]
        is_default = any(isinstance(m, UndefLiteral) for m in matchers)
        return NodeDefinition(matchers=matchers, is_default=is_default, body=body)

    def node_matchers(self, items: list[Any]) -> list[Any]:
        return items

    def node_matcher(self, items: list[Any]) -> PuppetNode:
        if not items:
            return UndefLiteral()
        item = items[0]
        if isinstance(item, Token):
            s = str(item)
            if s == "default":
                return UndefLiteral()
            if s.startswith("/"):
                return RegexLiteral(pattern=s[1:-1])
            return StringLiteral(value=s.strip("'\""))
        return item if isinstance(item, PuppetNode) else StringLiteral(value=str(item))

    # ── Variable Assignment ───────────────────────────────────────────────────────

    def variable_assignment(self, items: list[Any]) -> VariableAssignment:
        var   = items[0]
        value = items[1]
        return VariableAssignment(name=str(var)[1:], value=value, line=_tok_line(var))

    # ── Conditionals ─────────────────────────────────────────────────────────────

    def if_statement(self, items: list[Any]) -> IfStatement:
        condition = items[0]
        body: list[PuppetNode] = []
        elsif_clauses: list[ElsifClause] = []
        else_body: list[PuppetNode] = []

        for item in items[1:]:
            if isinstance(item, ElsifClause):
                elsif_clauses.append(item)
            elif isinstance(item, list) and item and item[0] == "__else__":
                else_body = item[1:]
            elif isinstance(item, PuppetNode):
                body.append(item)
            elif item is not None:
                body.append(item)

        return IfStatement(condition=condition, body=body, elsif_clauses=elsif_clauses, else_body=else_body)

    def elsif_clause(self, items: list[Any]) -> ElsifClause:
        condition = items[0]
        body = [s for s in items[1:] if s is not None]
        return ElsifClause(condition=condition, body=body)

    def else_clause(self, items: list[Any]) -> list[Any]:
        return ["__else__"] + [s for s in items if s is not None]

    def unless_statement(self, items: list[Any]) -> UnlessStatement:
        condition = items[0]
        body: list[PuppetNode] = []
        else_body: list[PuppetNode] = []
        for item in items[1:]:
            if isinstance(item, list) and item and item[0] == "__else__":
                else_body = item[1:]
            elif isinstance(item, PuppetNode):
                body.append(item)
        return UnlessStatement(condition=condition, body=body, else_body=else_body)

    def case_statement(self, items: list[Any]) -> CaseStatement:
        control = items[0]
        cases = [i for i in items[1:] if isinstance(i, CaseWhen)]
        return CaseStatement(control=control, cases=cases)

    def case_when(self, items: list[Any]) -> CaseWhen:
        matchers = items[0]
        body = [s for s in items[1:] if s is not None]
        is_default = any(isinstance(m, UndefLiteral) for m in matchers)
        return CaseWhen(matchers=matchers, body=body, is_default=is_default)

    def case_matchers(self, items: list[Any]) -> list[Any]:
        return items

    def case_matcher(self, items: list[Any]) -> PuppetNode:
        item = items[0]
        if isinstance(item, Token) and str(item) == "default":
            return UndefLiteral()
        return item if isinstance(item, PuppetNode) else StringLiteral(value=str(item))

    # ── Realize ───────────────────────────────────────────────────────────────────

    def realize_statement(self, items: list[Any]) -> RealizeStatement:
        refs = []
        for item in items:
            if isinstance(item, ResourceReference):
                refs.append(item)
            elif isinstance(item, list):
                refs.extend(r for r in item if isinstance(r, ResourceReference))
        return RealizeStatement(references=refs)

    def resource_ref_list(self, items: list[Any]) -> list[ResourceReference]:
        return [i for i in items if isinstance(i, ResourceReference)]

    # ── Import ────────────────────────────────────────────────────────────────────

    def import_statement(self, items: list[Any]) -> FunctionCall:
        path = str(items[0]).strip("'\"")
        return FunctionCall(name="__import__", arguments=[StringLiteral(value=path)])

    # ── Function call as statement ────────────────────────────────────────────────

    def function_call_stmt(self, items: list[Any]) -> FunctionCall:
        name = str(items[0])
        args: list[Any] = []
        block = None
        for item in items[1:]:
            if isinstance(item, LambdaBlock):
                block = item
            elif isinstance(item, list):
                args.extend(item)
            elif isinstance(item, PuppetNode):
                args.append(item)
        return FunctionCall(name=name, arguments=args, block=block)

    def method_call_stmt(self, items: list[Any]) -> MethodCall:
        receiver = items[0]
        method   = str(items[1])
        args: list[Any] = []
        block = None
        for item in items[2:]:
            if isinstance(item, LambdaBlock):
                block = item
            elif isinstance(item, list):
                args.extend(item)
            elif item is not None:
                args.append(item)
        return MethodCall(receiver=receiver, method=method, arguments=args, block=block)

    # ── Expressions ──────────────────────────────────────────────────────────────

    def variable_expr(self, items: list[Any]) -> Variable:
        tok = items[0]
        name = str(tok)[1:]  # strip $
        return Variable(name=name, line=_tok_line(tok))

    def resource_reference(self, items: list[Any]) -> ResourceReference:
        rtype = items[0]
        expr  = items[1]
        return ResourceReference(type_name=rtype, titles=[expr])

    def function_call(self, items: list[Any]) -> FunctionCall:
        name = str(items[0])
        args: list[Any] = []
        block = None
        for item in items[1:]:
            if isinstance(item, LambdaBlock):
                block = item
            elif isinstance(item, list):
                args.extend(item)
            elif isinstance(item, PuppetNode):
                args.append(item)
        return FunctionCall(name=name, arguments=args, block=block, line=_tok_line(items[0]))

    def argument_list(self, items: list[Any]) -> list[Any]:
        return items

    def lambda_block(self, items: list[Any]) -> LambdaBlock:
        params: list[str] = []
        body: list[Any] = []
        for item in items:
            if isinstance(item, list) and item and isinstance(item[0], str) and item[0] != "__else__":
                params = item
            elif isinstance(item, PuppetNode):
                body.append(item)
            elif item is not None:
                body.append(item)
        return LambdaBlock(parameters=params, body=[b for b in body if b is not None])

    def lambda_params(self, items: list[Any]) -> list[str]:
        result = []
        for item in items:
            if isinstance(item, ClassParameter):
                result.append(item.name)
        return result

    def lambda_param(self, items: list[Any]) -> ClassParameter:
        return self.class_parameter(items)

    # ── Binary ops ────────────────────────────────────────────────────────────────

    def bin_or(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="or", left=items[0], right=items[1])

    def bin_and(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="and", left=items[0], right=items[1])

    def bin_compare(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator=str(items[1]), left=items[0], right=items[2])

    def bin_in(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="in", left=items[0], right=items[1])

    def bin_add(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="+", left=items[0], right=items[1])

    def bin_sub(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="-", left=items[0], right=items[1])

    def bin_mul(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="*", left=items[0], right=items[1])

    def bin_div(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="/", left=items[0], right=items[1])

    def bin_mod(self, items: list[Any]) -> BinaryOp:
        return BinaryOp(operator="%", left=items[0], right=items[1])

    def unary_not(self, items: list[Any]) -> UnaryOp:
        return UnaryOp(operator="not", operand=items[0])

    def unary_neg(self, items: list[Any]) -> UnaryOp:
        return UnaryOp(operator="-", operand=items[0])

    # ── Postfix ops ───────────────────────────────────────────────────────────────

    def array_access(self, items: list[Any]) -> Any:
        # $facts['os']['family'] handling
        base = items[0]
        key  = items[1]
        if isinstance(base, Variable) and base.name == "facts":
            return FactAccess(keys=[key])
        if isinstance(base, FactAccess):
            base.keys.append(key)
            return base
        # General array access — represent as a special node
        return FunctionCall(name="__index__", arguments=[base, key])

    def method_call(self, items: list[Any]) -> MethodCall:
        receiver = items[0]
        method   = str(items[1])
        args: list[Any] = []
        block = None
        for item in items[2:]:
            if isinstance(item, LambdaBlock):
                block = item
            elif isinstance(item, list):
                args.extend(item)
            elif item is not None:
                args.append(item)
        return MethodCall(receiver=receiver, method=method, arguments=args, block=block)

    def method_call_noarg(self, items: list[Any]) -> MethodCall:
        receiver = items[0]
        method   = str(items[1])
        block    = items[2] if len(items) > 2 else None
        return MethodCall(receiver=receiver, method=method, arguments=[], block=block)

    def attr_access(self, items: list[Any]) -> MethodCall:
        return MethodCall(receiver=items[0], method=str(items[1]), arguments=[])

    # ── Selector ──────────────────────────────────────────────────────────────────

    def selector_expr(self, items: list[Any]) -> SelectorExpression:
        control = items[0]
        cases   = items[1] if len(items) > 1 else []
        return SelectorExpression(control=control, cases=cases)

    def selector_cases(self, items: list[Any]) -> list[tuple[Any, Any]]:
        return items

    def selector_case(self, items: list[Any]) -> tuple[Any, Any]:
        matcher = items[0]
        value   = items[1]
        if isinstance(matcher, Token) and str(matcher) == "default":
            return (UndefLiteral(), value)
        return (matcher, value)

    # ── Type ─────────────────────────────────────────────────────────────────────

    def type_cast(self, items: list[Any]) -> TypeCast:
        return TypeCast(type_name=str(items[0]), value=items[1])

    def type_expr(self, items: list[Any]) -> StringLiteral:
        name = str(items[0])
        if len(items) > 1 and isinstance(items[1], list):
            inner = ", ".join(str(p) for p in items[1])
            name = f"{name}[{inner}]"
        return StringLiteral(value=name)

    def type_params(self, items: list[Any]) -> list[Any]:
        return items

    def type_param(self, items: list[Any]) -> Any:
        return items[0] if items else None

    # ── Literals ─────────────────────────────────────────────────────────────────

    def literal(self, items: list[Any]) -> Any:
        return items[0]

    def dq_string(self, items: list[Any]) -> StringLiteral | StringInterpolation:
        return _parse_dq_string(str(items[0]))

    def sq_string(self, items: list[Any]) -> StringLiteral:
        return StringLiteral(value=str(items[0])[1:-1])

    def number(self, items: list[Any]) -> NumberLiteral:
        raw = str(items[0])
        val: int | float = float(raw) if "." in raw else int(raw)
        return NumberLiteral(value=val, line=_tok_line(items[0]))

    def bool_true(self, items: list[Any]) -> BoolLiteral:
        return BoolLiteral(value=True)

    def bool_false(self, items: list[Any]) -> BoolLiteral:
        return BoolLiteral(value=False)

    def undef_lit(self, items: list[Any]) -> UndefLiteral:
        return UndefLiteral()

    def regex_lit(self, items: list[Any]) -> RegexLiteral:
        raw = str(items[0])
        return RegexLiteral(pattern=raw[1:-1])

    def bareword(self, items: list[Any]) -> StringLiteral:
        return StringLiteral(value=str(items[0]))

    def type_ref(self, items: list[Any]) -> StringLiteral:
        """Puppet type reference used as an expression value (e.g. Hash, String, Enum['a','b']).
        The child is the already-transformed type_expr → StringLiteral."""
        return items[0] if items else StringLiteral(value="Unknown")

    def array_expr(self, items: list[Any]) -> ArrayLiteral:
        return ArrayLiteral(elements=list(items))

    def hash_expr(self, items: list[Any]) -> HashLiteral:
        return HashLiteral(pairs=list(items))

    def hash_pair(self, items: list[Any]) -> tuple[Any, Any]:
        return (items[0], items[1])
