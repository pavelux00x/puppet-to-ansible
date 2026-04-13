"""mysql::db → community.mysql.mysql_db."""
from __future__ import annotations

from typing import Any

from puppet_to_ansible.converters.base import BaseConverter, ConversionContext
from puppet_to_ansible.parser.ast_nodes import ResourceBody


_ENSURE_MAP = {
    "present": "present",
    "absent":  "absent",
    "dump":    "dump",
    "import":  "import",
}


class MysqlDbConverter(BaseConverter):
    """Converts Puppet `mysql::db` defined-type resources
    (puppetlabs/mysql module) to Ansible community.mysql.mysql_db tasks.

    Mapping:
        mysql::db { 'myapp':
          user     => 'myapp',
          password => 'secret',
          host     => 'localhost',
          grant    => ['SELECT', 'UPDATE'],
          charset  => 'utf8mb4',
          collate  => 'utf8mb4_unicode_ci',
        }
        → community.mysql.mysql_db: name: myapp, encoding: utf8mb4,
                                    collation: utf8mb4_unicode_ci, state: present
        + community.mysql.mysql_user for the user/grant (when user is specified)
    """

    puppet_type = "mysql::db"

    def convert(
        self,
        resource_type: str,
        body: ResourceBody,
        context: ConversionContext,
    ) -> list[dict[str, Any]]:
        context.require_collection("community.mysql")

        title  = str(self.resolve_title(body, context))
        notify = self.get_notify(body, context)
        when   = self.get_when(body, context)

        ensure_node  = body.get_attr("ensure")
        charset_node = body.get_attr("charset")
        collate_node = body.get_attr("collate") or body.get_attr("collation")
        user_node    = body.get_attr("user")
        password_node = body.get_attr("password")
        host_node    = body.get_attr("host")
        grant_node   = body.get_attr("grant")
        dbfile_node  = body.get_attr("sql")

        ensure_raw = self.resolve(ensure_node, context) if ensure_node else "present"
        state      = _ENSURE_MAP.get(str(ensure_raw).lower(), "present")

        db_params: dict[str, Any] = {"name": title, "state": state}

        if charset_node is not None:
            db_params["encoding"] = str(self.resolve(charset_node, context))

        if collate_node is not None:
            db_params["collation"] = str(self.resolve(collate_node, context))

        if dbfile_node is not None:
            # sql => '/path/to/dump.sql' used for import/dump states
            db_params["target"] = str(self.resolve(dbfile_node, context))

        tasks: list[dict[str, Any]] = [self.make_task(
            name=f"Create MySQL database {title}",
            module="community.mysql.mysql_db",
            params=db_params,
            notify=notify or None,
            when=when,
        )]

        # If a db user is declared alongside the database, emit a mysql_user task too.
        if user_node is not None and state == "present":
            db_user = str(self.resolve(user_node, context))
            db_host = str(self.resolve(host_node, context)) if host_node else "localhost"
            db_pass = str(self.resolve(password_node, context)) if password_node else "{{ omit }}"

            # grants: Puppet uses ['SELECT', 'UPDATE'] → 'SELECT,UPDATE' per priv entry
            privs: list[str] = []
            if grant_node is not None:
                raw_grants = self.resolve(grant_node, context)
                if isinstance(raw_grants, list):
                    privs = [str(g) for g in raw_grants]
                else:
                    privs = [str(raw_grants)]
            priv_str = f"{title}.*:" + ",".join(privs) if privs else f"{title}.*:ALL"

            user_params: dict[str, Any] = {
                "name": db_user,
                "host": db_host,
                "password": db_pass,
                "priv": priv_str,
                "state": "present",
            }

            tasks.append(self.make_task(
                name=f"Grant MySQL user {db_user} on {title}",
                module="community.mysql.mysql_user",
                params=user_params,
                when=when,
            ))

        return tasks
