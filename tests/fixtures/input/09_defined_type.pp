# Level 4 — Defined type (reusable resource pattern)

define nginx::vhost (
  String           $docroot,
  Integer          $port       = 80,
  Boolean          $ssl        = false,
  Optional[String] $ssl_cert   = undef,
  Optional[String] $ssl_key    = undef,
  Boolean          $enabled    = true,
  Array[String]    $server_names = [$name],
) {

  $config_file = "/etc/nginx/sites-available/${name}.conf"
  $enabled_link = "/etc/nginx/sites-enabled/${name}.conf"

  file { $config_file:
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('nginx/vhost.conf.erb'),
    notify  => Service['nginx'],
  }

  if $enabled {
    file { $enabled_link:
      ensure => link,
      target => $config_file,
    }
  } else {
    file { $enabled_link:
      ensure => absent,
    }
  }

  if $ssl and $ssl_cert == undef {
    fail("nginx::vhost '${name}': ssl_cert is required when ssl => true")
  }
}

# Multiple instances (create_resources pattern)
$vhosts = {
  'myapp' => {
    docroot      => '/var/www/myapp',
    port         => 8080,
    ssl          => true,
    ssl_cert     => '/etc/ssl/certs/myapp.crt',
    ssl_key      => '/etc/ssl/private/myapp.key',
    server_names => ['myapp.example.com', 'www.myapp.example.com'],
  },
  'api' => {
    docroot => '/var/www/api',
    port    => 3000,
  },
}

create_resources('nginx::vhost', $vhosts)
