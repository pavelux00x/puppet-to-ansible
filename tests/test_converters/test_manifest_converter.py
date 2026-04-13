"""Tests for ManifestConverter logic — conditions, fail(), and other statement-level behaviour."""
from __future__ import annotations

import pytest
from tests.conftest import convert_snippet


class TestConditionSimplification:
    """M2 — not (a == b) → a != b / not (a != b) → a == b."""

    def test_not_equal_simplifies(self):
        """not ($x == 'foo') → x != 'foo'."""
        src = """
        unless $operatingsystem == 'Ubuntu' {
          package { 'build-essential': ensure => installed }
        }
        """
        r = convert_snippet(src)
        assert len(r.tasks) == 1
        when = r.tasks[0].get("when", "")
        # Should NOT contain 'not (' — should be simplified to !=
        assert "not (" not in when
        assert "!=" in when

    def test_not_not_equal_simplifies(self):
        """unless ($x != 'foo') — the condition is negated, so it becomes $x == 'foo'."""
        src = """
        unless $os != 'Debian' {
          package { 'curl': ensure => installed }
        }
        """
        r = convert_snippet(src)
        assert len(r.tasks) == 1
        when = r.tasks[0].get("when", "")
        assert "not (" not in when
        assert "==" in when

    def test_unary_not_on_bool_var_untouched(self):
        """not $flag (not a BinaryOp) stays as 'not (flag)'."""
        src = """
        if !$ssl {
          package { 'openssl': ensure => installed }
        }
        """
        r = convert_snippet(src)
        assert len(r.tasks) == 1
        when = r.tasks[0].get("when", "")
        assert "not" in when


class TestFailConversion:
    """M3 — fail() → ansible.builtin.fail with preserved when:."""

    def test_fail_produces_task(self):
        """fail('msg') at top level → ansible.builtin.fail task."""
        src = "fail('Unsupported OS')"
        r = convert_snippet(src)
        assert len(r.tasks) == 1
        t = r.tasks[0]
        assert "ansible.builtin.fail" in t
        assert "Unsupported OS" in t["ansible.builtin.fail"]["msg"]

    def test_fail_inside_if_preserves_when(self):
        """fail() inside if block → fail task inherits when: condition."""
        src = """
        if $osfamily == 'Windows' {
          fail('Windows is not supported')
        }
        """
        r = convert_snippet(src)
        fail_tasks = [t for t in r.tasks if "ansible.builtin.fail" in t]
        assert len(fail_tasks) == 1
        assert "when" in fail_tasks[0]
        assert "Windows" in fail_tasks[0]["ansible.builtin.fail"]["msg"]

    def test_fail_no_args_produces_generic_message(self):
        """fail() without arguments → generic message, no crash."""
        src = "fail()"
        r = convert_snippet(src)
        assert any("ansible.builtin.fail" in t for t in r.tasks)
