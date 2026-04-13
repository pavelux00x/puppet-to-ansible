"""Tests for the sysctl resource converter."""
from __future__ import annotations

from tests.conftest import convert_snippet


class TestSysctlConverter:
    def test_basic_val(self):
        r = convert_snippet("sysctl { 'net.ipv4.ip_forward': val => '1' }")
        assert len(r.tasks) == 1
        t = r.tasks[0]
        s = t["ansible.posix.sysctl"]
        assert s["name"] == "net.ipv4.ip_forward"
        assert s["value"] == "1"
        assert s["state"] == "present"

    def test_persist_true_by_default(self):
        r = convert_snippet("sysctl { 'vm.swappiness': val => '10' }")
        s = r.tasks[0]["ansible.posix.sysctl"]
        assert s["sysctl_set"] is True

    def test_persist_false(self):
        r = convert_snippet("sysctl { 'vm.swappiness': val => '10', persist => false }")
        s = r.tasks[0]["ansible.posix.sysctl"]
        assert s["sysctl_set"] is False

    def test_ensure_absent(self):
        r = convert_snippet("sysctl { 'net.ipv4.ip_forward': ensure => absent }")
        s = r.tasks[0]["ansible.posix.sysctl"]
        assert s["state"] == "absent"

    def test_fqcn(self):
        r = convert_snippet("sysctl { 'net.ipv4.ip_forward': val => '1' }")
        keys = [k for k in r.tasks[0] if k != "name"]
        assert any(k == "ansible.posix.sysctl" for k in keys)

    def test_requires_ansible_posix_collection(self):
        r = convert_snippet("sysctl { 'net.ipv4.ip_forward': val => '1' }")
        assert "ansible.posix" in r.collections

    def test_task_name_includes_key(self):
        r = convert_snippet("sysctl { 'net.ipv4.ip_forward': val => '1' }")
        assert "net.ipv4.ip_forward" in r.tasks[0]["name"]

    def test_reload_true_by_default(self):
        r = convert_snippet("sysctl { 'kernel.shmmax': val => '268435456' }")
        s = r.tasks[0]["ansible.posix.sysctl"]
        assert s["reload"] is True
