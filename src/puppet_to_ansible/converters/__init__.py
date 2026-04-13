"""Converter package — import all converters so auto_discover() can find them."""
# noqa: F401
from puppet_to_ansible.converters import (
    apt,
    augeas,
    cron,
    exec_res,
    file_res,
    firewall,
    group,
    host,
    ini_setting,
    mount,
    mysql_db,
    package,
    selboolean,
    service,
    ssh_authorized_key,
    sysctl,
    user,
    yumrepo,
)
