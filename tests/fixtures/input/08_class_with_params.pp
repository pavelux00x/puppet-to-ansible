# Level 3 — Parameterized class (Puppet 4 style with types)

class nginx (
  String           $package_name    = 'nginx',
  String           $version         = 'installed',
  Boolean          $manage_service  = true,
  Boolean          $service_enable  = true,
  Enum['running', 'stopped'] $service_ensure = 'running',
  String           $config_dir      = '/etc/nginx',
  Integer          $worker_processes = 1,
  Integer          $worker_connections = 1024,
  Array[String]    $extra_modules   = [],
  Hash             $vhosts          = {},
) {

  package { $package_name:
    ensure => $version,
  }

  file { $config_dir:
    ensure  => directory,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    require => Package[$package_name],
  }

  file { "${config_dir}/nginx.conf":
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => template('nginx/nginx.conf.erb'),
    require => File[$config_dir],
    notify  => Service[$package_name],
  }

  if $manage_service {
    service { $package_name:
      ensure  => $service_ensure,
      enable  => $service_enable,
      require => Package[$package_name],
    }
  }

  # Iterate over extra modules
  $extra_modules.each |String $mod| {
    package { "nginx-module-${mod}":
      ensure  => installed,
      require => Package[$package_name],
    }
  }
}

# Usage
class { 'nginx':
  worker_processes   => 4,
  worker_connections => 2048,
  extra_modules      => ['geoip', 'image-filter'],
}
