# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#  Copyright (c) 2011 Openstack, LLC.
#  All Rights Reserved.
#
#     Licensed under the Apache License, Version 2.0 (the "License"); you may
#     not use this file except in compliance with the License. You may obtain
#     a copy of the License at
#
#          http://www.apache.org/licenses/LICENSE-2.0
#
#     Unless required by applicable law or agreed to in writing, software
#     distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#     WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#     License for the specific language governing permissions and limitations
#     under the License.
#

"""Gentoo Network Helper Module

Configures Gentoo's networking configuration according to upstream
specifications found here:
http://www.gentoo.org/doc/en/handbook/handbook-x86.xml?part=4

The basic parts to this configuration are the following files:

    * /etc/conf.d/net - The addressing and routing specification
    * /etc/hosts - The local host resolution specification
    * /etc/conf.d/hostname - The system hostname specification
    * /etc/resolv.conf - The hostname resolver specification

The network addressing and routing configuration has a simple but powerful
syntax.  The following is an example of this syntax:

    config_eth0="192.168.0.100/24"
    routes_eth0="default via 192.168.0.1"

Multiple IPs and routes can be specified for a particular interface.  The
following is an example of this syntax:

    config_eth0="192.168.0.100/24 192.168.0.101/24"
    routes_eth0="
      default via 192.168.0.1
      172.100.100.0/24 via 192.168.0.1
    "

This allows for cool things (not used here) like the following:

config_eth0="$(for i in $(seq 100 200); do echo -n 192.168.0.${i}/24' '; done)"

"""

import sys
import os
import subprocess
import logging
import re

from datetime import datetime

import commands.network
import commands.utils

HOSTNAME_FILE = "/etc/conf.d/hostname"
NETWORK_FILE = "/etc/conf.d/net"


def configure_network(hostname, interfaces):
    update_files = {}

    # Generate new conf.d/net file
    if os.path.isfile('/sbin/rc'):
        data, ifaces = _confd_net_file(interfaces)
    else:
        data, ifaces = _confd_net_file_legacy(interfaces)

    update_files[NETWORK_FILE] = data

    # Generate new resolv.conf file
    filepath, data = commands.network.get_resolv_conf(interfaces)
    if data:
        update_files[filepath] = data

    # Generate new hostname file
    data = get_hostname_file(hostname)
    update_files[HOSTNAME_FILE] = data

    # Generate new /etc/hosts file
    filepath, data = commands.network.get_etc_hosts(interfaces, hostname)
    update_files[filepath] = data

    # Write out new files
    commands.network.update_files(update_files)

    pipe = subprocess.PIPE

    # Set hostname
    try:
        commands.network.sethostname(hostname)
    except Exception as e:
        logging.error("Couldn't sethostname(): %s" % str(e))
        return (500, "Couldn't set hostname: %s" % str(e))

    # Restart network
    for ifname in ifaces:
        if commands.utils.is_system_command("ip"):
            if not _clean_assigned_ip(ifname):
                return (500, "Couldn't flush network %s: %d" %
                        (ifname, status))
        else:
            logging.warning("Couldn't flush old network configuration as" +
                          " safeguard. Required 'ip' command not present.")

        scriptpath = '/etc/init.d/net.%s' % ifname
        if not os.path.exists(scriptpath):
            # Gentoo won't create these symlinks automatically
            os.symlink('net.lo', scriptpath)

        logging.debug('executing %s restart' % scriptpath)
        script_proc = subprocess.Popen([scriptpath, 'restart'],
                            stdin=pipe, stdout=pipe, stderr=pipe, env={})
        logging.debug('waiting on pid %d' % script_proc.pid)
        status = os.waitpid(script_proc.pid, 0)[1]
        logging.debug('status = %d' % status)


        if status != 0:
            return (500, "Couldn't restart network %s: %d" % (ifname, status))

    return (0, "")


def get_hostname():
    """
    Will fetch current hostname of VM if any and return.
    Looks at /etc/conf.d/hostname config for Gentoo server.
    """
    try:
        with open(HOSTNAME_FILE) as hostname_fyl:
            for line in hostname_fyl.readlines():
                hn = re.search('HOSTNAME="(.*)"', line)
                if hn:
                    return hn.group(1)
        return None

    except Exception, e:
        logging.info("Current Gentoo hostname enquiry failed: %s" % str(e))
        return None


def get_hostname_file(hostname):
    """Given the new hostname creates the hostname configuration content.

    This will generate the /etc/conf.d/hostname configuration file for a Gentoo
    server running this agent.

    """

    lines = ["# Set to the hostname of this machine"]
    lines.append(_header())
    lines.append("HOSTNAME=\"{0}\"".format(hostname))

    return "\n".join(lines)


def _header():
    """Provides a generic textwrapped header for autogenerated files."""

    return """# Creator: NOVA AGENT:
        # This file was autogenerated at {time} by {comm}.
        # While it can still be managed manually, definitely not recommended.
        """.format(comm=sys.argv[0], time=datetime.now())


def _confd_net_file(interfaces):
    """Given the interfaces creates the network configuration content.

    This will generate the /etc/conf.d/net configuration file for a Gentoo
    server running this agent.

    """

    ifaces = set()

    lines = []
    lines.append(_header())
    lines.append("")
    if commands.utils.is_system_command("ip"):
        lines.append('modules="iproute2"')
    else:
        lines.append('modules="ifconfig"')
    lines.append("")
    lines.append("")

    for name, interface in interfaces.iteritems():
        if interface['label']:
            lines.append("# Label %s" % interface['label'])
        lines.append("config_{0}=\"".format(name))
        lines.extend(["  {0}/{1}".format(ip['address'],
                        commands.network.NETMASK_TO_PREFIXLEN[ip['netmask']]
                    ) for ip in interface['ip4s'] ])
        lines.extend([ "  {0}/{1}".format(ip['address'], ip['prefixlen']
                    ) for ip in interface['ip6s'] ])
        lines.append("\"")
        lines.append("")

        lines.append("routes_{0}=\"".format(name))
        lines.extend([ "  {0}/{1} via {2}".format(route['network'],
                        commands.network.NETMASK_TO_PREFIXLEN[route['netmask']],
                        route['gateway']
                    ) for route in interface['routes'] if not
                    route['network'] == '0.0.0.0' and not
                    route['netmask'] == '0.0.0.0' and
                    'gateway4' in interface and not
                    route['gateway'] == interface['gateway4']])
        if 'gateway4' in interface and interface['gateway4']:
            lines.append("  default via {0}".format(interface['gateway4']))
        if 'gateway6' in interface and interface['gateway6']:
            lines.append("  default via {0}".format(interface['gateway6']))
        lines.append("\"")
        lines.append("")

        dns = interface['dns']
        if dns:
            lines.append('dns_servers_{0}="{1}"\n'.format(name,
                                                          '\n'.join(dns)))
            lines.append("")

        ifaces.add(name)

    return "\n".join(lines), ifaces


def _confd_net_file_legacy(interfaces):
    """
    Return data for (sub-)interfaces and routes
    """

    ifaces = set()

    lines = []
    lines.append(_header())
    lines.append("")
    if commands.utils.is_system_command("ip"):
        lines.append('modules="iproute2"')
    else:
        lines.append('modules="ifconfig"')
    lines.append("")
    lines.append("")

    for name, interface in interfaces.iteritems():
        if interface['label']:
            lines.append("# Label %s" % interface['label'])
        lines.append("config_{0}=(".format(name))

        lines.extend(["  \"{0} netmask {1}\"".format(ip['address'],
                    ip['netmask']) for ip in interface['ip4s'] ])
        lines.extend([ "  \"{0}/{1}\"".format(ip['address'], ip['prefixlen']
                    ) for ip in interface['ip6s'] ])
        lines.append(")")
        lines.append("")

        lines.append("routes_{0}=(".format(name))
        lines.extend([ "  \"{0} netmask {1} gw {2}\"".format(
                        route['network'], route['netmask'], route['gateway']
                    ) for route in interface['routes'] if not
                    route['network'] == '0.0.0.0' and not
                    route['netmask'] == '0.0.0.0' and
                    'gateway4' in interface and not
                    route['gateway'] == interface['gateway4'] ])
        if 'gateway4' in interface and interface['gateway4']:
            lines.append("  \"default via {0}\"".format(interface['gateway4']))
        if 'gateway6' in interface and interface['gateway6']:
            lines.append("  \"default via {0}\"".format(interface['gateway6']))
        lines.append(")")
        lines.append("")

        dns_list = interface['dns']
        if dns_list:
            lines.append("dns_servers_{0}=(".format(name))
            lines.extend([' "{0}"'.format(dns) for dns in dns_list])
            lines.append(")")
            lines.append("")

        ifaces.add(name)

    return "\n".join(lines), ifaces


def get_interface_files(interfaces, version):
    if version == 'openrc':
        data, ifaces = _confd_net_file(interfaces)
    else:
        data, ifaces = _confd_net_file_legacy(interfaces)

    return {'net': data}


def _clean_assigned_ip(ifname):
    pipe = subprocess.PIPE
    logging.debug("cleaning up current ip assigned to %s" % ifname)
    ip_proc = subprocess.Popen(["ip", "address", "flush", "dev", ifname],
                                stdin=pipe, stdout=pipe, stderr=pipe, env={})
    logging.debug('waiting on pid %d' % ip_proc.pid)
    status = os.waitpid(ip_proc.pid, 0)[1]
    logging.debug('status = %d' % status)

    if status != 0:
        return False
    return True
