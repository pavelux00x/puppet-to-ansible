"""Puppet DSL parser package."""
from src.parser.parser import ParseError, parse, parse_file

__all__ = ["parse", "parse_file", "ParseError"]
