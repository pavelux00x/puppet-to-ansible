"""Tests for the package resource converter."""
from __future__ import annotations

import pytest
from tests.conftest import convert_snippet


class TestPackageConverter:
    def test_ensure_installed(self):
        r = convert_snippet("package { 'nginx': ensure => installed }")
        assert len(r.tasks) == 1
        t = r.tasks[0]
        assert t["ansible.builtin.package"]["name"] == "nginx"
        assert t["ansible.builtin.package"]["state"] == "present"

    def test_ensure_present(self):
        r = convert_snippet("package { 'nginx': ensure => present }")
        assert r.tasks[0]["ansible.builtin.package"]["state"] == "present"

    def test_ensure_latest(self):
        r = convert_snippet("package { 'nginx': ensure => latest }")
        assert r.tasks[0]["ansible.builtin.package"]["state"] == "latest"

    def test_ensure_absent(self):
        r = convert_snippet("package { 'nginx': ensure => absent }")
        assert r.tasks[0]["ansible.builtin.package"]["state"] == "absent"

    def test_ensure_purged(self):
        r = convert_snippet("package { 'nginx': ensure => purged }")
        assert r.tasks[0]["ansible.builtin.package"]["state"] == "absent"

    def test_ensure_version_string(self):
        r = convert_snippet("package { 'nginx': ensure => '1.24.0' }")
        pkg = r.tasks[0]["ansible.builtin.package"]
        assert pkg["name"] == "nginx=1.24.0" or pkg.get("name") == "nginx"

    def test_provider_pip(self):
        r = convert_snippet("package { 'flask': ensure => installed, provider => pip }")
        t = r.tasks[0]
        assert "ansible.builtin.pip" in t
        assert t["ansible.builtin.pip"]["name"] == "flask"

    def test_provider_apt(self):
        r = convert_snippet("package { 'nginx': ensure => installed, provider => apt }")
        t = r.tasks[0]
        assert "ansible.builtin.apt" in t

    def test_provider_yum(self):
        r = convert_snippet("package { 'httpd': ensure => installed, provider => yum }")
        t = r.tasks[0]
        assert "ansible.builtin.yum" in t

    def test_task_name_includes_package_name(self):
        r = convert_snippet("package { 'nginx': ensure => installed }")
        assert "nginx" in r.tasks[0]["name"].lower()

    def test_multiple_packages_semicolon(self):
        src = "package { 'nginx': ensure => installed; 'curl': ensure => present }"
        r = convert_snippet(src)
        assert len(r.tasks) == 2

    def test_fqcn_module_name(self):
        r = convert_snippet("package { 'nginx': ensure => installed }")
        t = r.tasks[0]
        keys = [k for k in t if k != "name"]
        assert any(k.startswith("ansible.") for k in keys), f"No FQCN in {t}"

    # M1 — array title → single list task
    def test_array_title_emits_single_task(self):
        """M1: package { ['a', 'b', 'c']: ensure => installed } → one task with name list."""
        src = "package { ['mysql-server', 'mysql-client', 'python3-mysqldb']: ensure => installed }"
        r = convert_snippet(src)
        assert len(r.tasks) == 1
        pkg = r.tasks[0]["ansible.builtin.package"]
        assert isinstance(pkg["name"], list)
        assert pkg["name"] == ["mysql-server", "mysql-client", "python3-mysqldb"]
        assert pkg["state"] == "present"

    def test_array_title_version_pinned_stays_individual(self):
        """When version is pinned, array titles stay as individual tasks."""
        src = "package { ['nginx', 'curl']: ensure => '1.24.0' }"
        r = convert_snippet(src)
        assert len(r.tasks) == 2

    def test_array_title_pip_stays_individual(self):
        """pip provider with array title emits individual tasks (complex extras)."""
        src = "package { ['flask', 'requests']: ensure => installed, provider => pip }"
        r = convert_snippet(src)
        assert len(r.tasks) == 2
