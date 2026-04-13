"""Map Puppet Facter facts to Ansible facts."""

from __future__ import annotations

# Comprehensive mapping of Facter facts → Ansible facts
FACT_MAP: dict[str, str] = {
    # Operating System
    "operatingsystem": "ansible_distribution",
    "operatingsystemmajrelease": "ansible_distribution_major_version",
    "operatingsystemrelease": "ansible_distribution_version",
    "osfamily": "ansible_os_family",
    "lsbdistcodename": "ansible_distribution_release",
    "lsbdistid": "ansible_distribution",
    "lsbdistrelease": "ansible_distribution_version",
    "lsbmajdistrelease": "ansible_distribution_major_version",
    # Hardware
    "architecture": "ansible_architecture",
    "processorcount": "ansible_processor_vcpus",
    "processor0": "ansible_processor[1]",
    "memorysize_mb": "ansible_memtotal_mb",
    "memorysize": "ansible_memtotal_mb",
    "memoryfree_mb": "ansible_memfree_mb",
    "swapsize_mb": "ansible_swaptotal_mb",
    "swapfree_mb": "ansible_swapfree_mb",
    "blockdevices": "ansible_devices.keys() | list",
    # Network
    "fqdn": "ansible_fqdn",
    "hostname": "ansible_hostname",
    "domain": "ansible_domain",
    "ipaddress": "ansible_default_ipv4.address",
    "ipaddress_lo": "ansible_lo.ipv4.address",
    "macaddress": "ansible_default_ipv4.macaddress",
    "netmask": "ansible_default_ipv4.netmask",
    "network": "ansible_default_ipv4.network",
    "interfaces": "ansible_interfaces",
    # Kernel
    "kernel": "ansible_kernel",
    "kernelversion": "ansible_kernel_version",
    "kernelrelease": "ansible_kernel",
    "kernelmajversion": "ansible_kernel.split('.')[0:2] | join('.')",
    # Virtualization
    "virtual": "ansible_virtualization_type",
    "is_virtual": "ansible_virtualization_role == 'guest'",
    # System
    "timezone": "ansible_date_time.tz",
    "uptime_hours": "(ansible_uptime_seconds / 3600) | int",
    "uptime_days": "(ansible_uptime_seconds / 86400) | int",
    "uptime_seconds": "ansible_uptime_seconds",
    # SELinux
    "selinux": "ansible_selinux.status == 'enabled'",
    "selinux_current_mode": "ansible_selinux.mode",
    # Puppet-specific (no Ansible equivalent)
    "puppetversion": None,
    "clientcert": "inventory_hostname",
    "clientversion": None,
    "environment": None,
    # Trusted facts
    "trusted.certname": "inventory_hostname",
    "trusted.domain": "ansible_domain",
    "trusted.hostname": "ansible_hostname",
}


def map_fact(puppet_fact: str) -> str:
    """Map a single Puppet fact name to its Ansible equivalent.

    Args:
        puppet_fact: Puppet fact name, with or without $:: prefix.
            Examples: "$::osfamily", "osfamily", "facts['os']['family']"

    Returns:
        Ansible fact name, or the original with a warning comment if unmapped.
    """
    # Clean up the fact name
    clean = puppet_fact.strip()
    clean = clean.lstrip("$")
    clean = clean.lstrip(":")
    clean = clean.lstrip(":")

    # Handle modern facts hash syntax: $facts['os']['family']
    if clean.startswith("facts["):
        return _map_structured_fact(clean)

    # Handle interface-specific facts: ipaddress_eth0
    for prefix in ("ipaddress_", "macaddress_", "netmask_", "network_"):
        if clean.startswith(prefix) and clean != prefix.rstrip("_"):
            iface = clean[len(prefix):]
            base = prefix.rstrip("_")
            if base == "ipaddress":
                return f"ansible_{iface}.ipv4.address"
            elif base == "macaddress":
                return f"ansible_{iface}.macaddress"
            elif base == "netmask":
                return f"ansible_{iface}.ipv4.netmask"
            elif base == "network":
                return f"ansible_{iface}.ipv4.network"

    # Direct mapping
    if clean in FACT_MAP:
        mapped = FACT_MAP[clean]
        if mapped is None:
            return f"UNMAPPED_PUPPET_FACT_{clean}"
        return mapped

    # Unknown fact — return with warning marker
    return f"UNMAPPED_PUPPET_FACT_{clean}"


def _map_structured_fact(fact: str) -> str:
    """Map modern structured fact syntax to Ansible.

    $facts['os']['family'] → ansible_os_family
    $facts['networking']['ip'] → ansible_default_ipv4.address
    """
    structured_map = {
        "facts['os']['family']": "ansible_os_family",
        "facts['os']['name']": "ansible_distribution",
        "facts['os']['release']['major']": "ansible_distribution_major_version",
        "facts['os']['release']['full']": "ansible_distribution_version",
        "facts['os']['distro']['codename']": "ansible_distribution_release",
        "facts['os']['architecture']": "ansible_architecture",
        "facts['networking']['fqdn']": "ansible_fqdn",
        "facts['networking']['hostname']": "ansible_hostname",
        "facts['networking']['domain']": "ansible_domain",
        "facts['networking']['ip']": "ansible_default_ipv4.address",
        "facts['networking']['mac']": "ansible_default_ipv4.macaddress",
        "facts['kernel']": "ansible_kernel",
        "facts['kernelversion']": "ansible_kernel_version",
        "facts['memory']['system']['total_bytes']": "ansible_memtotal_mb",
        "facts['processors']['count']": "ansible_processor_vcpus",
        "facts['virtual']": "ansible_virtualization_type",
        "facts['timezone']": "ansible_date_time.tz",
    }

    if fact in structured_map:
        return structured_map[fact]

    return f"UNMAPPED_STRUCTURED_FACT_{fact}"
