# Level 4 — Class inheritance, params.pp pattern (Puppet 3 classic)

# params.pp pattern: central defaults class
class myapp::params {
  # OS-specific defaults
  case $::osfamily {
    'Debian': {
      $package_name    = 'myapp'
      $config_dir      = '/etc/myapp'
      $log_dir         = '/var/log/myapp'
      $service_name    = 'myapp'
      $user            = 'myapp'
      $group           = 'myapp'
    }
    'RedHat': {
      $package_name    = 'myapp-server'
      $config_dir      = '/etc/myapp'
      $log_dir         = '/var/log/myapp'
      $service_name    = 'myapp-server'
      $user            = 'myapp'
      $group           = 'myapp'
    }
    default: {
      fail("Unsupported OS family: ${::osfamily}")
    }
  }

  $port    = 8080
  $workers = $::processorcount
  $version = 'latest'
}

# Main class inheriting from params
class myapp (
  $package_name = $myapp::params::package_name,
  $config_dir   = $myapp::params::config_dir,
  $log_dir      = $myapp::params::log_dir,
  $service_name = $myapp::params::service_name,
  $user         = $myapp::params::user,
  $group        = $myapp::params::group,
  $port         = $myapp::params::port,
  $workers      = $myapp::params::workers,
  $version      = $myapp::params::version,
) inherits myapp::params {

  # Hiera lookup (Puppet 3 style)
  $db_host     = hiera('myapp::db_host', 'localhost')
  $db_name     = hiera('myapp::db_name', 'myapp_production')
  $db_password = hiera('myapp::db_password')

  group { $group:
    ensure => present,
  }

  user { $user:
    ensure     => present,
    gid        => $group,
    home       => "/home/${user}",
    shell      => '/bin/bash',
    managehome => true,
    require    => Group[$group],
  }

  package { $package_name:
    ensure => $version,
  }

  file { [$config_dir, $log_dir]:
    ensure  => directory,
    owner   => $user,
    group   => $group,
    mode    => '0755',
    require => User[$user],
  }

  file { "${config_dir}/app.yaml":
    ensure  => file,
    owner   => $user,
    group   => $group,
    mode    => '0640',
    content => template('myapp/app.yaml.erb'),
    require => [Package[$package_name], File[$config_dir]],
    notify  => Service[$service_name],
  }

  service { $service_name:
    ensure  => running,
    enable  => true,
    require => Package[$package_name],
  }
}

include myapp
