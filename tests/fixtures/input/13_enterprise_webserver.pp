# Level 5 — Enterprise: full web server stack
# Combines: classes, defined types, Hiera lookups, templates, conditionals,
# ordering arrows, notify/subscribe, exported resources, virtual resources

class profile::webserver (
  String           $app_name        = lookup('profile::webserver::app_name'),
  String           $app_version     = lookup('profile::webserver::version', String, 'first', 'latest'),
  Array[String]    $allowed_origins  = lookup('profile::webserver::cors_origins', Array, 'unique', []),
  Hash             $vhosts           = lookup('profile::webserver::vhosts', Hash, 'hash', {}),
  Boolean          $enable_metrics   = lookup('profile::webserver::metrics', Boolean, 'first', false),
  Boolean          $enable_ssl       = true,
  String           $ssl_provider     = 'letsencrypt',
  Integer          $max_connections  = 10000,
) {

  include nginx
  include profile::logging
  include profile::monitoring

  # Resolve package name by OS
  $nginx_pkg = $facts['os']['family'] ? {
    'Debian' => 'nginx',
    'RedHat' => 'nginx',
    default  => 'nginx',
  }

  # Conditional SSL setup
  if $enable_ssl {
    case $ssl_provider {
      'letsencrypt': {
        include certbot
        class { 'certbot::nginx':
          email => lookup('ssl::admin_email'),
        }
      }
      'internal': {
        $ssl_cert = "/etc/ssl/certs/${app_name}.crt"
        $ssl_key  = "/etc/ssl/private/${app_name}.key"
        file { $ssl_cert:
          ensure => file,
          owner  => 'root',
          group  => 'ssl-cert',
          mode   => '0644',
          source => "puppet:///modules/profile/ssl/${app_name}.crt",
        }
        file { $ssl_key:
          ensure => file,
          owner  => 'root',
          group  => 'ssl-cert',
          mode   => '0640',
          source => "puppet:///modules/profile/ssl/${app_name}.key",
        }
      }
      default: {
        fail("Unknown ssl_provider: ${ssl_provider}")
      }
    }
  }

  # Create vhosts from Hiera hash
  create_resources('nginx::vhost', $vhosts)

  # Metrics endpoint
  if $enable_metrics {
    nginx::vhost { 'metrics-internal':
      docroot      => '/var/www/metrics',
      port         => 9145,
      server_names => ['localhost'],
      enabled      => true,
    }
    package { 'prometheus-nginx-exporter':
      ensure => installed,
    }
    service { 'prometheus-nginx-exporter':
      ensure    => running,
      enable    => true,
      subscribe => File['/etc/nginx/nginx.conf'],
      require   => Package['prometheus-nginx-exporter'],
    }
  }

  # Sysctl tuning for high-traffic server
  $sysctl_settings = {
    'net.core.somaxconn'           => 65535,
    'net.ipv4.tcp_max_syn_backlog' => 65535,
    'net.ipv4.ip_local_port_range' => '1024 65535',
    'net.core.netdev_max_backlog'  => 65535,
    'net.ipv4.tcp_fin_timeout'     => 30,
  }

  $sysctl_settings.each |String $key, $value| {
    file_line { "sysctl-${key}":
      path  => '/etc/sysctl.d/99-webserver.conf',
      line  => "${key} = ${value}",
      match => "^${key}",
      notify => Exec['reload-sysctl'],
    }
  }

  exec { 'reload-sysctl':
    command     => '/sbin/sysctl --system',
    refreshonly => true,
  }

  # Log rotation
  file { '/etc/logrotate.d/nginx-app':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('profile/logrotate_nginx.erb'),
  }

  # Security: fail2ban for nginx
  if lookup('security::fail2ban::enabled', Boolean, 'first', false) {
    class { 'fail2ban':
      require => Class['nginx'],
    }
    file { '/etc/fail2ban/jail.d/nginx.conf':
      ensure  => file,
      content => template('profile/fail2ban_nginx.conf.erb'),
      notify  => Service['fail2ban'],
      require => Class['fail2ban'],
    }
  }

  # Exported resource: register this webserver in load balancer
  @@haproxy::balancermember { "webserver-${facts['networking']['fqdn']}":
    listening_service => $app_name,
    server            => $facts['networking']['fqdn'],
    ipaddresses       => $facts['networking']['ip'],
    ports             => '8080',
    options           => 'check inter 2000 rise 2 fall 5',
  }
}
