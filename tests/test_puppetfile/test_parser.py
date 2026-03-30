"""Tests for Puppetfile parser."""
from pathlib import Path
import pytest
from src.puppetfile.parser import PuppetfileParser, PuppetModule

FIXTURE = Path(__file__).parent.parent / "fixtures" / "input" / "Puppetfile"


# ── parse_file ────────────────────────────────────────────────────────────────

def test_parse_fixture_file():
    pf = PuppetfileParser().parse_file(FIXTURE)
    assert pf.forge_url == "https://forgeapi.puppet.com"
    assert len(pf.modules) == 16


def test_forge_modules_count():
    pf = PuppetfileParser().parse_file(FIXTURE)
    assert len(pf.forge_modules) == 14


def test_git_modules_count():
    pf = PuppetfileParser().parse_file(FIXTURE)
    assert len(pf.git_modules) == 2


# ── Forge module fields ───────────────────────────────────────────────────────

def test_forge_module_with_version():
    pf = PuppetfileParser().parse("mod 'puppetlabs/apache', '11.1.0'")
    m = pf.modules[0]
    assert m.author == "puppetlabs"
    assert m.name == "apache"
    assert m.version == "11.1.0"
    assert m.source == "forge"


def test_forge_module_no_version():
    pf = PuppetfileParser().parse("mod 'puppetlabs/ntp'")
    m = pf.modules[0]
    assert m.version is None
    assert m.source == "forge"


def test_forge_module_version_constraint():
    pf = PuppetfileParser().parse("mod 'puppetlabs/stdlib', '>= 9.0.0 < 10.0.0'")
    m = pf.modules[0]
    assert m.version == ">= 9.0.0 < 10.0.0"


def test_standalone_module_no_author():
    pf = PuppetfileParser().parse("mod 'profile', git: 'https://git.example.com/profile.git'")
    m = pf.modules[0]
    assert m.author == ""
    assert m.name == "profile"
    assert m.full_name == "profile"


# ── Git module fields ─────────────────────────────────────────────────────────

def test_git_module_modern_syntax():
    content = """
mod 'profile',
  git: 'https://git.example.com/profile.git',
  tag: 'v3.2.1'
"""
    pf = PuppetfileParser().parse(content)
    m = pf.modules[0]
    assert m.source == "git"
    assert m.git_url == "https://git.example.com/profile.git"
    assert m.git_ref == "v3.2.1"
    assert m.git_ref_type == "tag"


def test_git_module_hash_rocket_syntax():
    content = """
mod 'role',
  :git => 'https://git.example.com/role.git',
  :branch => 'main'
"""
    pf = PuppetfileParser().parse(content)
    m = pf.modules[0]
    assert m.source == "git"
    assert m.git_url == "https://git.example.com/role.git"
    assert m.git_ref == "main"
    assert m.git_ref_type == "branch"


def test_git_module_with_commit():
    content = "mod 'mymod', git: 'https://git.example.com/mymod.git', commit: 'abc1234'"
    pf = PuppetfileParser().parse(content)
    m = pf.modules[0]
    assert m.git_ref == "abc1234"
    assert m.git_ref_type == "commit"


# ── Comments and edge cases ───────────────────────────────────────────────────

def test_inline_comments_stripped():
    content = "mod 'puppetlabs/apache', '11.1.0'  # main web server"
    pf = PuppetfileParser().parse(content)
    assert len(pf.modules) == 1
    assert pf.modules[0].version == "11.1.0"


def test_comment_lines_ignored():
    content = """
# This is a comment
forge 'https://forgeapi.puppet.com'
# Another comment
mod 'puppetlabs/stdlib', '9.4.1'
"""
    pf = PuppetfileParser().parse(content)
    assert len(pf.modules) == 1


def test_forge_url_parsed():
    pf = PuppetfileParser().parse("forge 'https://internal-forge.example.com'")
    assert pf.forge_url == "https://internal-forge.example.com"


def test_full_name_forge():
    pf = PuppetfileParser().parse("mod 'puppetlabs/mysql', '15.0.0'")
    assert pf.modules[0].full_name == "puppetlabs/mysql"
