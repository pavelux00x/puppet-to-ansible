# p2a — Puppet to Ansible Converter

> Convert Puppet manifests, modules, and entire codebases into production-ready, idiomatic Ansible.

![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![lark](https://img.shields.io/badge/lark-1.3.1-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![ansible-lint](https://img.shields.io/badge/ansible--lint-passing-brightgreen)

---

## Overview

**p2a** is a CLI tool for infrastructure teams migrating from Puppet to Ansible. It reads Puppet
manifests (`.pp` files), resolves include chains, evaluates Hiera data, converts ERB templates to
Jinja2, and writes production-ready Ansible playbooks and roles.

The goal is not a literal transliteration of Puppet syntax — it is to produce **idiomatic Ansible**
that follows best practices, uses Fully Qualified Collection Names (FQCN), and passes
`ansible-lint` out of the box. Output is something an engineer can review, adjust, and deploy
with minimal rework.

p2a targets:

- Infrastructure engineers migrating a Puppet codebase to Ansible
- Platform teams running Puppet 3 on legacy infrastructure alongside modern Puppet 4/5/6/7
- Anyone who needs an automated first pass before a manual migration review

---

## Features

- **Full Puppet 3 + 4/5/6/7 support** — handles `$::osfamily`, `import`, classic `params.pp`,
  `hiera()` (Puppet 3) as well as `$facts['os']['family']`, `lookup()`, typed parameters, lambdas,
  and EPP templates (Puppet 4+)
- **Smart output mode selection** — auto-detects whether to emit a flat playbook or a full role
  structure based on what the manifest contains; override with `--output-mode`
- **Hiera v3/v4/v5 live resolution** — reads `hiera.yaml`, loads data files, resolves `lookup()`
  and `hiera()` calls at conversion time; unresolved lookups become Ansible variables
- **Cross-file include/import chain resolution** — the preprocessor follows `include`, `require`,
  and `import` statements across module files and merges them into a single conversion pass
- **ERB → Jinja2 template conversion** — converts variables, conditionals, loops, and common Ruby
  methods to Jinja2 equivalents; also available as a standalone `convert-erb` command
- **Hiera data → group\_vars / host\_vars** — maps Hiera hierarchy levels to the equivalent
  Ansible inventory vars structure
- **16+ resource types converted** — see the [Resource Mapping Table](#resource-mapping-table)
- **Graceful degradation** — unconvertible resources produce a `# TODO: Manual conversion needed`
  task and are logged in the report; the output is always valid YAML
- **FQCN output** — every generated module reference uses the fully qualified form
  (`ansible.builtin.package`, not `package`)
- **Auto-generated `requirements.yml`** — lists every Galaxy collection needed by the converted
  output
- **Detailed conversion report** — printed after every run; shows converted counts, warnings,
  and unconverted items by type

---

## Installation

```bash
git clone https://github.com/your-org/puppet-to-ansible.git
cd puppet-to-ansible
pip install -e ".[dev]"
```

For `ansible-lint` validation support:

```bash
pip install -e ".[dev,lint]"
```

Verify the install:

```bash
p2a --version
```

---

## Quick Start

**Convert a single manifest:**

```bash
p2a convert site.pp -o output/
```

**Convert a full Puppet module to an Ansible role:**

```bash
p2a convert-module /etc/puppet/modules/nginx/ -o roles/
```

**Convert an entire Puppet control repo:**

```bash
p2a convert-all /etc/puppet/ \
  --module-paths /etc/puppet/modules \
  --hiera /etc/puppet/hiera.yaml \
  -o ansible-project/
```

---

## Commands Reference

### `p2a convert` — Single manifest

Converts a single `.pp` file. When `--module-paths` is provided, the preprocessor follows all
`include` / `require` chains and merges them into the output before conversion.

```bash
p2a convert <manifest> [OPTIONS]
```

| Option | Short | Default | Description |
|---|---|---|---|
| `--puppet-version` | `-p` | `4` | Puppet language version: `3` (legacy) or `4` (modern) |
| `--output-mode` | `-m` | `auto` | Output format: `auto`, `playbook`, or `role` |
| `--output` | `-o` | `output` | Output directory |
| `--hosts` | | `all` | Ansible hosts pattern for generated playbooks |
| `--module-paths` | `-M` | _(none)_ | Puppet modulepath directories; repeat for multiple paths |
| `--hiera` | | _(auto)_ | Path to `hiera.yaml`; auto-searched upward from the manifest if omitted |
| `--dry-run` | | off | Parse and convert without writing any files |
| `-v` / `-vv` / `-vvv` | | 0 | Verbosity: `-v` warnings, `-vv` info, `-vvv` debug |

**Auto-detection logic for `--output-mode auto`:**

| Manifest content | Selected output mode |
|---|---|
| Only resource declarations (no class/define) | `playbook` |
| Contains class or defined-type definitions | `role` |
| Multiple manifests resolved via include chains | `role` |

**Examples:**

```bash
# Puppet 3 manifest
p2a convert site.pp --puppet-version 3 -o output/

# Follow include chains, resolve Hiera, force role output
p2a convert manifests/init.pp \
  --module-paths /etc/puppet/modules \
  --hiera /etc/puppet/hiera.yaml \
  --output-mode role \
  -o roles/myapp/

# Dry run with full debug output
p2a convert site.pp --dry-run -vvv
```

---

### `p2a convert-module` — Full module

Converts an entire Puppet module directory to an Ansible role. The preprocessor processes
`init.pp` first, then all other manifests in dependency order. ERB templates are converted to
Jinja2, and static files are copied unchanged.

```bash
p2a convert-module <module_dir> [OPTIONS]
```

Accepts the same options as `convert` (output is always `role` mode).

**Examples:**

```bash
# Basic conversion
p2a convert-module /etc/puppet/modules/nginx/ -o roles/

# With Hiera and sibling module resolution
p2a convert-module /etc/puppet/modules/nginx/ \
  --module-paths /etc/puppet/modules \
  --hiera /etc/puppet/hiera.yaml \
  -o roles/
```

Output is written to `<output>/<module_name>/`:

```
roles/nginx/
├── tasks/main.yml
├── handlers/main.yml
├── templates/          # ERB → Jinja2
├── files/              # Copied as-is
├── defaults/main.yml
├── vars/main.yml
└── meta/main.yml
requirements.yml        # Galaxy collections needed
```

---

### `p2a convert-all` — Entire codebase

Converts a complete Puppet control repository. Automatically discovers the codebase structure —
no flags are required for standard layouts.

```bash
p2a convert-all <puppet_dir> [OPTIONS]
```

**Auto-discovery:** `convert-all` searches the root of `puppet_dir` for:

| Artifact | Searched locations |
|---|---|
| Module directories | `modules/`, `site-modules/`, `dist/`, `site/` |
| Hiera config | `hiera.yaml`, `hiera.yml` |
| Hiera data directory | `datadir` from `hiera.yaml`, then `hieradata/`, `hiera/`, `data/` |
| Site manifest | `manifests/site.pp`, `site.pp`, `manifests/init.pp` |

**Conversion passes (in order):**

1. Each discovered module → `roles/<module_name>/`
2. `site.pp` (with resolved includes) → `site.yml` + `inventory/hosts.yml`
3. Hiera data directory → `inventory/group_vars/` and `inventory/host_vars/`
4. Aggregated `requirements.yml`

**Examples:**

```bash
# Fully automatic on a standard control-repo layout
p2a convert-all /etc/puppet/ -o ansible-project/

# Explicit paths for non-standard layout
p2a convert-all /opt/puppet-control/ \
  --module-paths /opt/puppet-control/modules \
  --module-paths /opt/puppet-control/site-modules \
  --hiera /opt/puppet-control/hiera.yaml \
  -o ansible-project/

# Dry run to preview what would be generated
p2a convert-all /etc/puppet/ --dry-run -vv
```

---

### `p2a convert-erb` — Single ERB template

Converts a single ERB template file to Jinja2 (`.j2`).

```bash
p2a convert-erb <template> [--output <path>] [-v]
```

If `--output` is omitted, the `.j2` file is written next to the source with `.erb` replaced.

```bash
# Writes templates/nginx.conf.j2
p2a convert-erb templates/nginx.conf.erb

# Write to a specific location
p2a convert-erb templates/nginx.conf.erb -o roles/nginx/templates/nginx.conf.j2
```

---

### `p2a convert-hiera` — Hiera data directory

Converts a Hiera data directory to Ansible `group_vars` / `host_vars` structure.

```bash
p2a convert-hiera <hiera_dir> -o <output_dir> [-v]
```

```bash
p2a convert-hiera /etc/puppet/hieradata/ -o inventory/
```

See [Hiera Integration](#hiera-integration) for details on the hierarchy mapping.

---

## Real-world Examples

### Example A: Simple manifest → playbook

**Input (`nginx.pp`):**

```puppet
package { 'nginx':
  ensure => installed,
}

service { 'nginx':
  ensure  => running,
  enable  => true,
  require => Package['nginx'],
}

file { '/etc/nginx/nginx.conf':
  content => template('nginx/nginx.conf.erb'),
  notify  => Service['nginx'],
}
```

**Command:**

```bash
p2a convert nginx.pp -o output/
```

**Generated `output/nginx.yml`:**

```yaml
# Generated by p2a (Puppet to Ansible Converter)
# Source: nginx.pp
# Review this file before deploying — automated conversion may need manual adjustments
---
- name: nginx
  hosts: all
  become: true
  tasks:
    - name: Install nginx
      ansible.builtin.package:
        name: nginx
        state: present

    - name: Ensure nginx is running and enabled
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true

    - name: Configure /etc/nginx/nginx.conf
      ansible.builtin.template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
      notify: Restart nginx

  handlers:
    - name: Restart nginx
      ansible.builtin.service:
        name: nginx
        state: restarted
```

---

### Example B: Parameterized class with Hiera → role structure

**Input (`manifests/init.pp`):**

```puppet
class nginx (
  Integer $port = lookup('nginx::port', Integer, 'first', 80),
  String  $user = lookup('nginx::user', String, 'first', 'www-data'),
) {
  package { 'nginx':
    ensure => installed,
  }

  file { '/etc/nginx/nginx.conf':
    content => template('nginx/nginx.conf.erb'),
    notify  => Service['nginx'],
  }

  service { 'nginx':
    ensure => running,
    enable => true,
  }
}
```

**Command:**

```bash
p2a convert-module /etc/puppet/modules/nginx/ \
  --hiera /etc/puppet/hiera.yaml \
  -o roles/
```

**Generated structure:**

```
roles/nginx/
├── tasks/
│   └── main.yml        # package, template, service tasks
├── handlers/
│   └── main.yml        # Restart nginx handler
├── templates/
│   └── nginx.conf.j2   # ERB converted to Jinja2
├── defaults/
│   └── main.yml        # nginx_port: 80, nginx_user: www-data
└── meta/
    └── main.yml
requirements.yml          # collections: []  (only builtins used)
```

**`defaults/main.yml`:**

```yaml
---
# Defaults for role nginx
# Source: manifests/init.pp class parameters + Hiera data
nginx_port: 80
nginx_user: www-data
```

---

### Example C: Enterprise control repo

**Puppet codebase layout:**

```
/etc/puppet/
├── hiera.yaml
├── manifests/
│   └── site.pp
├── modules/
│   ├── nginx/
│   ├── mysql/
│   └── monitoring/
└── hieradata/
    ├── common.yaml
    ├── nodes/
    │   └── web01.example.com.yaml
    └── roles/
        └── webserver.yaml
```

**Command:**

```bash
p2a convert-all /etc/puppet/ \
  --module-paths /etc/puppet/modules \
  --hiera /etc/puppet/hiera.yaml \
  -o ansible-project/
```

**Generated output:**

```
ansible-project/
├── site.yml                          # From site.pp node definitions
├── requirements.yml                  # All needed Galaxy collections
├── roles/
│   ├── nginx/
│   │   ├── tasks/main.yml
│   │   ├── handlers/main.yml
│   │   ├── templates/nginx.conf.j2
│   │   └── defaults/main.yml
│   ├── mysql/
│   │   └── ...
│   └── monitoring/
│       └── ...
└── inventory/
    ├── hosts.yml                     # From site.pp node definitions
    ├── group_vars/
    │   ├── all.yml                   # From hieradata/common.yaml
    │   └── webserver.yml             # From hieradata/roles/webserver.yaml
    └── host_vars/
        └── web01.example.com.yml     # From hieradata/nodes/web01...yaml
```

---

## Output Structure

### Playbook mode

Generated for a simple manifest with no class definitions, or with `--output-mode playbook`.

```
output/
└── <manifest_stem>.yml     # Single playbook with tasks and handlers inline
```

### Role mode

Generated when the manifest contains class or defined-type definitions, or when converting a
full module (always role mode).

```
roles/<name>/
├── tasks/
│   └── main.yml            # All converted tasks, in dependency order
├── handlers/
│   └── main.yml            # Handlers generated from notify/subscribe
├── templates/              # ERB files converted to Jinja2
├── files/                  # Static files copied as-is
├── defaults/
│   └── main.yml            # Class parameters + Hiera module data defaults
├── vars/
│   └── main.yml            # OS-specific vars (from params.pp case blocks)
└── meta/
    └── main.yml            # From metadata.json if present
```

---

## Resource Mapping Table

| Puppet Resource | Ansible Module | Notes |
|---|---|---|
| `package` | `ansible.builtin.package` | `ansible.builtin.apt` / `ansible.builtin.yum` when provider is explicit |
| `service` | `ansible.builtin.service` | `ansible.builtin.systemd` when provider is systemd |
| `file` (ensure=file + content) | `ansible.builtin.copy` | |
| `file` (ensure=file + template source) | `ansible.builtin.template` | |
| `file` (ensure=directory) | `ansible.builtin.file` | `state: directory` |
| `file` (ensure=link) | `ansible.builtin.file` | `state: link` |
| `file` (ensure=absent) | `ansible.builtin.file` | `state: absent` |
| `file_line` | `ansible.builtin.lineinfile` | Direct mapping |
| `exec` | `ansible.builtin.command` | Default |
| `exec` (shell metacharacters) | `ansible.builtin.shell` | When command contains `\|`, `>`, `<`, `;`, `&&`, `\|\|` |
| `exec` (refreshonly=true) | handler | Converted to an Ansible handler |
| `cron` | `ansible.builtin.cron` | Direct mapping |
| `user` | `ansible.builtin.user` | |
| `group` | `ansible.builtin.group` | |
| `mount` | `ansible.posix.mount` | Requires `ansible.posix` collection |
| `host` | `ansible.builtin.lineinfile` | Writes to `/etc/hosts` |
| `ssh_authorized_key` | `ansible.posix.authorized_key` | Requires `ansible.posix` collection |
| `yumrepo` | `ansible.builtin.yum_repository` | |
| `apt::source` | `ansible.builtin.apt_repository` | |
| `apt::key` | `ansible.builtin.apt_key` | Consider migrating to `get_url` + `signed-by` |
| `ini_setting` | `community.general.ini_file` | Requires `community.general` collection |
| `selboolean` | `ansible.posix.seboolean` | Requires `ansible.posix` collection |
| `firewall` (puppetlabs/firewall) | `ansible.posix.firewalld` | When target is firewalld |
| `firewall` (puppetlabs/firewall) | `community.general.ufw` | When target is ufw |
| `augeas` | context-dependent | See below |
| `tidy` | `ansible.builtin.find` + `ansible.builtin.file` | Two tasks: find then remove |

### Augeas handling

Augeas edits configuration files structurally. There is no 1:1 Ansible equivalent. p2a applies
these rules in order:

1. Target file has a dedicated Ansible module (e.g. sshd\_config) → `ansible.builtin.lineinfile`
   with appropriate regex
2. Target is an INI file → `community.general.ini_file`
3. Target is an XML file → `community.general.xml`
4. Default fallback → `ansible.builtin.lineinfile` with regex
5. Too complex for auto-conversion → generates a `# TODO` task and suggests a Jinja2 template

---

## Hiera Integration

p2a performs live Hiera resolution during conversion. `lookup()` and `hiera()` calls in manifests
are evaluated against your actual Hiera data at conversion time, producing concrete values in
the Ansible output rather than unresolved placeholders.

**Hierarchy level → Ansible mapping:**

| Hiera level | Ansible equivalent | Path |
|---|---|---|
| `nodes/%{::fqdn}` | `host_vars/` | `inventory/host_vars/<hostname>.yml` |
| `roles/%{::role}` | `group_vars/` (role group) | `inventory/group_vars/<role>.yml` |
| `os/%{::osfamily}` | `group_vars/` (OS group) | `inventory/group_vars/<os_family>.yml` |
| `common` | `group_vars/all.yml` | `inventory/group_vars/all.yml` |

**Variable naming:**

- Puppet `::` scope separator becomes `_` in Ansible variable names
- `apache::port: 8080` → `apache_port: 8080`
- Values that look like secrets (contain `password`, `key`, `token`, or `secret`) are flagged:
  the output uses `"{{ vault_<varname> }}"` with a comment recommending ansible-vault

**When resolution fails:**

If a `lookup()` call references a key not present in the Hiera data (or if no `hiera.yaml` is
provided), the call is converted to an Ansible variable reference: `"{{ nginx_port }}"`. Define
the value in `defaults/main.yml` or `group_vars/`.

---

## Cross-file Resolution

When `--module-paths` is provided, p2a uses its preprocessor to follow `include`, `require`, and
`import` statements across module files before parsing begins. The files are sorted into dependency
order (dependencies first), parsed individually, and their conversion results are merged.

A manifest that does:

```puppet
include nginx::config
include nginx::service
```

will resolve `nginx/manifests/config.pp` and `nginx/manifests/service.pp` as part of the same
pass, producing a single coherent output.

**Best practice:** always pass `--module-paths` when converting code that crosses module boundaries:

```bash
p2a convert site.pp \
  --module-paths /etc/puppet/modules \
  --module-paths /etc/puppet/site-modules \
  -o output/
```

Unresolvable includes (modules not on the provided paths) generate a warning in the report but
do not abort the conversion.

---

## Migration Report

After every conversion, p2a prints a summary report to stdout:

```
=== Puppet to Ansible Conversion Report ===

  Converted Resources
  ─────────────────────────────
  Resource type    Count
  cron                 3
  exec                 7
  file                15
  package             12
  service              8
  user                 2
  TOTAL               47

⚠  3 warning(s):
  • exec 'run-migration' uses refreshonly but no subscriber found (line 42)
  • template 'config.erb' contains Ruby code block — manual review needed
  • Variable $custom_fact has no Facter → Ansible mapping

✗  2 resource(s) not converted:
  • custom_type['logrotate::rule'] — no converter, TODO generated
  • augeas['complex-edit'] — too complex for auto-conversion, TODO generated

Collections required: ansible.posix, community.general
  Install with: ansible-galaxy collection install -r requirements.yml
```

The report is always printed at the end of every command; there is no separate flag needed.

---

## Graceful Degradation

p2a never silently drops a resource it cannot convert. Instead it emits a placeholder task in
the output that preserves the original Puppet code for manual review:

```yaml
# TODO: Manual conversion needed
# Puppet resource: custom_type['logrotate::rule']
# Reason: no automated converter for this resource type
# Original Puppet code:
#   custom_type { 'logrotate::rule':
#     path   => '/var/log/myapp/*.log',
#     rotate => 7,
#   }
- name: "TODO: convert custom_type logrotate::rule"
  ansible.builtin.debug:
    msg: "Manual conversion required — see comment above"
```

Every unconverted resource appears in the report under "Unconverted" with the reason. The output
YAML remains syntactically valid so `ansible-lint` can process the partial result.

---

## Limitations

The following Puppet constructs cannot be auto-converted and always produce TODO tasks:

| Construct | Reason | Suggested path forward |
|---|---|---|
| **Custom resource types** (`lib/puppet/type/`) | Require rewriting as Python Ansible modules | Write a module in `library/`; the TODO preserves the type interface |
| **Custom providers** (`lib/puppet/provider/`) | Coupled to Puppet's resource abstraction layer | Same as custom types above |
| **Custom facts** (`lib/facter/`) | Written in Ruby; no mechanical translation | Rewrite as Ansible custom facts or filter plugins; TODO includes the original Ruby |
| **Custom Puppet functions** (`lib/puppet/functions/`) | Ruby internals | Usually replaceable with Jinja2 filters or `set_fact`; TODO includes the original code |
| **Exported resources** (`<<\| \|>>`) | No direct Ansible equivalent | Use delegation, `hostvars`, or dynamic groups; the TODO includes an explanation |
| **Puppet environments** | Puppet-specific staging concept | Use branch-based inventory or separate directory trees |

**Virtual resources** (`@resource`) are converted to regular tasks with a `when:` condition based
on collection context. The condition may need manual adjustment.

**Regex node definitions** in `site.pp` produce a TODO comment in `inventory/hosts.yml` — the
matching hostnames must be added manually since p2a cannot enumerate the set at conversion time.

---

## Contributing

1. Fork the repository and create a feature branch
2. Run the test suite: `pytest`
3. Lint and format: `ruff check src/ tests/` and `ruff format src/ tests/`
4. Add at least one fixture-based test for any new converter (see `tests/fixtures/`)
5. Open a pull request

### Adding a new resource converter

Each resource type lives in its own file under `src/converters/`. The quickest way to scaffold
a new converter, test fixtures, and test file is:

```
/add-converter
```

### Running tests

```bash
# Full suite
pytest

# With coverage
pytest --cov=src --cov-report=term-missing

# Single converter test file
pytest tests/test_converters/test_package.py -v
```

---

## License

MIT — see `LICENSE` for details.
