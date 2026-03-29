"""Tests for the service resource converter."""
from __future__ import annotations

from tests.conftest import convert_snippet


class TestServiceConverter:
    def test_ensure_running(self):
        r = convert_snippet("service { 'nginx': ensure => running }")
        t = r.tasks[0]
        mod = t.get("ansible.builtin.service") or t.get("ansible.builtin.systemd")
        assert mod["state"] == "started"

    def test_ensure_stopped(self):
        r = convert_snippet("service { 'nginx': ensure => stopped }")
        t = r.tasks[0]
        mod = t.get("ansible.builtin.service") or t.get("ansible.builtin.systemd")
        assert mod["state"] == "stopped"

    def test_enable_true(self):
        r = convert_snippet("service { 'nginx': ensure => running, enable => true }")
        t = r.tasks[0]
        mod = t.get("ansible.builtin.service") or t.get("ansible.builtin.systemd")
        assert mod.get("enabled") is True or mod.get("enabled") == "true"

    def test_enable_false(self):
        r = convert_snippet("service { 'nginx': ensure => running, enable => false }")
        t = r.tasks[0]
        mod = t.get("ansible.builtin.service") or t.get("ansible.builtin.systemd")
        assert mod.get("enabled") is False or mod.get("enabled") == "false"

    def test_provider_systemd(self):
        r = convert_snippet("service { 'nginx': ensure => running, provider => systemd }")
        t = r.tasks[0]
        assert "ansible.builtin.systemd" in t

    def test_task_name_includes_service_name(self):
        r = convert_snippet("service { 'nginx': ensure => running }")
        assert "nginx" in r.tasks[0]["name"].lower()

    def test_fqcn(self):
        r = convert_snippet("service { 'nginx': ensure => running }")
        keys = [k for k in r.tasks[0] if k != "name"]
        assert any(k.startswith("ansible.") for k in keys)
