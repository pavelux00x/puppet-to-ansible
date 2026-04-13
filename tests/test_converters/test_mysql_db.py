"""Tests for the mysql::db resource converter."""
from __future__ import annotations

from tests.conftest import convert_snippet


class TestMysqlDbConverter:
    def test_basic_database(self):
        r = convert_snippet("mysql::db { 'myapp': }")
        assert len(r.tasks) == 1
        t = r.tasks[0]
        s = t["community.mysql.mysql_db"]
        assert s["name"] == "myapp"
        assert s["state"] == "present"

    def test_charset_and_collate(self):
        r = convert_snippet(
            "mysql::db { 'myapp': charset => 'utf8mb4', collate => 'utf8mb4_unicode_ci' }"
        )
        s = r.tasks[0]["community.mysql.mysql_db"]
        assert s["encoding"] == "utf8mb4"
        assert s["collation"] == "utf8mb4_unicode_ci"

    def test_ensure_absent(self):
        r = convert_snippet("mysql::db { 'myapp': ensure => absent }")
        s = r.tasks[0]["community.mysql.mysql_db"]
        assert s["state"] == "absent"

    def test_user_generates_second_task(self):
        r = convert_snippet(
            "mysql::db { 'myapp': user => 'appuser', password => 'secret', host => 'localhost' }"
        )
        assert len(r.tasks) == 2
        user_task = r.tasks[1]
        u = user_task["community.mysql.mysql_user"]
        assert u["name"] == "appuser"
        assert u["host"] == "localhost"
        assert "myapp.*" in u["priv"]

    def test_user_absent_no_user_task(self):
        """When ensure => absent, no mysql_user task should be emitted."""
        r = convert_snippet(
            "mysql::db { 'myapp': ensure => absent, user => 'appuser' }"
        )
        assert len(r.tasks) == 1
        assert "community.mysql.mysql_db" in r.tasks[0]

    def test_grant_list(self):
        r = convert_snippet(
            "mysql::db { 'myapp': user => 'appuser', grant => ['SELECT', 'INSERT'] }"
        )
        u = r.tasks[1]["community.mysql.mysql_user"]
        assert "SELECT" in u["priv"]
        assert "INSERT" in u["priv"]

    def test_requires_community_mysql_collection(self):
        r = convert_snippet("mysql::db { 'myapp': }")
        assert "community.mysql" in r.collections

    def test_task_name_includes_db_name(self):
        r = convert_snippet("mysql::db { 'myapp': }")
        assert "myapp" in r.tasks[0]["name"].lower()

    def test_fqcn(self):
        r = convert_snippet("mysql::db { 'myapp': }")
        keys = [k for k in r.tasks[0] if k != "name"]
        assert any(k.startswith("community.mysql") for k in keys)
