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

"""
debian/ubuntu network helper module
"""

import logging
import os
import subprocess
import time
from cStringIO import StringIO

import commands.network

HOSTNAME_FILE = "/etc/hostname"
INTERFACE_FILE = "/etc/network/interfaces"

INTERFACE_HEADER = \
"""
# Used by ifup(8) and ifdown(8). See the interfaces(5) manpage or
# /usr/share/doc/ifupdown/examples for more information.
# The loopback network interface
auto lo
iface lo inet loopback
""".lstrip('\n')

INTERFACE_LABELS = {"public": "eth0",
                    "private": "eth1"}


def configure_network(network_config):

    # Generate new interface files
    interfaces = network_config.get('interfaces', [])

    data = _get_file_data(interfaces)
    update_files = {INTERFACE_FILE: data}

    # Generate new hostname file
    hostname = network_config.get('hostname')

    data = get_hostname_file(hostname)
    update_files[HOSTNAME_FILE] = data

    # Generate new /etc/hosts file
    filepath, data = commands.network.get_etc_hosts(interfaces, hostname)
    update_files[filepath] = data

    # Write out new files
    commands.network.update_files(update_files)

    # Set hostname
    logging.debug('executing /bin/hostname %s' % hostname)
    p = subprocess.Popen(["/bin/hostname", hostname])
    logging.debug('waiting on pid %d' % p.pid)
    status = os.waitpid(p.pid, 0)[1]
    logging.debug('status = %d' % status)

    if status != 0:
        return (500, "Couldn't set hostname: %d" % status)

    # Restart network
    logging.debug('executing /etc/init.d/networking restart')
    p = subprocess.Popen(["/etc/init.d/networking", "restart"])
    logging.debug('waiting on pid %d' % p.pid)
    status = os.waitpid(p.pid, 0)[1]
    logging.debug('status = %d' % status)

    if status != 0:
        return (500, "Couldn't restart network: %d" % status)

    return (0, "")


def get_hostname_file(hostname):
    return hostname + '\n'


def _get_file_data(interfaces):
    """
    Return interfaces file data in 1 long string
    """

    file_data = INTERFACE_HEADER

    for interface in interfaces:
        try:
            label = interface['label']
        except KeyError:
            raise SystemError("No interface label found")

        try:
            ifname_prefix = INTERFACE_LABELS[label]
        except KeyError:
            raise SystemError("Invalid label '%s'" % label)

        try:
            ips = interface['ips']
        except KeyError:
            raise SystemError("No IPs found for interface")

        ip6s = interface.get('ip6s', [])

        try:
            mac = interface['mac']
        except KeyError:
            raise SystemError("No mac address found for interface")

        try:
            routes = interface['routes']
        except KeyError:
            routes = []

        if label == "public":
            gateway4 = interface.get('gateway')
            gateway6 = interface.get('gateway6')
            if not gateway4 and not gateway6:
                raise SystemError("No gateway found for public interface")

            try:
                dns = interface['dns']
            except KeyError:
                raise SystemError("No DNS found for public interface")

        ifname_suffix_num = 0

        for i in xrange(max(len(ips), len(ip6s))):
            if ifname_suffix_num:
                ifname = "%s:%d" % (ifname_prefix, ifname_suffix_num)
            else:
                ifname = ifname_prefix

            if i < len(ips):
                ip_info = ips[i]
            else:
                ip_info = None

            if i < len(ip6s):
                ip6_info = ip6s[i]
            else:
                ip6_info = None

            if not ip_info and not ip6_info:
                continue

            file_data += "\n"
            file_data += "auto %s\n" % ifname

            if ip_info and ip_info.get('enabled', '0') != '0':
                try:
                    ip = ip_info['ip']
                    netmask = ip_info['netmask']
                except KeyError:
                    raise SystemError(
                            "Missing IP or netmask in interface's IP list")

                file_data += "iface %s inet static\n" % ifname
                file_data += "    address %s\n" % ip
                file_data += "    netmask %s\n" % netmask
                if label == "public":
                    file_data += "    gateway %s\n" % gateway4
                    if dns:
                        file_data += "    dns-nameservers %s\n" % ' '.join(dns)
                        dns = None

            if ip6_info and ip6_info.get('enabled', '0') != '0':
                print repr(ip6_info)
                try:
                    ip = ip6_info['address']
                    netmask = ip6_info['netmask']
                except KeyError:
                    raise SystemError(
                            "Missing IP or netmask in interface's IPv6 list")

                gateway = ip6_info.get('gateway', gateway6)

                file_data += "iface %s inet6 static\n" % ifname
                file_data += "    address %s\n" % ip
                file_data += "    netmask %s\n" % netmask
                if gateway:
                    file_data += "    gateway %s\n" % gateway
                    if dns:
                        file_data += "    dns-nameservers %s\n" % ' '.join(dns)
                        dns = None

            ifname_suffix_num += 1

        for route in routes:
            network = route['route']
            netmask = route['netmask']
            gateway = route['gateway']

            file_data += "up route add -net %s netmask %s gw %s\n" % (
                    network, netmask, gateway)
            file_data += "down route del -net %s netmask %s gw %s\n" % (
                    network, netmask, gateway)

    return file_data


def get_interface_files(interfaces):
    return {'interfaces': _get_file_data(interfaces)}
