# Level 1 — Single resource: package
# The simplest possible Puppet manifest

package { 'nginx':
  ensure => installed,
}
