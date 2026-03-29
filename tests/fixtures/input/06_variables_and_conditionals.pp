# Level 3 — Variables, conditionals, facts (Puppet 3 style)

$app_name    = 'myapp'
$app_version = '2.4.1'
$app_port    = 8080
$app_user    = 'deploy'

# Puppet 3 fact access
if $::osfamily == 'Debian' {
  $package_manager = 'apt'
  $service_manager = 'systemd'
  $config_dir      = '/etc/myapp'
} elsif $::osfamily == 'RedHat' {
  $package_manager = 'yum'
  $service_manager = 'systemd'
  $config_dir      = '/etc/myapp'
} else {
  $package_manager = 'package'
  $config_dir      = '/etc/myapp'
}

# Case statement on OS
case $::operatingsystem {
  'Ubuntu', 'Debian': {
    $pkg_name = 'myapp-deb'
  }
  'CentOS', 'RedHat': {
    $pkg_name = 'myapp-rpm'
  }
  default: {
    $pkg_name = 'myapp'
  }
}

package { $pkg_name:
  ensure => $app_version,
}

file { $config_dir:
  ensure => directory,
  owner  => $app_user,
  mode   => '0750',
}

file { "${config_dir}/app.conf":
  ensure  => file,
  content => "port=${app_port}\nversion=${app_version}\n",
  owner   => $app_user,
  mode    => '0640',
  require => File[$config_dir],
}

unless $::is_virtual {
  package { 'smartmontools':
    ensure => installed,
  }
}
