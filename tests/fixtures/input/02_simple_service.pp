# Level 1 — Single resource: service

service { 'nginx':
  ensure => running,
  enable => true,
}
