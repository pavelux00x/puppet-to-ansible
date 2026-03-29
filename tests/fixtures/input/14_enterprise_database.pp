# Level 5 — Enterprise: MySQL/PostgreSQL database server
# Puppet 4+ style with complex Hiera, virtual resources, custom facts

class profile::database (
  Enum['mysql', 'postgresql']  $engine          = lookup('profile::database::engine', Enum['mysql','postgresql'], 'first', 'mysql'),
  String                       $version         = lookup('profile::database::version', String, 'first', 'latest'),
  Integer[512, 65536]          $max_connections = lookup('profile::database::max_connections', Integer, 'first', 151),
  Integer[64, 131072]          $innodb_buffer   = lookup('profile::database::innodb_buffer_mb', Integer, 'first', 128),
  Boolean                      $enable_backups  = true,
  String                       $backup_dir      = '/var/backups/db',
  String                       $backup_hour     = '3',
  Hash[String, Hash]           $databases       = lookup('profile::database::databases', Hash, 'hash', {}),
  Hash[String, Hash]           $db_users        = lookup('profile::database::users', Hash, 'hash', {}),
  Array[String]                $replica_hosts   = lookup('profile::database::replicas', Array, 'unique', []),
) {

  $is_primary = $facts['custom']['db_role'] == 'primary'
  $is_replica = $facts['custom']['db_role'] == 'replica'

  # Engine-specific setup
  case $engine {
    'mysql': {
      $service_name = 'mysql'
      $config_file  = '/etc/mysql/mysql.conf.d/mysqld.cnf'
      $data_dir     = '/var/lib/mysql'

      class { 'mysql::server':
        package_ensure      => $version,
        root_password       => lookup('mysql::root_password'),
        override_options    => {
          mysqld => {
            'max_connections'        => $max_connections,
            'innodb_buffer_pool_size' => "${innodb_buffer}M",
            'slow_query_log'         => 'ON',
            'long_query_time'        => 2,
            'bind-address'           => $is_replica ? { true => '0.0.0.0', default => '127.0.0.1' },
          },
        },
        require => Package['mysql-server'],
      }

      # Create databases from Hiera
      $databases.each |String $db_name, Hash $db_config| {
        mysql::db { $db_name:
          user     => $db_config['user'],
          password => $db_config['password'],
          host     => $db_config.get('host', 'localhost'),
          grant    => $db_config.get('grants', ['ALL']),
          require  => Class['mysql::server'],
        }
      }

      # Replication setup
      if $is_primary and !$replica_hosts.empty {
        mysql_user { 'replication@%':
          ensure        => present,
          password_hash => mysql_password(lookup('mysql::replication_password')),
          require       => Class['mysql::server'],
        }
        mysql_grant { 'replication@%/*.*':
          ensure     => present,
          privileges => ['REPLICATION SLAVE'],
          table      => '*.*',
          user       => 'replication@%',
          require    => Mysql_user['replication@%'],
        }
      }
    }

    'postgresql': {
      $service_name = 'postgresql'
      $config_file  = '/etc/postgresql/14/main/postgresql.conf'
      $data_dir     = '/var/lib/postgresql/14/main'

      class { 'postgresql::server':
        postgres_password  => lookup('postgresql::postgres_password'),
        listen_addresses   => '*',
        max_connections    => $max_connections,
        require            => Package['postgresql-14'],
      }

      $databases.each |String $db_name, Hash $db_config| {
        postgresql::server::db { $db_name:
          user     => $db_config['user'],
          password => $db_config['password'],
          require  => Class['postgresql::server'],
        }
      }
    }

    default: {
      fail("Unsupported database engine: ${engine}")
    }
  }

  # Backup setup (common to both engines)
  if $enable_backups {
    file { $backup_dir:
      ensure => directory,
      owner  => 'root',
      group  => 'root',
      mode   => '0750',
    }

    file { '/usr/local/bin/db-backup.sh':
      ensure  => file,
      owner   => 'root',
      group   => 'root',
      mode    => '0750',
      content => template("profile/db_backup_${engine}.sh.erb"),
    }

    cron { 'database-backup':
      ensure  => present,
      command => '/usr/local/bin/db-backup.sh',
      user    => 'root',
      hour    => $backup_hour,
      minute  => '15',
      require => [File[$backup_dir], File['/usr/local/bin/db-backup.sh']],
    }
  }

  # Monitoring: export check for Nagios/Icinga
  @@nagios_service { "check_${engine}_${facts['networking']['hostname']}":
    check_command       => "check_${engine}",
    host_name           => $facts['networking']['fqdn'],
    service_description => "${engine} status",
    use                 => 'generic-service',
  }

  # Firewall rules for replica access
  $replica_hosts.each |String $replica| {
    firewall { "200 allow ${engine} from ${replica}":
      source => $replica,
      dport  => $engine ? { 'mysql' => 3306, 'postgresql' => 5432 },
      proto  => 'tcp',
      action => 'accept',
    }
  }
}
