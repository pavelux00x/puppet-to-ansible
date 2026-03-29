# Level 1 — File resources: copy, directory, symlink, absent

file { '/etc/motd':
  ensure  => file,
  content => "Welcome to the server.\n",
  owner   => 'root',
  group   => 'root',
  mode    => '0644',
}

file { '/var/app/data':
  ensure => directory,
  owner  => 'app',
  group  => 'app',
  mode   => '0755',
}

file { '/usr/local/bin/myapp':
  ensure => link,
  target => '/opt/myapp/bin/myapp',
}

file { '/tmp/oldconfig':
  ensure => absent,
}
