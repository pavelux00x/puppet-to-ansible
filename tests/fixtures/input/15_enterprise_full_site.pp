# Level 5 — Enterprise: site.pp with node definitions, roles, profiles
# This is the top-level Puppet manifest — the "site.pp"

# ── Node definitions ──────────────────────────────────────────────────────────

# Exact node match
node 'puppet.example.com' {
  include role::puppet_master
}

# Webserver nodes by regex
node /^web\d+\.prod\.example\.com$/ {
  include role::webserver
  include profile::monitoring
  include profile::security::hardening
}

# Staging webservers
node /^web\d+\.staging\.example\.com$/ {
  include role::webserver
  class { 'profile::webserver':
    enable_metrics => true,
    enable_ssl     => false,
  }
}

# Database primary
node 'db01.prod.example.com' {
  include role::database
  class { 'profile::database':
    engine     => 'mysql',
    is_primary => true,
  }
}

# Database replicas
node /^db0[2-9]\.prod\.example\.com$/ {
  include role::database
  class { 'profile::database':
    engine     => 'mysql',
    is_primary => false,
  }
}

# Kubernetes workers
node /^k8s-worker\d+\.example\.com$/ {
  include role::kubernetes_worker
  include profile::container_runtime
}

# Default node (catch-all)
node default {
  include role::base
  include profile::monitoring::agent
}

# ── Role classes ───────────────────────────────────────────────────────────────

class role::base {
  include profile::base
  include profile::security
  include profile::logging
}

class role::webserver {
  include role::base
  include profile::webserver
  include profile::haproxy_member
}

class role::database {
  include role::base
  include profile::database
  include profile::backup
}

class role::kubernetes_worker {
  include role::base
  include profile::kubernetes
  include profile::container_runtime
}

class role::puppet_master {
  include role::base
  include profile::puppet
}

# ── Base profile ───────────────────────────────────────────────────────────────

class profile::base (
  String        $timezone   = lookup('base::timezone', String, 'first', 'UTC'),
  Array[String] $ntp_servers = lookup('base::ntp_servers', Array, 'unique', ['pool.ntp.org']),
  Boolean       $manage_ntp  = true,
  Boolean       $manage_ssh  = true,
  Hash          $ssh_keys    = lookup('base::ssh_authorized_keys', Hash, 'hash', {}),
) {

  # Timezone
  class { 'timezone':
    timezone => $timezone,
  }

  # NTP
  if $manage_ntp {
    package { 'chrony':
      ensure => installed,
    }
    file { '/etc/chrony.conf':
      ensure  => file,
      owner   => 'root',
      group   => 'root',
      mode    => '0644',
      content => template('profile/chrony.conf.erb'),
      require => Package['chrony'],
      notify  => Service['chronyd'],
    }
    service { 'chronyd':
      ensure  => running,
      enable  => true,
      require => Package['chrony'],
    }
  }

  # SSH authorized keys from Hiera
  $ssh_keys.each |String $key_name, Hash $key_data| {
    ssh_authorized_key { $key_name:
      ensure => $key_data.get('ensure', 'present'),
      user   => $key_data['user'],
      type   => $key_data['type'],
      key    => $key_data['key'],
    }
  }

  # Base packages
  $base_packages = lookup('base::packages', Array, 'unique', [
    'vim', 'curl', 'wget', 'git', 'htop', 'tcpdump', 'strace',
  ])
  package { $base_packages:
    ensure => installed,
  }

  # Kernel parameters
  sysctl { 'net.ipv4.tcp_syncookies':
    ensure => present,
    value  => '1',
  }
  sysctl { 'kernel.core_pattern':
    ensure => present,
    value  => '/var/core/%e.%p.%h.%t',
  }
}

# ── Security profile ────────────────────────────────────────────────────────────

class profile::security (
  Boolean       $manage_firewall   = true,
  Boolean       $enable_fail2ban   = lookup('security::fail2ban::enabled', Boolean, 'first', true),
  Array[String] $allowed_ssh_from  = lookup('security::ssh::allowed_from', Array, 'unique', ['10.0.0.0/8']),
  Boolean       $disable_root_ssh  = true,
) {

  if $manage_firewall {
    # Default deny incoming, allow outgoing
    firewall { '000 accept all to lo interface':
      proto   => 'all',
      iniface => 'lo',
      action  => 'accept',
    }
    firewall { '001 accept established connections':
      proto  => 'all',
      state  => ['ESTABLISHED', 'RELATED'],
      action => 'accept',
    }
    firewall { '100 allow SSH from trusted networks':
      dport  => 22,
      proto  => 'tcp',
      source => $allowed_ssh_from,
      action => 'accept',
    }
    firewall { '999 drop all':
      proto  => 'all',
      action => 'drop',
      before => undef,
    }
  }

  if $disable_root_ssh {
    augeas { 'disable-root-ssh':
      context => '/files/etc/ssh/sshd_config',
      changes => ['set PermitRootLogin no'],
      notify  => Service['ssh'],
    }
    service { 'ssh':
      ensure => running,
      enable => true,
    }
  }

  if $enable_fail2ban {
    package { 'fail2ban':
      ensure => installed,
    }
    service { 'fail2ban':
      ensure  => running,
      enable  => true,
      require => Package['fail2ban'],
    }
  }
}
