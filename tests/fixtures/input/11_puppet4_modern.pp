# Level 4 — Modern Puppet 4+ features

class webstack (
  String[1]                    $app_name,
  Array[String[1], 1]          $packages,
  Hash[String, Integer]        $ports,
  Optional[String]             $ssl_cert    = undef,
  Boolean                      $manage_firewall = true,
  Variant[String, Integer]     $timeout     = 30,
) {

  # Puppet 4 fact access
  $os_name    = $facts['os']['name']
  $os_family  = $facts['os']['family']
  $os_release = $facts['os']['release']['major']

  # Puppet 4 lookup (replaces hiera())
  $db_config = lookup('webstack::database', Hash, 'hash', {})

  # Each with lambda
  $packages.each |String $pkg| {
    package { $pkg:
      ensure => installed,
    }
  }

  # Hash iteration
  $ports.each |String $service, Integer $port| {
    firewall { "100 allow ${service}":
      dport  => $port,
      proto  => 'tcp',
      action => 'accept',
    }
  }

  # Conditional with Puppet 4 facts
  if $os_family == 'Debian' {
    $firewall_pkg = 'ufw'
  } elsif $os_family == 'RedHat' and Integer($os_release) >= 7 {
    $firewall_pkg = 'firewalld'
  } else {
    $firewall_pkg = 'iptables'
  }

  if $manage_firewall {
    package { $firewall_pkg:
      ensure => installed,
    }
  }

  # Unless + selector
  $log_level = $facts['virtual'] ? {
    'physical' => 'warn',
    default    => 'debug',
  }

  file { "/etc/${app_name}/logging.conf":
    ensure  => file,
    content => "level=${log_level}\n",
  }

  # Chained resources
  Package[$packages] -> File["/etc/${app_name}"] ~> Service[$app_name]
}

class { 'webstack':
  app_name  => 'myplatform',
  packages  => ['python3', 'python3-pip', 'gunicorn'],
  ports     => { 'http' => 80, 'https' => 443, 'app' => 8080 },
  ssl_cert  => '/etc/ssl/certs/platform.crt',
}
