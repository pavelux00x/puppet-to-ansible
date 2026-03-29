# Level 4 — Template references, augeas, ini_setting, file_line

# ERB template reference
file { '/etc/ssh/sshd_config':
  ensure  => file,
  owner   => 'root',
  group   => 'root',
  mode    => '0600',
  content => template('sshd/sshd_config.erb'),
  notify  => Service['ssh'],
}

service { 'ssh':
  ensure => running,
  enable => true,
}

# Augeas — sshd_config tuning
augeas { 'harden-sshd':
  context => '/files/etc/ssh/sshd_config',
  changes => [
    'set PermitRootLogin no',
    'set PasswordAuthentication no',
    'set X11Forwarding no',
    'set MaxAuthTries 3',
  ],
  notify  => Service['ssh'],
}

# Augeas — INI-style file
augeas { 'configure-pam':
  context => '/files/etc/pam.d/common-auth',
  changes => [
    'set *[type="auth"][module="pam_unix.so"]/argument[.="nullok"] nonnull',
  ],
}

# ini_setting
ini_setting { 'java-heap':
  ensure  => present,
  path    => '/etc/java/jvm.conf',
  section => 'memory',
  setting => 'heap_size',
  value   => '2g',
}

# file_line
file_line { 'enable-ip-forwarding':
  path  => '/etc/sysctl.conf',
  line  => 'net.ipv4.ip_forward = 1',
  match => '^net\.ipv4\.ip_forward',
}

file_line { 'add-hosts-entry':
  path => '/etc/hosts',
  line => '10.0.0.10 internal.example.com',
}
