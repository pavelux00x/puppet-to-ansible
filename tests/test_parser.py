"""Parser unit tests — grammar correctness and AST shape."""
from __future__ import annotations

import pytest

from src.parser.ast_nodes import (
    ClassDeclaration,
    ClassDefinition,
    DefinedTypeDefinition,
    FunctionCall,
    IfStatement,
    NodeDefinition,
    ResourceDeclaration,
    UnlessStatement,
    VariableAssignment,
)
from src.parser.parser import ParseError, parse


# ── Helpers ───────────────────────────────────────────────────────────────────

def stmts(src: str):
    return parse(src).statements


def first(src: str):
    return stmts(src)[0]


# ── Resource declarations ─────────────────────────────────────────────────────

class TestResourceDeclaration:
    def test_simple_package(self):
        r = first("package { 'nginx': ensure => installed }")
        assert isinstance(r, ResourceDeclaration)
        assert r.type_name == "package"

    def test_multiple_resources(self):
        src = "package { 'nginx': ensure => installed; 'curl': ensure => present }"
        r = first(src)
        assert len(r.bodies) == 2

    def test_ensure_string(self):
        r = first("package { 'nginx': ensure => '2.4.6' }")
        assert isinstance(r, ResourceDeclaration)

    def test_file_resource(self):
        src = "file { '/etc/nginx.conf': ensure => file, owner => 'root', mode => '0644' }"
        r = first(src)
        assert r.type_name == "file"
        attrs = {a.name: a for a in r.bodies[0].attributes}
        assert "ensure" in attrs
        assert "owner" in attrs

    def test_virtual_resource(self):
        r = first("@package { 'nginx': ensure => installed }")
        assert isinstance(r, ResourceDeclaration)
        from src.parser.ast_nodes import ResourceVirtuality
        assert r.virtuality == ResourceVirtuality.VIRTUAL

    def test_exported_resource(self):
        r = first("@@file { '/etc/ssh/known_hosts': content => 'x' }")
        from src.parser.ast_nodes import ResourceVirtuality
        assert r.virtuality == ResourceVirtuality.EXPORTED

    def test_multi_title_array(self):
        src = "file { ['/tmp/a', '/tmp/b']: ensure => directory }"
        r = first(src)
        assert isinstance(r, ResourceDeclaration)


# ── Variable assignments ──────────────────────────────────────────────────────

class TestVariableAssignment:
    def test_simple(self):
        v = first("$foo = 'bar'")
        assert isinstance(v, VariableAssignment)
        assert v.name == "foo"

    def test_array(self):
        v = first("$arr = ['a', 'b', 'c']")
        assert isinstance(v, VariableAssignment)

    def test_hash(self):
        v = first("$h = { 'key' => 'val' }")
        assert isinstance(v, VariableAssignment)

    def test_fact_access(self):
        v = first("$os = $facts['os']['family']")
        from src.parser.ast_nodes import FactAccess
        assert isinstance(v, VariableAssignment)

    def test_hiera_lookup(self):
        v = first("$port = lookup('mymodule::port', Integer, 'first', 80)")
        assert isinstance(v, VariableAssignment)

    def test_selector(self):
        src = "$pkg = $facts['os'] ? { 'Debian' => 'apache2', default => 'httpd' }"
        v = first(src)
        assert isinstance(v, VariableAssignment)


# ── Conditionals ──────────────────────────────────────────────────────────────

class TestConditionals:
    def test_if_simple(self):
        src = "if $x { package { 'a': ensure => installed } }"
        s = first(src)
        assert isinstance(s, IfStatement)

    def test_if_elsif_else(self):
        src = """
        if $x { package { 'a': ensure => installed } }
        elsif $y { package { 'b': ensure => installed } }
        else { package { 'c': ensure => installed } }
        """
        s = first(src)
        assert isinstance(s, IfStatement)
        assert len(s.elsif_clauses) == 1
        assert s.else_body is not None

    def test_unless(self):
        src = "unless $x { package { 'a': ensure => installed } }"
        s = first(src)
        assert isinstance(s, UnlessStatement)

    def test_case(self):
        src = """
        case $::osfamily {
          'Debian': { package { 'apache2': ensure => installed } }
          'RedHat': { package { 'httpd': ensure => installed } }
          default:  { }
        }
        """
        from src.parser.ast_nodes import CaseStatement
        s = first(src)
        assert isinstance(s, CaseStatement)


# ── Class definitions ─────────────────────────────────────────────────────────

class TestClassDefinitions:
    def test_basic_class(self):
        src = "class mymodule { package { 'nginx': ensure => installed } }"
        s = first(src)
        assert isinstance(s, ClassDefinition)
        assert s.name == "mymodule"

    def test_parameterized_class(self):
        src = """
        class mymodule (
          String $port = '80',
          Boolean $ssl = false,
        ) { }
        """
        s = first(src)
        assert isinstance(s, ClassDefinition)
        assert len(s.parameters) == 2

    def test_class_with_inherits(self):
        src = "class mymodule::child inherits mymodule { }"
        s = first(src)
        assert isinstance(s, ClassDefinition)
        assert s.parent == "mymodule"

    def test_class_declaration(self):
        src = "class { 'nginx': ensure => present }"
        s = first(src)
        assert isinstance(s, ClassDeclaration)

    def test_include(self):
        src = "include nginx"
        s = first(src)
        # include_statement transforms to a list of ClassDeclaration nodes
        assert isinstance(s, (ClassDeclaration, list))


# ── Defined types ─────────────────────────────────────────────────────────────

class TestDefinedTypes:
    def test_basic_define(self):
        src = """
        define webapp::vhost (
          String $docroot,
          Integer $port = 80,
        ) {
          file { "/etc/nginx/sites-available/${name}":
            ensure => file,
          }
        }
        """
        s = first(src)
        assert isinstance(s, DefinedTypeDefinition)
        assert s.name == "webapp::vhost"
        assert len(s.parameters) == 2


# ── Node definitions ──────────────────────────────────────────────────────────

class TestNodeDefinitions:
    def test_simple_node(self):
        src = "node 'web01.example.com' { include role::webserver }"
        s = first(src)
        assert isinstance(s, NodeDefinition)

    def test_default_node(self):
        src = "node default { include base }"
        s = first(src)
        assert isinstance(s, NodeDefinition)

    def test_regex_node(self):
        src = r"node /^web\d+/ { include role::webserver }"
        s = first(src)
        assert isinstance(s, NodeDefinition)


# ── Puppet 4 features ─────────────────────────────────────────────────────────

class TestPuppet4Features:
    def test_typed_params(self):
        src = """
        class webstack (
          String[1]              $app_name,
          Array[String[1], 1]    $packages,
          Hash[String, Integer]  $ports,
          Enum['a', 'b']         $choice = 'a',
        ) { }
        """
        s = first(src)
        assert isinstance(s, ClassDefinition)
        assert len(s.parameters) == 4

    def test_lambda(self):
        src = """
        $packages.each |String $pkg| {
          package { $pkg: ensure => installed }
        }
        """
        s = first(src)
        from src.parser.ast_nodes import MethodCall
        assert isinstance(s, MethodCall)

    def test_lookup_with_types(self):
        src = "$x = lookup('mymodule::db', Hash, 'hash', {})"
        s = first(src)
        assert isinstance(s, VariableAssignment)

    def test_selector_on_fact_access(self):
        src = "$x = $facts['virtual'] ? { 'physical' => 'a', default => 'b' }"
        s = first(src)
        assert isinstance(s, VariableAssignment)


# ── Error handling ────────────────────────────────────────────────────────────

class TestParseErrors:
    def test_invalid_syntax(self):
        with pytest.raises(ParseError):
            parse("this is not valid puppet {{ }")

    def test_unclosed_brace(self):
        with pytest.raises(ParseError):
            parse("package { 'nginx': ensure => installed")

    def test_error_has_line_info(self):
        try:
            parse("package { 'nginx':\n  ensure => INVALID!!BAD\n}")
        except ParseError as e:
            assert e.line > 0
