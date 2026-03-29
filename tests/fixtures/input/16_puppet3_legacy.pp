# Level 3 — Puppet 3 legacy syntax
# Tests: $::facts, hiera(), import, resource defaults, virtual resources

import 'nodes/*.pp'

# Resource defaults (Puppet 3 style)
Package {
  provider => 'yum',
}

File {
  owner => 'root',
  group => 'root',
}

# Virtual resource
@user { 'johndoe':
  ensure => present,
  uid    => 2001,
  groups => ['developers'],
  shell  => '/bin/bash',
}

# Realize the virtual user
realize User['johndoe']

# Old-style fact access
$os     = $::operatingsystem
$arch   = $::architecture
$mem_mb = $::memorysize_mb
$cpu    = $::processorcount

# Old-style hiera
$java_version = hiera('java::version', '11')
$heap_size    = hiera_array('java::heap_flags', ['-Xms512m', '-Xmx2g'])
$jvm_opts     = hiera_hash('java::jvm_options', {})

package { 'java-1.8.0-openjdk':
  ensure => $java_version,
}

# Selector expression (Puppet 3)
$jdk_pkg = $::osfamily ? {
  'Debian' => "openjdk-${java_version}-jdk",
  'RedHat' => "java-${java_version}-openjdk-devel",
  default  => "java-${java_version}-openjdk",
}

package { $jdk_pkg:
  ensure  => installed,
  require => Package['java-1.8.0-openjdk'],
}

# Chained ordering arrows
Package[$jdk_pkg]
  -> File['/etc/java']
  -> File['/etc/java/jvm.conf']
  ~> Service['tomcat']

file { '/etc/java':
  ensure => directory,
}

file { '/etc/java/jvm.conf':
  ensure  => file,
  mode    => '0644',
  content => inline_template("<% @heap_size.each do |flag| -%>\n<%= flag %>\n<% end -%>"),
}

service { 'tomcat':
  ensure => running,
  enable => true,
}

# Old-style node-level class inclusion
class { 'ntp':
  servers => ['0.pool.ntp.org', '1.pool.ntp.org'],
}
