"""Forge module → Ansible Galaxy collection mapper.

Maps Puppetfile entries to their Ansible equivalents and produces an
analysis report showing which modules are covered, which need manual work,
and which Galaxy collections are required.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.puppetfile.parser import Puppetfile, PuppetModule


# ── Status constants ───────────────────────────────────────────────────────────

STATUS_MAPPED    = "mapped"      # has a known Galaxy collection equivalent
STATUS_BUILTIN   = "builtin"     # covered by ansible.builtin, no extra collection
STATUS_CONVERTED = "converted"   # p2a converts its resources, collection already tracked
STATUS_MANUAL    = "manual"      # requires manual rewrite (custom types/providers)
STATUS_GIT       = "git"         # internal git module → local role
STATUS_LOCAL     = "local"       # local module → local role
STATUS_UNKNOWN   = "unknown"     # no mapping known


# ── Known mappings ─────────────────────────────────────────────────────────────
#
# Key: 'author/module' (lowercase)
# Value: (status, galaxy_collection_or_None, note)

_FORGE_MAP: dict[str, tuple[str, str | None, str]] = {
    # stdlib / language helpers
    "puppetlabs/stdlib":         (STATUS_BUILTIN,   None,
                                  "Functions replaced by Jinja2 filters and ansible.builtin.set_fact"),
    "puppetlabs/concat":         (STATUS_BUILTIN,   None,
                                  "Use ansible.builtin.assemble or a Jinja2 template with loop"),

    # Core OS resources
    "puppetlabs/apache":         (STATUS_CONVERTED, None,
                                  "Resources convert to ansible.builtin.* — no extra collection"),
    "puppetlabs/nginx":          (STATUS_CONVERTED, None,
                                  "Resources convert to ansible.builtin.* — no extra collection"),
    "puppetlabs/ntp":            (STATUS_CONVERTED, None,
                                  "Resources convert to ansible.builtin.* — no extra collection"),
    "puppetlabs/sshd":           (STATUS_CONVERTED, "ansible.posix",
                                  "ssh_authorized_key → ansible.posix.authorized_key"),
    "saz/ssh":                   (STATUS_CONVERTED, "ansible.posix",
                                  "ssh_authorized_key → ansible.posix.authorized_key"),

    # Package management
    "puppetlabs/apt":            (STATUS_CONVERTED, None,
                                  "apt::source → ansible.builtin.apt_repository"),
    "puppetlabs/yum":            (STATUS_CONVERTED, None,
                                  "yumrepo → ansible.builtin.yum_repository"),

    # Databases
    "puppetlabs/mysql":          (STATUS_MAPPED,    "community.mysql",
                                  "mysql_db/mysql_user → community.mysql collection"),
    "puppetlabs/postgresql":     (STATUS_MAPPED,    "community.postgresql",
                                  "postgresql_db/postgresql_user → community.postgresql"),

    # Containers
    "puppetlabs/docker_platform": (STATUS_MAPPED,   "community.docker",
                                  "docker::container → community.docker.docker_container"),
    "garethr/docker":            (STATUS_MAPPED,    "community.docker",
                                  "docker resources → community.docker collection"),
    "puppetlabs/kubernetes":     (STATUS_MAPPED,    "kubernetes.core",
                                  "kubernetes::resource → kubernetes.core.k8s"),

    # Security / SELinux
    "puppetlabs/selinux":        (STATUS_CONVERTED, "ansible.posix",
                                  "selboolean/selmodule → ansible.posix"),
    "puppetlabs/firewall":       (STATUS_CONVERTED, "ansible.posix",
                                  "firewall rules → ansible.posix.firewalld"),

    # Configuration files
    "puppetlabs/inifile":        (STATUS_CONVERTED, "community.general",
                                  "ini_setting → community.general.ini_file"),
    "herculesteam/augeasproviders_core":    (STATUS_CONVERTED, None,
                                  "augeas → lineinfile or ini_file depending on context"),
    "herculesteam/augeasproviders_sysctl":  (STATUS_CONVERTED, "ansible.posix",
                                  "sysctl → ansible.posix.sysctl"),
    "herculesteam/augeasproviders_ssh":     (STATUS_CONVERTED, "ansible.posix",
                                  "sshd config → lineinfile + ansible.posix"),

    # Monitoring
    "puppet/archive":            (STATUS_BUILTIN,   None,
                                  "archive/download → ansible.builtin.get_url + unarchive"),
    "puppetlabs/vcsrepo":        (STATUS_BUILTIN,   None,
                                  "vcsrepo → ansible.builtin.git"),

    # Mount / storage
    "puppetlabs/lvm":            (STATUS_MAPPED,    "community.general",
                                  "lvm resources → community.general.lvg / lvol"),

    # Users / sudo
    "saz/sudo":                  (STATUS_CONVERTED, None,
                                  "sudo rules → ansible.builtin.template for /etc/sudoers.d/"),
    "puppetlabs/sudo":           (STATUS_CONVERTED, None,
                                  "sudo rules → ansible.builtin.template for /etc/sudoers.d/"),

    # Networking
    "puppet/resolv_conf":        (STATUS_BUILTIN,   None,
                                  "resolv.conf → ansible.builtin.template"),

    # Certs / PKI
    "puppetlabs/openssl":        (STATUS_MAPPED,    "community.crypto",
                                  "openssl certs → community.crypto collection"),
    "camptocamp/openssl":        (STATUS_MAPPED,    "community.crypto",
                                  "openssl certs → community.crypto collection"),

    # Scheduling
    "puppet/cron":               (STATUS_BUILTIN,   None,
                                  "cron → ansible.builtin.cron"),

    # Java
    "puppetlabs/java":           (STATUS_BUILTIN,   None,
                                  "java package → ansible.builtin.package"),

    # Ruby / rbenv
    "ruby/rbenv":                (STATUS_MANUAL,    None,
                                  "No direct Ansible equivalent — use community.general.rbenv or manual tasks"),

    # Windows (out of scope for most migrations but flag it)
    "puppetlabs/dsc":            (STATUS_MANUAL,    None,
                                  "Windows DSC → ansible.windows collection — requires manual mapping"),
    "puppetlabs/windows":        (STATUS_MANUAL,    "ansible.windows",
                                  "Windows resources → ansible.windows collection"),
}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ModuleMapping:
    """Analysis result for one Puppetfile entry."""
    module: PuppetModule
    status: str
    galaxy_collection: str | None
    notes: str


@dataclass
class MappingReport:
    """Full analysis of a Puppetfile."""
    mappings: list[ModuleMapping] = field(default_factory=list)
    # Collections already tracked by the converter (from actual resource conversion)
    converter_collections: set[str] = field(default_factory=set)

    # ── Derived views ─────────────────────────────────────────────────────────

    @property
    def required_collections(self) -> set[str]:
        """All Galaxy collections needed (from mappings + converter output)."""
        cols: set[str] = set(self.converter_collections)
        for m in self.mappings:
            if m.galaxy_collection:
                cols.add(m.galaxy_collection)
        return cols

    @property
    def manual_modules(self) -> list[ModuleMapping]:
        return [m for m in self.mappings if m.status == STATUS_MANUAL]

    @property
    def git_modules(self) -> list[ModuleMapping]:
        return [m for m in self.mappings if m.status == STATUS_GIT]

    @property
    def unknown_modules(self) -> list[ModuleMapping]:
        return [m for m in self.mappings if m.status == STATUS_UNKNOWN]

    @property
    def forge_total(self) -> int:
        return sum(1 for m in self.mappings if m.module.source == "forge")

    @property
    def covered_total(self) -> int:
        return sum(
            1 for m in self.mappings
            if m.status in (STATUS_MAPPED, STATUS_BUILTIN, STATUS_CONVERTED)
        )


# ── Mapper ─────────────────────────────────────────────────────────────────────

class PuppetfileMapper:
    """Analyse a :class:`Puppetfile` and produce a :class:`MappingReport`."""

    def analyze(
        self,
        puppetfile: Puppetfile,
        converter_collections: set[str] | None = None,
    ) -> MappingReport:
        report = MappingReport(
            converter_collections=converter_collections or set()
        )

        for mod in puppetfile.modules:
            mapping = self._map_module(mod)
            report.mappings.append(mapping)

        return report

    # ── Private ───────────────────────────────────────────────────────────────

    def _map_module(self, mod: PuppetModule) -> ModuleMapping:
        if mod.source == "git":
            return ModuleMapping(
                module=mod, status=STATUS_GIT,
                galaxy_collection=None,
                notes="Internal/git module → convert as local Ansible role",
            )

        if mod.source in ("local", "svn"):
            return ModuleMapping(
                module=mod, status=STATUS_LOCAL,
                galaxy_collection=None,
                notes="Local module → convert as local Ansible role",
            )

        # Forge module — look up the mapping table
        key = f"{mod.author}/{mod.name}".lower()
        if key in _FORGE_MAP:
            status, collection, notes = _FORGE_MAP[key]
            return ModuleMapping(
                module=mod, status=status,
                galaxy_collection=collection,
                notes=notes,
            )

        # Unknown — generate a useful hint
        return ModuleMapping(
            module=mod, status=STATUS_UNKNOWN,
            galaxy_collection=None,
            notes=(
                f"No known mapping for '{mod.full_name}'. "
                "Check Ansible Galaxy for a community collection. "
                "Resources it declares will generate TODO tasks."
            ),
        )
