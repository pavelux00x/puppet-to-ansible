"""Puppet DSL parser package."""
from puppet_to_ansible.parser.parser import ParseError, parse, parse_file

__all__ = ["parse", "parse_file", "ParseError"]
