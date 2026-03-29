"""Shared pytest fixtures for p2a test suite."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.converters.manifest_converter import ManifestConverter
from src.generators.playbook import PlaybookGenerator, RoleGenerator
from src.parser.parser import _parsers, parse, parse_file

FIXTURES_DIR = Path(__file__).parent / "fixtures"
INPUT_DIR    = FIXTURES_DIR / "input"
EXPECTED_DIR = FIXTURES_DIR / "expected"


@pytest.fixture(autouse=True)
def clear_parser_cache():
    """Clear the lark parser cache before each test (grammar may vary)."""
    _parsers.clear()
    yield
    _parsers.clear()


@pytest.fixture
def converter():
    return ManifestConverter()


@pytest.fixture
def playbook_gen():
    return PlaybookGenerator()


@pytest.fixture
def role_gen():
    return RoleGenerator()


def convert_snippet(puppet_src: str, puppet_version: int = 4):
    """Parse + convert a Puppet source snippet. Returns ConversionResult."""
    manifest = parse(puppet_src, puppet_version=puppet_version)
    return ManifestConverter(puppet_version=puppet_version).convert(manifest)


def convert_fixture(fixture_name: str, puppet_version: int = 4):
    """Load a fixture file by name and convert it. Returns ConversionResult."""
    path = INPUT_DIR / fixture_name
    manifest = parse_file(path, puppet_version=puppet_version)
    return ManifestConverter(puppet_version=puppet_version).convert(manifest)
