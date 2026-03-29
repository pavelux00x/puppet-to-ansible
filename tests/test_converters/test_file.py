"""Tests for the file resource converter."""
from __future__ import annotations

from tests.conftest import convert_snippet


class TestFileConverter:
    def test_ensure_directory(self):
        r = convert_snippet("file { '/etc/myapp': ensure => directory }")
        t = r.tasks[0]
        assert "ansible.builtin.file" in t
        assert t["ansible.builtin.file"]["state"] == "directory"

    def test_ensure_link(self):
        r = convert_snippet("file { '/etc/link': ensure => link, target => '/etc/real' }")
        t = r.tasks[0]
        assert "ansible.builtin.file" in t
        assert t["ansible.builtin.file"]["state"] == "link"

    def test_ensure_absent(self):
        r = convert_snippet("file { '/tmp/old': ensure => absent }")
        t = r.tasks[0]
        assert "ansible.builtin.file" in t
        assert t["ansible.builtin.file"]["state"] == "absent"

    def test_content_uses_copy(self):
        r = convert_snippet("file { '/etc/msg': content => 'hello world' }")
        t = r.tasks[0]
        assert "ansible.builtin.copy" in t
        assert t["ansible.builtin.copy"]["content"] == "hello world"

    def test_owner_group_mode(self):
        src = "file { '/etc/app.conf': ensure => file, owner => 'app', group => 'app', mode => '0640' }"
        r = convert_snippet(src)
        t = r.tasks[0]
        # Copy or file task
        mod = t.get("ansible.builtin.copy") or t.get("ansible.builtin.file") or {}
        assert mod.get("owner") == "app"
        assert mod.get("group") == "app"
        assert mod.get("mode") == "0640"

    def test_notify_generates_handler(self):
        """notify => Service['nginx'] should add 'notify:' to the task and a handler."""
        src = """
        file { '/etc/nginx.conf':
          content => 'x',
          notify  => Service['nginx'],
        }
        service { 'nginx':
          ensure => running,
          enable => true,
        }
        """
        r = convert_snippet(src)
        # The file task should reference the handler
        file_task = next(t for t in r.tasks if "nginx.conf" in str(t))
        assert "notify" in file_task
        # The service converter should register a handler
        assert len(r.handlers) >= 1
        handler_names = [h["name"] for h in r.handlers]
        assert any("nginx" in n.lower() for n in handler_names)

    def test_task_has_name(self):
        r = convert_snippet("file { '/etc/app.conf': ensure => file }")
        assert "name" in r.tasks[0]
        assert len(r.tasks[0]["name"]) > 0
