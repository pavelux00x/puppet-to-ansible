# Level 2 — exec, user, group, cron resources

user { 'deploy':
  ensure     => present,
  uid        => 1500,
  gid        => 'deploy',
  home       => '/home/deploy',
  shell      => '/bin/bash',
  managehome => true,
  comment    => 'Deployment user',
}

group { 'deploy':
  ensure => present,
  gid    => 1500,
}

exec { 'initialize-app':
  command => '/opt/app/bin/init.sh',
  creates => '/opt/app/.initialized',
  user    => 'deploy',
  require => User['deploy'],
}

exec { 'reload-sysctl':
  command     => '/sbin/sysctl -p',
  refreshonly => true,
}

cron { 'backup-database':
  ensure  => present,
  command => '/usr/local/bin/db-backup.sh',
  user    => 'root',
  hour    => '2',
  minute  => '30',
  weekday => ['1', '2', '3', '4', '5'],
}
