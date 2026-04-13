# p2a — Puppet to Ansible Converter

Convert Puppet manifests, modules, and entire codebases into production-ready Ansible.

* 100% Local Execution ( No external APIS / LLM ) 

![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## Install

```bash
git clone https://github.com/pavelux00x/puppet-to-ansible.git
cd puppet-to-ansible
pip install -e .
```

---

## Usage

```bash
# Single manifest → playbook
p2a convert nginx.pp -o output/

# Full module → role
p2a convert-module modules/nginx/ -o roles/

# Entire control repo → Ansible project
p2a convert-all /etc/puppet/ -o ansible-project/

# ERB template → Jinja2
p2a convert-erb templates/nginx.conf.erb

# Hiera data only
p2a convert-hiera hieradata/ -o inventory/

# Analyse Puppetfile → Galaxy collection mapping
p2a analyze-puppetfile Puppetfile
```

---

## Examples

### Package + Service + notify

**Input:**
```puppet
package { 'nginx': ensure => installed }

file { '/etc/nginx/nginx.conf':
  content => template('nginx/nginx.conf.erb'),
  notify  => Service['nginx'],
}

service { 'nginx':
  ensure => running,
  enable => true,
}
```

**Output (`output/nginx.yml`):**
```yaml
- name: nginx
  hosts: all
  become: true
  tasks:
    - name: Install nginx
      ansible.builtin.package:
        name: nginx
        state: present

    - name: Configure /etc/nginx/nginx.conf
      ansible.builtin.template:
        src: nginx.conf.j2
        dest: /etc/nginx/nginx.conf
      notify: Restart nginx

    - name: Ensure nginx is running and enabled
      ansible.builtin.service:
        name: nginx
        state: started
        enabled: true

  handlers:
    - name: Restart nginx
      ansible.builtin.service:
        name: nginx
        state: restarted
```

---

### Class with Hiera parameters → role

**Input (`modules/nginx/manifests/init.pp`):**
```puppet
class nginx (
  Integer $port = lookup('nginx::port', Integer, 'first', 80),
  String  $user = lookup('nginx::user', String,  'first', 'www-data'),
) {
  package { 'nginx': ensure => installed }

  file { '/etc/nginx/nginx.conf':
    content => template('nginx/nginx.conf.erb'),
    notify  => Service['nginx'],
  }

  service { 'nginx': ensure => running, enable => true }
}
```

```bash
p2a convert-module modules/nginx/ --hiera hiera.yaml -o roles/
```

**Output:**
```
roles/nginx/
├── tasks/
│   ├── main.yml          # include_tasks for each class
│   └── nginx.yml         # package, template, service tasks
├── handlers/main.yml     # Restart nginx
├── templates/
│   └── nginx.conf.j2     # ERB → Jinja2
└── defaults/main.yml     # nginx_port: 80, nginx_user: www-data
```

---

### Full control repo

```bash
p2a convert-all /etc/puppet/ -o ansible-project/
```

```
ansible-project/
├── site.yml                        # From site.pp node definitions
├── requirements.yml                # Galaxy collections needed
├── roles/
│   ├── nginx/
│   ├── mysql/
│   └── monitoring/
└── inventory/
    ├── hosts.yml                   # From site.pp node definitions
    ├── group_vars/
    │   ├── all.yml                 # From hieradata/common.yaml
    │   └── webserver.yml           # From hieradata/roles/webserver.yaml
    └── host_vars/
        └── web01.example.com.yml
```

---

## What gets converted

| Puppet | Ansible |
|---|---|
| `package` | `ansible.builtin.package` (or `apt`/`yum` if provider set) |
| `service` | `ansible.builtin.service` (or `systemd` if provider set) |
| `file` (content) | `ansible.builtin.copy` |
| `file` (template source) | `ansible.builtin.template` |
| `file` (directory/link/absent) | `ansible.builtin.file` |
| `file_line` | `ansible.builtin.lineinfile` |
| `exec` | `ansible.builtin.command` (or `shell` if pipe/redirect) |
| `exec` (refreshonly) | handler |
| `cron` | `ansible.builtin.cron` |
| `user` / `group` | `ansible.builtin.user` / `group` |
| `mount` | `ansible.posix.mount` |
| `host` | `ansible.builtin.lineinfile` → `/etc/hosts` |
| `ssh_authorized_key` | `ansible.posix.authorized_key` |
| `yumrepo` | `ansible.builtin.yum_repository` |
| `apt::source` | `ansible.builtin.apt_repository` |
| `selboolean` | `ansible.posix.seboolean` |
| `firewall` | `ansible.posix.firewalld` or `community.general.ufw` |
| `augeas` | `lineinfile` / `ini_file` / `xml` depending on context |
| `ini_setting` | `community.general.ini_file` |
| `concat` / `concat::fragment` | `ansible.builtin.assemble` + `copy` to staging dir |
| `tidy` | `ansible.builtin.find` + `ansible.builtin.file` |
| `sysctl` | `ansible.posix.sysctl` |
| `mysql::db` | `community.mysql.mysql_db` |
| `notify` (resource) | `ansible.builtin.debug` |
| ERB templates | Jinja2 (variables, loops, conditionals, Ruby method → filter) |
| Hiera `lookup()` / `hiera()` | Resolved at conversion time; unresolved → Ansible var |
| `params.pp` pattern | `defaults/main.yml` |
| `notify` / `subscribe` | `notify:` + handler |

---

## What needs manual work

- **Exported resources** (`@@resource`, `<<| |>>`) — no Ansible equivalent; p2a generates a TODO task with suggestions
- **Custom resource types / providers** (`lib/puppet/type/`) — rewrite as Python Ansible modules
- **Custom facts** (`lib/facter/`) — rewrite as Ansible custom facts (`facts.d/`)
- **Class inheritance** (`inherits`) — p2a warns and includes the parent; vars need manual merge
- **Regex node definitions** — inventory entries must be added manually
- **`concat`** — requires `ansible.builtin.assemble`; review fragment ordering after conversion

Anything p2a can't convert produces a `# TODO` task with the original Puppet code. The output is always valid YAML.

---

## Puppet 3 support

Pass `--puppet-version 3` for legacy codebases that use `$::osfamily`, `hiera()`, `hiera_array()`, `import`, and the `params.pp` pattern:

```bash
p2a convert-all /etc/puppet/ --puppet-version 3 -o output/
```

---

## Parser resilience

p2a is designed to handle real-world Puppet code, which is often imperfect:

- **Stray trailing `}`** — manifests with an extra closing brace after the class body are automatically recovered. p2a strips the trailing `}` and re-parses, emitting a warning. The conversion continues normally.
- **Graceful degradation** — any resource or construct that cannot be automatically converted produces a `# TODO` task with the original Puppet code instead of crashing. The output is always syntactically valid YAML.

---

## Development

```bash
# Tests (200 tests)
pytest

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Add a new resource converter
/add-converter
```

---

## License

MIT
