"""Tests for Puppetfile → Ansible mapper."""
from pathlib import Path
import pytest
from puppet_to_ansible.puppetfile.parser import PuppetfileParser
from puppet_to_ansible.puppetfile.mapper import (
    PuppetfileMapper,
    STATUS_MAPPED, STATUS_BUILTIN, STATUS_CONVERTED,
    STATUS_MANUAL, STATUS_GIT, STATUS_UNKNOWN,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "input" / "Puppetfile"


def _analyze(content: str, converter_cols: set[str] | None = None):
    pf = PuppetfileParser().parse(content)
    return PuppetfileMapper().analyze(pf, converter_cols)


# ── Status mapping ────────────────────────────────────────────────────────────

def test_stdlib_is_builtin():
    r = _analyze("mod 'puppetlabs/stdlib', '9.4.1'")
    assert r.mappings[0].status == STATUS_BUILTIN
    assert r.mappings[0].galaxy_collection is None


def test_mysql_is_mapped():
    r = _analyze("mod 'puppetlabs/mysql', '15.0.0'")
    m = r.mappings[0]
    assert m.status == STATUS_MAPPED
    assert m.galaxy_collection == "community.mysql"


def test_apache_is_converted():
    r = _analyze("mod 'puppetlabs/apache', '11.1.0'")
    assert r.mappings[0].status == STATUS_CONVERTED


def test_git_module_status():
    r = _analyze("mod 'profile', git: 'https://git.example.com/profile.git', tag: 'v1.0'")
    assert r.mappings[0].status == STATUS_GIT
    assert r.mappings[0].galaxy_collection is None


def test_unknown_module():
    r = _analyze("mod 'acme/custom_thing', '1.0.0'")
    assert r.mappings[0].status == STATUS_UNKNOWN


def test_manual_module():
    r = _analyze("mod 'puppetlabs/dsc', '2.0.0'")
    assert r.mappings[0].status == STATUS_MANUAL


# ── required_collections ──────────────────────────────────────────────────────

def test_required_collections_from_mappings():
    content = """
mod 'puppetlabs/mysql', '15.0.0'
mod 'puppetlabs/postgresql', '10.1.0'
mod 'camptocamp/openssl', '2.1.0'
"""
    r = _analyze(content)
    assert "community.mysql" in r.required_collections
    assert "community.postgresql" in r.required_collections
    assert "community.crypto" in r.required_collections


def test_required_collections_merged_with_converter():
    r = _analyze("mod 'puppetlabs/stdlib'", converter_cols={"ansible.posix"})
    assert "ansible.posix" in r.required_collections


def test_builtin_module_adds_no_collection():
    r = _analyze("mod 'puppetlabs/concat', '9.0.2'")
    assert r.required_collections == set()


# ── Summary counts ────────────────────────────────────────────────────────────

def test_covered_total():
    content = """
mod 'puppetlabs/stdlib', '9.4.1'
mod 'puppetlabs/mysql', '15.0.0'
mod 'puppetlabs/apache', '11.1.0'
mod 'acme/custom_thing', '1.0.0'
"""
    r = _analyze(content)
    assert r.forge_total == 4
    assert r.covered_total == 3   # stdlib + mysql + apache


def test_manual_modules_list():
    r = _analyze("mod 'puppetlabs/dsc', '2.0.0'")
    assert len(r.manual_modules) == 1


def test_unknown_modules_list():
    r = _analyze("mod 'acme/custom_thing', '1.0.0'")
    assert len(r.unknown_modules) == 1


# ── Full fixture analysis ─────────────────────────────────────────────────────

def test_full_fixture_analysis():
    pf = PuppetfileParser().parse_file(FIXTURE)
    r = PuppetfileMapper().analyze(pf)

    assert r.forge_total >= 14
    assert r.covered_total >= 12
    assert len(r.git_modules) == 2
    assert len(r.unknown_modules) == 1   # acme/custom_thing
    assert "community.mysql" in r.required_collections
    assert "community.postgresql" in r.required_collections
    assert "kubernetes.core" in r.required_collections
    assert "community.docker" in r.required_collections
    assert "community.crypto" in r.required_collections
