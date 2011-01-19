#!/usr/bin/env python
#
# ovirt-config-setup.py - Copyright (C) 2010 Red Hat, Inc.
# Written by Joey Boggs <jboggs@redhat.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston,
# MA  02110-1301, USA.  A copy of the GNU General Public License is
# also available at http://www.gnu.org/copyleft/gpl.html.

from snack import *
import _snack
import os
import traceback
import fcntl
import libvirt
import PAM
import gudev
import cracklib
from ovirtnode.ovirtfunctions import *
from ovirtnode.collectd import *
from ovirtnode.password import *
from ovirtnode.logging import *
from ovirtnode.network import *
from ovirtnode.kdump import *
from ovirtnode.iscsi import *
from ovirtnode.logging import *

OK_BUTTON = "OK"
BACK_BUTTON = "Back"
RESET_BUTTON = "Reset"
CANCEL_BUTTON = "Cancel"
APPLY_BUTTON = "Apply"
IDENTIFY_BUTTON = "Identify NIC"
LOCK_BUTTON = "Lock"
RESTART_BUTTON = "Restart"
POWER_OFF_BUTTON = "Power Off"
LOGIN_BUTTON = "Login"
login_password = ""

STATUS_PAGE = 1
NETWORK_PAGE = 3
AUTHENTICATION_PAGE = 5
MONITORING_PAGE = 7
LOGGING_PAGE = 9
KDUMP_PAGE = 11
REMOTE_STORAGE_PAGE = 13
NETWORK_DETAILS_PAGE = 19
LOCKED_PAGE = 99

OVIRT_VARS = parse_defaults()

def pam_conv(auth, query_list):
    global login_password
    resp = []
    for i in range(len(query_list)):
        resp.append((login_password, 0))
    return resp

class NodeConfigScreen():
      """
      This example presents an application that looks like
      an appliance more than an application.
      """

      def __init__(self):
            self.__colorset = {
                        "ROOT"           : ("gray",  "magenta"),
                        "BORDER"         : ("magenta", "magenta"),
                        "WINDOW"         : ("magenta", "magenta"),
                         "ACTBUTTON"     : ("blue",  "white"),
                         "BUTTON"        : ("blue",  "white"),
                         "COMPACTBUTTON" : ("black", "magenta"),
                         "LISTBOX"       : ("green",  "red"),
                         "ACTLISTBOX"    : ("cyan", "red"),
                         "ACTSELLISTBOX" : ("blue",  "white"), 
                         "TEXTBOX"       : ("cyan",  "magenta"),
                         "ENTRY"         : ("cyan", "magenta"),
                         "SHADOW"        : ("magenta",  "magenta"),
                         "LABEL"         : ("brown",  "magenta"),
                         "TITLE"         : ("white",  "blue"),
                         "HELPLINE"      : ("cyan",  "magenta"),
                         "EMPTYSCALE"    : ("cyan",  "magenta"),
                         "CHECKBOX"      : ("black",  "red"),
                         "ACTCHECKBOX"   : ("blue", "white")
                         }
            self.__current_page = 1
            self.__finished = False
            self.__nic_config_failed = 0
      def set_console_colors(self):
          GIO_CMAP = 0x4B70
          PIO_CMAP = 0x4B71
          tty_file = open("/dev/console", "rw")
          self.existing_color_array = bytearray(fcntl.ioctl(tty_file.fileno(), GIO_CMAP, b"\x00" * 48))
          color_array = self.existing_color_array
          color_array[3] = 0xde
          color_array[4] = 0xde
          color_array[5] = 0xde
          color_array[6] = 0x30
          color_array[7] = 0x30
          color_array[8] = 0x30
          color_array[9] = 0x52
          color_array[10] = 0x52
          color_array[11] = 0x52
          color_array[12] = 0x38
          color_array[13] = 0x8f
          color_array[14] = 0xcd
          color_array[15] = 0xea
          color_array[16] = 0xea
          color_array[17] = 0xea
          color_array[18] = 0x71
          color_array[19] = 0x71
          color_array[20] = 0x71
          color_array[21] = 0xff
          color_array[22] = 0xff
          color_array[23] = 0xff
          fcntl.ioctl(tty_file.fileno(), PIO_CMAP, bytes(color_array))

      def restore_console_colors(self):
          GIO_CMAP = 0x4B70
          PIO_CMAP = 0x4B71
          tty_file = open("/dev/console", "rw")
          fcntl.ioctl(tty_file.fileno(), PIO_CMAP, bytes(self.existing_color_array))

      def get_elements_for_page(self, screen, page):
            if page is 1:
                return self.status_page(screen)
            if page is 3:
                return self.network_configuration_page(screen)
            if page is 5:
                return self.authentication_configuration_page(screen)
            if page is 7:
                return self.monitoring_configuration_page(screen)
            if page is 9:
                return self.logging_configuration_page(screen)
            if page is 11:
                return self.kdump_configuration_page(screen)
            if page is 13:
                return self.remote_storage_configuration_page(screen)
            if page is 19:
                return self.network_details_page(screen)
            if page is 99:
                return self.screen_locked_page(screen)

      def network_proto_Callback(self):
          return

      def nic_lb_callback(self):
         try:
             get_ip_address(self.nic_lb.current())
             self.nic_disabled.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_RESET)
         except:
             self.nic_disabled.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)
         return

      def nic_dhcp_callback(self):
          if self.nic_dhcp.value() == 1:
              flag = _snack.FLAGS_SET
          else:
              flag = _snack.FLAGS_RESET
          for i in self.netdevip, self.netdevmask, self.netdevgateway:
              i.setFlags(_snack.FLAG_DISABLED, flag)
          return

      def dns_host1_callback(self):
          warn = 0
          if not self.dns_host1.value() is None and not self.dns_host1.value() == "":
               if not is_valid_ipv4(self.dns_host1.value()):
                   warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP Address", buttons = ['Ok'])
              self.dns_host1.set("")
          return

      def dns_host2_callback(self):
          warn = 0
          if not self.dns_host2.value() is None and not self.dns_host2.value() == "":
              if not is_valid_ipv4(self.dns_host2.value()):
                   warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP Address", buttons = ['Ok'])
              self.dns_host2.set("")
          return

      def ipv4_ip_callback(self):
          warn = 0
          if not self.ipv4_netdevip.value() is None and not self.ipv4_netdevip.value() == "":
               if not is_valid_ipv4(self.ipv4_netdevip.value()):
                   warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP Address", buttons = ['Ok'])
              self.ipv4_netdevip.set("")
          return

      def ipv4_netmask_callback(self):
          warn = 0
          if not self.ipv4_netdevmask.value() is None and not self.ipv4_netdevmask.value() == "":
               if not is_valid_ipv4(self.ipv4_netdevmask.value()):
                   warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP Address", buttons = ['Ok'])
              self.ipv4_netdevmask.set("")
          return

      def ipv4_gateway_callback(self):
          warn = 0
          if not self.ipv4_netdevgateway.value() is None and not self.ipv4_netdevgateway.value() == "":
               if not is_valid_ipv4(self.ipv4_netdevgateway.value()):
                   warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP Address", buttons = ['Ok'])
              self.ipv4_netdevgateway.set("")
          return

      def ipv4_disabled_callback(self):
          if self.disabled_ipv4_nic_proto.value() == 1:
              flag = _snack.FLAGS_SET
              for i in self.ipv4_netdevip, self.ipv4_netdevmask, self.ipv4_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.dhcp_ipv4_nic_proto.setValue(" 0")
                  self.static_ipv4_nic_proto.setValue(" 0")

      def ipv4_dhcp_callback(self):
          if self.dhcp_ipv4_nic_proto.value() == 1:
              flag = _snack.FLAGS_SET
              for i in self.ipv4_netdevip, self.ipv4_netdevmask, self.ipv4_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.disabled_ipv4_nic_proto.setValue(" 0")
                  self.static_ipv4_nic_proto.setValue(" 0")

      def ipv4_static_callback(self):
          if self.static_ipv4_nic_proto.value() == 1:
              flag = _snack.FLAGS_RESET
              for i in self.ipv4_netdevip, self.ipv4_netdevmask, self.ipv4_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.disabled_ipv4_nic_proto.setValue(" 0")
                  self.dhcp_ipv4_nic_proto.setValue(" 0")

      def ipv6_disabled_callback(self):
          if self.disabled_ipv6_nic_proto.value() == 1:
              flag = _snack.FLAGS_SET
              for i in self.ipv6_netdevip, self.ipv6_netdevmask, self.ipv6_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.dhcp_ipv6_nic_proto.setValue(" 0")
                  self.static_ipv6_nic_proto.setValue(" 0")
                  self.auto_ipv6_nic_proto.setValue(" 0")

      def ipv6_dhcp_callback(self):
          if self.dhcp_ipv6_nic_proto.value() == 1:
              flag = _snack.FLAGS_SET
              for i in self.ipv6_netdevip, self.ipv6_netdevmask, self.ipv6_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.disabled_ipv6_nic_proto.setValue(" 0")
                  self.static_ipv6_nic_proto.setValue(" 0")
                  self.auto_ipv6_nic_proto.setValue(" 0")

      def ipv6_static_callback(self):
          if self.static_ipv6_nic_proto.value() == 1:
              flag = _snack.FLAGS_RESET
              for i in self.ipv6_netdevip, self.ipv6_netdevmask, self.ipv6_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.disabled_ipv6_nic_proto.setValue(" 0")
                  self.dhcp_ipv6_nic_proto.setValue(" 0")
                  self.auto_ipv6_nic_proto.setValue(" 0")

      def ipv6_auto_callback(self):
          if self.auto_ipv6_nic_proto.value() == 1:
              flag = _snack.FLAGS_SET
              for i in self.ipv6_netdevip, self.ipv6_netdevmask, self.ipv6_netdevgateway:
                  i.setFlags(_snack.FLAG_DISABLED, flag)
                  self.disabled_ipv6_nic_proto.setValue(" 0")
                  self.dhcp_ipv6_nic_proto.setValue(" 0")
                  self.static_ipv6_nic_proto.setValue(" 0")
      def password_check_callback(self):
          if self.root_password_1.value() != "" and self.root_password_2.value() != "":
              if self.root_password_1.value() != self.root_password_2.value():
                  self.screen.setColor("BUTTON", "black", "red")
                  self.screen.setColor("ACTBUTTON", "blue", "white")
                  ButtonChoiceWindow(self.screen, "Password Check", "Passwords Do Not Match", buttons = ['Ok'])
                  return
              try:
                  cracklib.FascistCheck(self.root_password_1.value())
              except ValueError, e:
                  self.screen.setColor("BUTTON", "black", "red")
                  self.screen.setColor("ACTBUTTON", "blue", "white")
                  ButtonChoiceWindow(self.screen, "Password Check", "You have provided a weak password", buttons = ['Ok'])
          elif self.root_password_1.value() != "" and self.root_password_2.value() == "":
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Password Check", "Please Confirm Password", buttons = ['Ok'])
          return

      def valid_syslog_port_callback(self):
          if not is_valid_port(self.syslog_port.value()):
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Configuration Check", "Invalid Port Number", buttons = ['Ok'])

      def valid_syslog_server_callback(self):
          if not is_valid_host_or_ip(self.syslog_server.value()):
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Configuration Check", "Invalid Hostname or Address", buttons = ['Ok'])

      def valid_collectd_server_callback(self):
          if not is_valid_host_or_ip(self.collectd_server.value()):
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Configuration Check", "Invalid Hostname or Address", buttons = ['Ok'])

      def valid_collectd_port_callback(self):
          if not is_valid_port(self.collectd_port.value()):
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Configuration Check", "Invalid Port Number", buttons = ['Ok'])

      def kdump_nfs_callback(self):
          self.kdump_ssh_type.setValue(" 0")
          self.kdump_restore_type.setValue(" 0")
          self.kdump_nfs_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_RESET)
          self.kdump_ssh_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)

      def kdump_ssh_callback(self):
          self.kdump_nfs_type.setValue(" 0")
          self.kdump_restore_type.setValue(" 0")
          self.kdump_nfs_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)
          self.kdump_ssh_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_RESET)

      def kdump_restore_callback(self):
          self.kdump_ssh_type.setValue(" 0")
          self.kdump_nfs_type.setValue(" 0")
          self.kdump_nfs_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)
          self.kdump_ssh_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)

      def valid_fqdn_or_ipv4(self):
          warn = 0
          if not self.ntp_host1.value() == "":
               if not is_valid_ipv4(self.ntp_host1.value()):
                   if not is_valid_hostname(self.ntp_host1.value()):
                       warn = 1
          if not self.ntp_host2.value() == "":
               if not is_valid_ipv4(self.ntp_host2.value()):
                   if not is_valid_hostname(self.ntp_host2.value()):
                       warn = 1
          if warn == 1:
              self.screen.setColor("BUTTON", "black", "red")
              self.screen.setColor("ACTBUTTON", "blue", "white")
              ButtonChoiceWindow(self.screen, "Network", "Invalid IP/Hostname", buttons = ['Ok'])
          return

      def screen_locked_page(self, screen):
            self.screen_locked = True
            elements = Grid(1, 3)
            pw_elements = Grid(2, 2)
            elements.setField(Label("Unlock " + os.uname()[1]), 0, 0, padding=(13,1,0,1))
            self.login_username = Entry(15, "")
            self.login_password = Entry(15, "", password = 1)
            pw_elements.setField(Label("Login: "), 0, 0, padding=(13,1,0,1))
            pw_elements.setField(self.login_username, 1, 0)
            pw_elements.setField(Label("Password: "), 0, 1, padding=(13,0,0,1))
            pw_elements.setField(self.login_password, 1, 1)
            elements.setField(pw_elements, 0, 1)
            return [Label(""), elements]

      def status_page(self, screen):
            elements = Grid(2, 10)
            elements.setField(Label("Hostname:"), 0, 2, anchorLeft = 1)
            hostname = Textbox(30, 1, os.uname()[1])
            elements.setField(hostname, 1, 2, anchorLeft = 1, padding=(4, 0, 0, 1))
            if network_up():
                self.network_status = {}
                status_text = ""
                client = gudev.Client(['net'])
                for nic in client.query_by_subsystem("net"):
                    try:
                        interface = nic.get_property("INTERFACE")
                        log(interface)
                        if not interface == "lo":
                            if has_ip_address(interface) or get_ipv6_address(interface):
                                ipv4_address = get_ip_address(interface)
                                if get_ipv6_address(interface):
                                    ipv6_address = get_ipv6_address(interface)
                                else:
                                    ipv6_address = ""
                                self.network_status[interface] = (ipv4_address,ipv6_address)
                    except:
                        pass
                # remove parent/bridge duplicates
                for key in sorted(self.network_status.iterkeys()):
                    if key.startswith("br"):
                        parent_dev = key[+2:]
                        del self.network_status[parent_dev]
                for key in sorted(self.network_status.iterkeys()):
                    ipv4_addr, ipv6_addr = self.network_status[key]
                    cmd = "/files/etc/sysconfig/network-scripts/ifcfg-%s/BOOTPROTO" % str(key)
                    dev_bootproto = augtool_get(cmd)
                    if dev_bootproto is None:
                      cmd = "/files/etc/sysconfig/network-scripts/ifcfg-br%s/BOOTPROTO" % str(key)
                      dev_bootproto = augtool_get(cmd)
                      if dev_bootproto is None:
                          dev_bootproto = "Disabled"
                    if ipv6_addr != "":
                        status_text += "%1s: %5s %14s \nIPv6: %1s\n\n" % (key.strip(),dev_bootproto.strip(),ipv4_addr.strip(),ipv6_addr.strip())
                    else:
                        status_text += "%1s: %5s %14s \n\n" % (key.strip(),dev_bootproto.strip(),ipv4_addr.strip(),ipv6_addr.strip())
                    status_text.strip()
                    networking = TextboxReflowed(32, status_text, maxHeight=10)
                    networking.setText(status_text)
                log(status_text)
                log(self.network_status)
            else:
                networking = Textbox(25, 1, "Not Connected")
            elements.setField(Label("Networking:"), 0, 3, anchorLeft = 1, anchorTop = 1)
            elements.setField(networking, 1, 3, anchorLeft = 1, padding=(4, 0, 0, 1))
            logging_status = Textbox(18, 1, "local only")
            elements.setField(Label("Logs and Reporting:"), 0, 5, anchorLeft = 1)
            elements.setField(logging_status, 1, 5, anchorLeft = 1, padding=(4, 0, 0, 1))
            try:
                conn = libvirt.openReadOnly(None)
                self.dom_count = conn.numOfDomains()
            except:
                self.dom_count = "Failed to connect"
            self.jobs_status = Textbox(18, 1, str(self.dom_count))
            elements.setField(Label("Running VMs:"), 0, 6, anchorLeft = 1)
            elements.setField(self.jobs_status, 1, 6, anchorLeft = 1, padding=(4, 0, 0, 1))
            return [Label(""), elements]

      def monitoring_configuration_page(self, screen):
            elements = Grid(2, 10)
            elements.setField(Label("Monitoring Configuration"), 0, 0, anchorLeft = 1)
            elements.setField(Label(""), 0, 1, anchorLeft = 1)
            elements.setField(Label("Collectd"), 0, 2, anchorLeft = 1)
            elements.setField(Textbox(45,3,"Collectd gathers statistics about the system that\ncan be used to find performance bottlenecks\nand predict future system load."), 0, 3, anchorLeft = 1)
            collectd_grid = Grid(2,2)
            collectd_grid.setField(Label("Server Address:"), 0, 0, anchorLeft = 1)
            self.collectd_server = Entry(20, "")
            self.collectd_server.setCallback(self.valid_collectd_server_callback)
            collectd_grid.setField(self.collectd_server, 1, 0, anchorLeft = 1, padding=(2, 0, 0, 1))
            self.collectd_port = Entry(5, "")
            self.collectd_port.setCallback(self.valid_collectd_port_callback)
            collectd_grid.setField(Label("Server Port:"), 0, 1, anchorLeft = 1)
            collectd_grid.setField(self.collectd_port, 1, 1, anchorLeft = 1, padding=(2, 0, 0, 1))
            elements.setField(collectd_grid, 0, 4, anchorLeft = 1, padding = (0,1,0,0))
            collectd_config = get_collectd_config()
            if not collectd_config is None:
                collectd_server, collectd_port = get_collectd_config()
                self.collectd_server.set(collectd_server)
                self.collectd_port.set(collectd_port)
            else:
                self.collectd_port.set("7634")
            return [Label(""), elements]


      def logging_configuration_page(self, screen):
          elements = Grid(2, 8)
          elements.setField(Label("Logging Configuration"), 0, 0, anchorLeft = 1)
          elements.setField(Label(" "), 0, 1, anchorLeft = 1)
          elements.setField(Label("Rsyslog"), 0, 2, anchorLeft = 1)
          elements.setField(Textbox(45,3,"Rsyslog is an enhanced multi-threaded syslogd\nwith a focus on security and reliability."), 0, 3, anchorLeft = 1)
          rsyslog_grid = Grid(2,2)
          rsyslog_grid.setField(Label("Server Address:"), 0, 0, anchorLeft = 1)
          self.syslog_server = Entry(20, "")
          self.syslog_server.setCallback(self.valid_syslog_server_callback)
          rsyslog_grid.setField(self.syslog_server, 1, 0, anchorLeft = 1, padding=(0, 0, 0, 1))
          self.syslog_port = Entry(6, "", scroll = 0)
          self.syslog_port.setCallback(self.valid_syslog_port_callback)
          rsyslog_grid.setField(Label("Server Port:"), 0, 1, anchorLeft = 1, padding=(0, 0, 0, 1))
          rsyslog_grid.setField(self.syslog_port, 1, 1, anchorLeft = 1)
          rsyslog_config = get_rsyslog_config()
          log(rsyslog_config)
          if not rsyslog_config is None:
              rsyslog_server, rsyslog_port = rsyslog_config
              self.syslog_server.set(rsyslog_server)
              self.syslog_port.set(rsyslog_port)
          else:
              self.syslog_port.set("514")
          elements.setField(rsyslog_grid, 0, 4, anchorLeft = 1)
          return [Label(""), elements]


      def authentication_configuration_page(self, screen):
          elements = Grid(2, 9)
          elements.setField(Label("Remote Access"), 0, 0, anchorLeft = 1)
          pw_elements = Grid (3,3)
          self.current_ssh_pwd_status = augtool_get("/files/etc/ssh/sshd_config/PasswordAuthentication")
          if self.current_ssh_pwd_status == "yes":
              self.current_ssh_pwd_status = 1
          else:
              self.current_ssh_pwd_status = 0
          self.ssh_passwd_status = Checkbox("Enable ssh password authentication", isOn=self.current_ssh_pwd_status)
          elements.setField(self.ssh_passwd_status, 0, 1, anchorLeft = 1)
          elements.setField(Label(""), 0, 2, anchorLeft = 1)
          elements.setField(Label("Local Access"), 0, 3, anchorLeft = 1)
          elements.setField(Label(" "), 0, 6)
          pw_elements.setField(Label("Password: "), 0, 1, anchorLeft = 1)
          pw_elements.setField(Label("Confirm Password: "), 0, 2, anchorLeft = 1)
          self.root_password_1 = Entry(15,password = 1)
          self.root_password_1.setCallback(self.password_check_callback)
          self.root_password_2 = Entry(15,password = 1)
          self.root_password_2.setCallback(self.password_check_callback)
          pw_elements.setField(self.root_password_1, 1,1)
          pw_elements.setField(self.root_password_2, 1,2)
          state = _snack.FLAGS_SET
          elements.setField(pw_elements, 0, 7)
          return [Label(""), elements]

      def network_configuration_page(self, screen):
          grid = Grid(2,15)
          grid.setField(Label("System Identification"), 0, 1, anchorLeft = 1)
          hostname_grid = Grid(2,2)
          hostname_grid.setField(Label("Hostname: "), 0, 1, anchorLeft = 1, padding=(0,0,2,0))
          self.current_hostname = os.uname()[1]
          hostname = os.uname()[1]
          self.net_hostname = Entry(35, hostname)
          hostname_grid.setField(self.net_hostname, 1, 1, anchorLeft = 1, padding=(0,0,2,0))
          grid.setField(hostname_grid, 0, 3)
          dns_grid = Grid(2,2)
          self.dns_host1 = Entry(25)
          self.dns_host1.setCallback(self.dns_host1_callback)
          self.current_dns_host1 = augtool_get("/files/etc/resolv.conf/nameserver[1]")
          if self.current_dns_host1:
              self.dns_host1.set(self.current_dns_host1)
          else:
              self.dns_host1.set("")
          self.dns_host2 = Entry(25)
          self.dns_host2.setCallback(self.dns_host2_callback)
          self.current_dns_host2 = augtool_get("/files/etc/resolv.conf/nameserver[2]")
          if self.current_dns_host2:
              self.dns_host2.set(self.current_dns_host2)
          else:
              self.dns_host2.set("")
          dns_grid.setField(Label("DNS Server 1: "), 0, 0, anchorLeft = 1)
          dns_grid.setField(Label("DNS Server 2: "), 0, 1, anchorLeft = 1)
          dns_grid.setField(self.dns_host1, 1, 0, anchorLeft = 1)
          dns_grid.setField(self.dns_host2, 1, 1, anchorLeft = 1)
          grid.setField(Label("  "), 0, 4)
          grid.setField(dns_grid, 0, 6, anchorLeft =1)
          grid.setField(Label("  "), 0, 7)
          ntp_grid = Grid(2,2)
          self.ntp_host1 = Entry(25)
          self.ntp_host1.setCallback(self.valid_fqdn_or_ipv4)
          self.ntp_host2 = Entry(25)
          self.ntp_host2.setCallback(self.valid_fqdn_or_ipv4)
          self.current_ntp_host1 = augtool_get("/files/etc/ntp.conf/server[1]")
          if self.current_ntp_host1:
              self.ntp_host1.set(self.current_ntp_host1)
          self.current_ntp_host2 = augtool_get("/files/etc/ntp.conf/server[2]")
          if self.current_ntp_host2:
              self.ntp_host2.set(self.current_ntp_host2)
          ntp_grid.setField(Label("NTP Server 1: "), 0, 0, anchorLeft = 1)
          ntp_grid.setField(Label("NTP Server 2: "), 0, 1, anchorLeft = 1)
          ntp_grid.setField(self.ntp_host1, 1, 0, anchorLeft = 1)
          ntp_grid.setField(self.ntp_host2, 1, 1, anchorLeft = 1)
          grid.setField(Label("  "), 0, 10)
          grid.setField(ntp_grid, 0, 9, anchorLeft =1)

          self.nic_lb = Listbox(height = 5, width = 56, returnExit = 1, scroll = 1)
          self.nic_dict = {}
          client = gudev.Client(['net'])
          self.configured_nics = 0
          for device in client.query_by_subsystem("net"):
              try:
                  dev_interface = device.get_property("INTERFACE")
                  dev_vendor = device.get_property("ID_VENDOR_FROM_DATABASE")
                  try:
                      dev_vendor = dev_vendor.replace(",", "")
                  except AttributeError:
                      dev_vendor = "unknown"
                  to_rem = len(dev_vendor) - 25
                  # if negative pad name space
                  if to_rem < 0:
                      while abs(to_rem) != 0:
                          dev_vendor = dev_vendor + " "
                          to_rem = to_rem + 1
                  else:
                      dev_vendor = dev_vendor.rstrip(dev_vendor[-to_rem:])
                  dev_driver = os.readlink("/sys/class/net/" + dev_interface + "/device/driver")
                  dev_driver = os.path.basename(dev_driver)
                  nic_addr_file = open("/sys/class/net/" + dev_interface + "/address")
                  dev_address = nic_addr_file.read().strip()
                  cmd = "/files/etc/sysconfig/network-scripts/ifcfg-%s/BOOTPROTO" % str(dev_interface)
                  dev_bootproto = augtool_get(cmd)
                  if dev_bootproto is None:
                      cmd = "/files/etc/sysconfig/network-scripts/ifcfg-br%s/BOOTPROTO" % str(dev_interface)
                      dev_bootproto = augtool_get(cmd)
                      if dev_bootproto is None:
                          dev_bootproto = "Disabled"
                          dev_conf_status = "Unconfigured"
                      else:
                          dev_conf_status = "Configured"
                  else:
                      dev_conf_status = "Configured"
                  if dev_conf_status == "Configured":
                      self.configured_nics = self.configured_nics + 1
              except:
                  pass
              if not dev_interface == "lo" and not dev_interface.startswith("br") and not dev_interface.startswith("bond") and not dev_interface.startswith("sit"):
                  self.nic_dict[dev_interface] = "%s,%s,%s,%s,%s,%s" % (dev_interface,dev_bootproto,dev_vendor,dev_address, dev_driver, dev_conf_status)
          for key in sorted(self.nic_dict.iterkeys()):
              dev_interface,dev_bootproto,dev_vendor,dev_address,dev_driver,dev_conf_status = self.nic_dict[key].split(",", 5)
              to_rem = len(dev_vendor) - 10
              # if negative pad name space
              if to_rem < 1:
                  while abs(to_rem) != 0:
                      dev_vendor += " "
                      to_rem = to_rem + 1
              else:
                  dev_vendor = dev_vendor.rstrip(dev_vendor[-to_rem:])
              if len(dev_interface) > 6:
                  to_rem = len(dev_interface) - 6
                  dev_interface = dev_interface.lstrip(dev_interface[:to_rem])
              else:
                  to_rem = len(dev_interface) - 6
                  # if negative pad name space
                  if to_rem < 0:
                      while abs(to_rem) != 0:
                          dev_interface = dev_interface + " "
                          to_rem = to_rem + 1

              nic_option = '%2s %13s %12s %19s\n' % (dev_interface,dev_conf_status,dev_vendor,dev_address)
              self.nic_lb.append(nic_option, dev_interface.strip())
          NIC_LABEL = Label("Device     Status      Model       MAC Address")
          grid.setField(NIC_LABEL, 0, 11, (0, 0, 0, 0), anchorLeft = 1)
          grid.setField(self.nic_lb, 0, 12)
          return [Label(""),
                  grid]

      def network_details_page(self,screen):
          grid = Grid(1,15)
          link_status_cmd = "ethtool %s|grep \"Link detected\"" % self.nic_lb.current()
          link_status = subprocess.Popen(link_status_cmd, shell=True, stdout=PIPE, stderr=STDOUT)
          link_status = link_status.stdout.read()
          if "yes" in link_status:
              link_status = "Active"
          else:
              link_status = "Inactive"
          interface,bootproto,vendor,address,driver,conf_status = self.nic_dict[self.nic_lb.current()].split(",", 5)
          nic_driver_grid = Grid(2,2)
          nic_driver_grid.setField(Label("Driver: "), 0, 0, anchorLeft = 1)
          nic_driver_grid.setField(Label(driver), 1, 0, anchorLeft = 1)
          nic_detail_grid = Grid(6, 10)
          nic_detail_grid.setField(Label("Interface:   "), 0, 1, anchorLeft = 1)
          nic_detail_grid.setField(Label("Vendor:      "), 0, 2, anchorLeft = 1)
          nic_detail_grid.setField(Label("MAC Address: "), 0, 3, anchorLeft = 1)
          nic_detail_grid.setField(nic_driver_grid, 3, 1, anchorLeft = 1)
          nic_detail_grid.setField(Label("Protocol:    "), 3, 2, anchorLeft = 1)
          nic_detail_grid.setField(Label("Link Status: "), 3, 3, anchorLeft = 1)
          nic_detail_grid.setField(Label(interface), 1, 1, anchorLeft = 1)
          nic_detail_grid.setField(Label(vendor), 1, 2, anchorLeft = 1)
          nic_detail_grid.setField(Label(address), 1, 3, anchorLeft = 1)
          nic_detail_grid.setField(Label(" "), 2, 1, anchorLeft = 1)
          nic_detail_grid.setField(Label(" "), 2, 2, anchorLeft = 1)
          nic_detail_grid.setField(Label("  "), 2, 3, anchorLeft = 1)
          nic_detail_grid.setField(Label(bootproto), 4, 2, anchorLeft = 1)
          nic_detail_grid.setField(Label(link_status), 4, 3, anchorLeft = 1)
          grid.setField(nic_detail_grid, 0, 1)
          ipv4_main_grid = Grid(6,8)
          self.disabled_ipv4_nic_proto = Checkbox("Disabled ")
          self.disabled_ipv4_nic_proto.setCallback(self.ipv4_disabled_callback)
          self.dhcp_ipv4_nic_proto = Checkbox("DHCP ")
          self.dhcp_ipv4_nic_proto.setCallback(self.ipv4_dhcp_callback)
          self.static_ipv4_nic_proto = Checkbox("Static ")
          self.static_ipv4_nic_proto.setCallback(self.ipv4_static_callback)
          if bootproto.lower() == "dhcp":
              self.dhcp_ipv4_nic_proto.setValue("*")
          elif bootproto.lower() == "static":
              self.static_ipv4_nic_proto.setValue("*")
          else:
              self.disabled_ipv4_nic_proto.setValue("*")
          ipv4_proto_grid = Grid(6,1)
          ipv4_proto_grid.setField(self.disabled_ipv4_nic_proto, 0, 0, anchorLeft = 1)
          ipv4_proto_grid.setField(self.dhcp_ipv4_nic_proto, 2, 0, anchorLeft = 1)
          ipv4_proto_grid.setField(self.static_ipv4_nic_proto, 4, 0, anchorLeft = 1)
          ipv4_main_grid.setField(Label("IPv4 Settings"), 0, 0, anchorLeft = 1)
          ipv4_main_grid.setField(ipv4_proto_grid, 0, 2, anchorLeft = 1)
          self.ipv4_netdevip = Entry(16, "", scroll = 0)
          self.ipv4_netdevip.setCallback(self.ipv4_ip_callback)
          self.ipv4_netdevmask = Entry(16, "", scroll = 0)
          self.ipv4_netdevmask.setCallback(self.ipv4_netmask_callback)
          self.ipv4_netdevgateway = Entry(16, "", scroll = 0)
          self.ipv4_netdevgateway.setCallback(self.ipv4_gateway_callback)
          if "OVIRT_IP_ADDRESS" in OVIRT_VARS:
              self.ipv4_netdevip.set(OVIRT_VARS["OVIRT_IP_ADDRESS"])
          else:
              current_ip = get_ip_address(self.nic_lb.current())
              if current_ip == "":
                  current_ip = get_ip_address("br" + self.nic_lb.current())
              if current_ip != "":
                  self.ipv4_netdevip.set(current_ip)
          if "OVIRT_IP_NETMASK" in OVIRT_VARS:
              self.ipv4_netdevmask.set(OVIRT_VARS["OVIRT_IP_NETMASK"])
          else:
              current_netmask = get_netmask(self.nic_lb.current())
              if current_netmask == "":
                  current_netmask = get_netmask("br" + self.nic_lb.current())
              if current_ip != "":
                  self.ipv4_netdevmask.set(current_netmask)
          if "OVIRT_IP_GATEWAY" in OVIRT_VARS:
              self.ipv4_netdevgateway.set(OVIRT_VARS["OVIRT_IP_GATEWAY"])
          else:
              current_gateway = get_gateway(self.nic_lb.current())
              if current_gateway == "":
                  current_gateway = get_netmask("br" + self.nic_lb.current())
              if current_gateway != "":
                  self.ipv4_netdevgateway.set(current_gateway)
          ipv4_grid = Grid (5,3)
          ipv4_grid.setField(Label("IP Address: "), 0, 1, anchorLeft = 1)
          ipv4_grid.setField(Label(" Netmask: "), 3, 1, anchorLeft = 1)
          ipv4_grid.setField(Label("Gateway:"), 0, 2, anchorLeft = 1)
          ipv4_grid.setField(self.ipv4_netdevip, 2, 1)
          ipv4_grid.setField(self.ipv4_netdevmask, 4, 1)
          ipv4_grid.setField(self.ipv4_netdevgateway, 2, 2)
          ipv4_main_grid.setField(ipv4_grid, 0,3)
          if self.dhcp_ipv4_nic_proto.value() == 1:
              self.ipv4_dhcp_callback()
          elif self.static_ipv4_nic_proto.value() == 1:
              self.ipv4_static_callback()
          else:
              self.ipv4_disabled_callback()
          # prepopulate current values only in case of missing values
          if self.__nic_config_failed == 1:
              self.ipv4_netdevip.set(self.ipv4_current_netdevip)
              self.ipv4_netdevmask.set(self.ipv4_current_netdevmask)
              self.ipv4_netdevgateway.set(self.ipv4_current_netdevgateway)
              self.static_ipv4_nic_proto.setValue("*")
              self.ipv4_static_callback()
          # ipv6 grids
          ipv6_main_grid = Grid(6,8)
          self.disabled_ipv6_nic_proto = Checkbox("Disabled ")
          self.disabled_ipv6_nic_proto.setCallback(self.ipv6_disabled_callback)
          self.dhcp_ipv6_nic_proto = Checkbox("DHCP ")
          self.dhcp_ipv6_nic_proto.setCallback(self.ipv6_dhcp_callback)
          self.static_ipv6_nic_proto = Checkbox("Static ")
          self.static_ipv6_nic_proto.setCallback(self.ipv6_static_callback)
          self.auto_ipv6_nic_proto = Checkbox("Auto")
          self.auto_ipv6_nic_proto.setCallback(self.ipv6_auto_callback)
          ipv6_autoconf_lookup_cmd = "/files/etc/sysconfig/network-scripts/ifcfg-%s/IPV6_AUTOCONF" % self.nic_lb.current()
          ipv6_autoconf = augtool_get(ipv6_autoconf_lookup_cmd)
          if ipv6_autoconf is None:
              ipv6_autoconf_lookup_cmd = "/files/etc/sysconfig/network-scripts/ifcfg-br%s/IPV6_AUTOCONF" % self.nic_lb.current()
              ipv6_autoconf = augtool_get(ipv6_autoconf_lookup_cmd)
          ipv6_dhcp_lookup_cmd = "/files/etc/sysconfig/network-scripts/ifcfg-%s/DHCPV6C" % self.nic_lb.current()
          ipv6_dhcp = augtool_get(ipv6_dhcp_lookup_cmd)
          if ipv6_dhcp is None:
              ipv6_dhcp_lookup_cmd = "/files/etc/sysconfig/network-scripts/ifcfg-br%s/DHCPV6C" % self.nic_lb.current()
              ipv6_dhcp = augtool_get(ipv6_dhcp_lookup_cmd)
          ipv6_bootproto = ""
          if ipv6_autoconf == "yes":
              ipv6_bootproto = "auto"
          if ipv6_dhcp == "yes":
              ipv6_bootproto = "dhcp"
          if ipv6_bootproto == "dhcp":
              self.dhcp_ipv6_nic_proto.setValue("*")
          elif ipv6_bootproto == "auto":
              self.auto_ipv6_nic_proto.setValue("*")
          else:
              self.disabled_ipv6_nic_proto.setValue("*")
          ipv6_proto_grid = Grid(6,1)
          ipv6_proto_grid.setField(self.disabled_ipv6_nic_proto, 0, 0, anchorLeft = 1)
          ipv6_proto_grid.setField(self.dhcp_ipv6_nic_proto, 1, 0, anchorLeft = 1)
          ipv6_proto_grid.setField(self.static_ipv6_nic_proto, 2, 0, anchorLeft = 1)
          ipv6_proto_grid.setField(self.auto_ipv6_nic_proto, 3, 0, anchorLeft = 1)
          ipv6_main_grid.setField(Label("IPv6 Settings"), 0, 0, anchorLeft = 1)
          ipv6_main_grid.setField(ipv6_proto_grid, 0, 2, anchorLeft = 1)
          self.ipv6_netdevip = Entry(15, "", scroll = 0)
          self.ipv6_netdevmask = Entry(15, "", scroll = 0)
          self.ipv6_netdevgateway = Entry(15, "", scroll = 0)
          ipv6_grid = Grid (5,3)
          ipv6_grid.setField(Label("IP Address: "), 0, 1, anchorLeft = 1)
          ipv6_grid.setField(Label(" Netmask: "), 3, 1, anchorLeft = 1)
          ipv6_grid.setField(Label("Gateway:"), 0, 2, anchorLeft = 1)
          ipv6_grid.setField(self.ipv6_netdevip, 2, 1)
          ipv6_grid.setField(self.ipv6_netdevmask, 4, 1)
          ipv6_grid.setField(self.ipv6_netdevgateway, 2, 2)
          ipv6_main_grid.setField(ipv6_grid, 0,3)
          if self.dhcp_ipv6_nic_proto.value() == 1:
              self.ipv6_dhcp_callback()
          elif self.static_ipv6_nic_proto.value() == 1:
              self.ipv6_static_callback()
          else:
              self.ipv6_disabled_callback()
          grid.setField(Label(" "), 0, 4, anchorLeft = 1)
          grid.setField(ipv4_main_grid, 0, 5, anchorLeft = 1)
          grid.setField(Label(" "), 0, 6, anchorLeft = 1)
          grid.setField(ipv6_main_grid, 0, 7, anchorLeft = 1)
          grid.setField(Label(" "), 0, 8, anchorLeft = 1)
          vlan_grid = Grid(2,2)
          self.netvlanid = Entry(4, "", scroll = 0)
          if "OVIRT_VLAN" in OVIRT_VARS:
              self.netvlanid.set(OVIRT_VARS["OVIRT_VLAN"])
          vlan_grid.setField(Label("VLAN ID: "), 0, 0, anchorLeft = 1)
          vlan_grid.setField(self.netvlanid, 1, 0)
          grid.setField(vlan_grid, 0, 9, anchorLeft = 1)
          return [Label(""),
                  grid]
      def kdump_configuration_page(self, screen):
          elements = Grid(2, 12)
          elements.setField(Label("KDump Configuration"), 0, 0, anchorLeft = 1)
          elements.setField(Label(" "), 0, 1, anchorLeft = 1)
          kdump_type_grid = Grid(5, 2)
          self.kdump_nfs_type = Checkbox("NFS ")
          self.kdump_nfs_type.setCallback(self.kdump_nfs_callback)
          self.kdump_ssh_type = Checkbox("SSH ")
          self.kdump_ssh_type.setCallback(self.kdump_ssh_callback)
          self.kdump_restore_type = Checkbox("Restore (Local)")
          self.kdump_restore_type.setCallback(self.kdump_restore_callback)
          kdump_type_grid.setField(self.kdump_nfs_type, 0, 0, anchorLeft = 1)
          kdump_type_grid.setField(self.kdump_ssh_type, 1, 0, anchorLeft = 1)
          kdump_type_grid.setField(self.kdump_restore_type, 2, 0, anchorLeft = 1)
          elements.setField(kdump_type_grid, 0, 2, anchorLeft = 1)
          elements.setField(Label(" "), 0, 3, anchorLeft = 1)
          elements.setField(Label("NFS Location (example.redhat.com:/var/crash):"), 0, 4, anchorLeft = 1)
          self.kdump_nfs_config = Entry(30, "")
          elements.setField(self.kdump_nfs_config, 0, 5, anchorLeft = 1)
          elements.setField(Label(" "), 0, 6, anchorLeft = 1)
          elements.setField(Label("SSH Location (root@example.redhat.com)"), 0, 7, anchorLeft = 1)
          self.kdump_ssh_config = Entry(30, "")
          elements.setField(self.kdump_ssh_config, 0, 8, anchorLeft = 1)
          try:
              kdump_config_file = open("/etc/kdump.conf")
              for line in kdump_config_file:
                  if "net" in line:
                      line = line.replace("net ", "")
                      if "@" in line:
                          self.kdump_ssh_type.setValue("*")
                          self.kdump_ssh_config.set(line)
                          self.kdump_nfs_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)

                      elif ":" in line:
                          self.kdump_nfs_type.setValue("*")
                          self.kdump_nfs_config.set(line)
                          self.kdump_ssh_config.setFlags(_snack.FLAG_DISABLED, _snack.FLAGS_SET)

                  elif "/dev/HostVG/Data" in line:
                      self.kdump_restore_type.setValue("*")
          except:
              pass
          return [Label(""), elements]

      def remote_storage_configuration_page(self, screen):
          elements = Grid(2, 8)
          elements.setField(Label("Remote Storage Configuration"), 0, 0, anchorLeft = 1)
          elements.setField(Label(" "), 0, 1, anchorLeft = 1)
          elements.setField(Label("Iscsi Initiator Name:"), 0, 2, anchorLeft = 1)
          self.iscsi_initiator_config = Entry(50, "")
          elements.setField(self.iscsi_initiator_config, 0, 3, anchorLeft = 1)
          current_iscsi_initiator_name = get_current_iscsi_initiator_name()
          if current_iscsi_initiator_name is not None:
              self.iscsi_initiator_config.set(current_iscsi_initiator_name)
          return [Label(""), elements]

      def menuSpacing(self):
          menu_option = self.menu_list.current()
          if self.menuo < menu_option:
              if menu_option == 2:
                  self.menu_list.setCurrent(3)
                  self.menuo = 3
              if menu_option == 4:
                  self.menu_list.setCurrent(5)
                  self.menuo = 5
              if menu_option == 6:
                  self.menu_list.setCurrent(7)
                  self.menuo = 7
              if menu_option == 8:
                  self.menu_list.setCurrent(9)
                  self.menuo = 9
              if menu_option == 10:
                  self.menu_list.setCurrent(11)
                  self.menuo = 11
              if menu_option == 12:
                  self.menu_list.setCurrent(13)
                  self.menuo = 13
              if menu_option == 14:
                  self.menu_list.setCurrent(13)
                  self.menuo = 13
          elif self.menuo > self.menu_list.current():
              if menu_option == 14:
                  self.menu_list.setCurrent(13)
                  self.menuo = 13
              if menu_option == 12:
                  self.menu_list.setCurrent(11)
                  self.menuo = 11
              if menu_option == 10:
                  self.menu_list.setCurrent(9)
                  self.menuo = 9
              if menu_option == 8:
                  self.menu_list.setCurrent(7)
                  self.menuo = 7
              if menu_option == 6:
                  self.menu_list.setCurrent(5)
                  self.menuo = 5
              if menu_option == 4:
                  self.menu_list.setCurrent(3)
                  self.menuo = 3
              if menu_option == 2:
                  self.menu_list.setCurrent(1)
                  self.menuo = 1

      def process_network_config(self):
          if self.net_hostname.value() != self.current_hostname:
              augtool(set, "/file/etc/sysconfig/network", self.net_hostname.value())
              os.system("hostname " + self.net_hostname.value())
          dns_servers = ""
          ntp_servers = ""
          if not self.dns_host1.value() == "":
              dns_servers += self.dns_host1.value()
          if not self.dns_host2.value() == "":
              dns_servers += "," + self.dns_host2.value()
          if not self.ntp_host1.value() == "":
              ntp_servers += self.ntp_host1.value()
          if not self.ntp_host2.value() == "":
              ntp_servers += "," + self.ntp_host2.value()
          if not dns_servers == "":
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_DNS", '"' + dns_servers + '"')
          if not ntp_servers == "":
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_NTP", '"' + ntp_servers + '"')
          network = Network()
          if len(dns_servers) > 0:
              network.configure_dns()
          if len(ntp_servers) > 0:
              network.configure_ntp()
              network.save_ntp_configuration()
          return

      def process_nic_config(self):
          augtool("rm", "/files/" + OVIRT_DEFAULTS + "/OVIRT_BOOTIF", "")
          augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_BOOTIF", '"' + self.nic_lb.current() + '"')
          if self.static_ipv4_nic_proto.value() == 1:
              msg = ""
              if self.ipv4_netdevip.value() == "":
                  msg = "  - IP Address\n"
              if self.ipv4_netdevmask.value() == "":
                  msg += "  - Netmask Address\n"
              # left out gateway check to prevent multiple ones
              if msg != "":
                  msg = "Please Input:\n" + msg
                  warn = ButtonChoiceWindow(self.screen, "Network Settings", msg, buttons = ['Ok'])
                  self.__nic_config_failed = 1
                  self.ipv4_current_netdevip = self.ipv4_netdevip.value()
                  self.ipv4_current_netdevmask = self.ipv4_netdevmask.value()
                  self.ipv4_current_netdevgateway = self.ipv4_netdevgateway.value()
                  return
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_IP_ADDRESS", '"' + self.ipv4_netdevip.value() + '"')
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_IP_GATEWAY", '"' + self.ipv4_netdevmask.value() + '"')
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_IP_NETMASK", '"' + self.ipv4_netdevgateway.value() + '"')
          if self.netvlanid.value() != "":
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_VLAN", '"' + self.netvlanid.value() + '"')
          if self.dhcp_ipv6_nic_proto.value() == 1:
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_IPV6", '"' + "dhcp" + '"')
          if self.auto_ipv6_nic_proto.value() == 1:
              augtool("set", "/files/" + OVIRT_DEFAULTS + "/OVIRT_IPV6", '"' + "auto" + '"')

          self.screen = SnackScreen()
          # apply any colorsets that were provided.
          for item in self.__colorset.keys():
              colors = self.__colorset.get(item)
              self.screen.setColor(item, colors[0], colors[1])
          self.screen.pushHelpLine(" ")
          self.screen.refresh()
          self.screen.setColor("BUTTON", "black", "red")
          self.screen.setColor("ACTBUTTON", "blue", "white")
          warn = ButtonChoiceWindow(self.screen, "Confirm Network Settings", "Network Configuration may take a few moments, proceed?")
          if warn == "ok":
              self.set_console_colors()
              self.screen.refresh()
              network = Network()
              gridform = GridForm(self.screen, "", 2, 2)
              gridform.add(Label("Configuring Networking"), 0, 0)
              progress_bar = Scale(50,100)
              gridform.add(progress_bar, 0, 1)
              gridform.draw()
              self.screen.refresh()
              network.configure_interface()
              self.screen.popWindow()
              gridform = GridForm(self.screen, "", 2, 2)
              gridform.add(Label("Enabling Network Configuration"), 0, 0)
              progress_bar = Scale(50,100)
              gridform.add(progress_bar, 0, 1)
              progress_bar.set(75)
              gridform.draw()
              self.screen.refresh()
              network.save_network_configuration()
              self.screen.popWindow()
              return

      def process_collectd_config(self):
          self.screen.setColor("BUTTON", "black", "red")
          self.screen.setColor("ACTBUTTON", "blue", "white")
          if not self.collectd_server.value() is "" and not self.collectd_port.value() is "":
              if ovirt_collectd(self.collectd_server.value(), self.collectd_port.value()):
                  ButtonChoiceWindow(self.screen, "Collectd Configuration", "Collectd Configuration Successfully Changed", buttons = ['Ok'])
                  return True
              else:
                  ButtonChoiceWindow(self.screen, "Collectd Configuration", "Collectd Configuration Failed", buttons = ['Ok'])
                  return False

      def process_authentication_config(self):
          self.screen.setColor("BUTTON", "black", "red")
          self.screen.setColor("ACTBUTTON", "blue", "white")
          set_password(self.root_password_1.value(), "root")
          set_password(self.root_password_1.value(), "admin")
          ButtonChoiceWindow(self.screen, "Remote Access", "Password Successfully Changed", buttons = ['Ok'])
          log("\nroot & admin password changed")
          return True

      def process_logging_config(self):
          if not self.syslog_server.value() is "" and not self.syslog_port.value() is "":
              ovirt_rsyslog(self.syslog_server.value(), self.syslog_port.value(), "udp")
          return True

      def process_monitoring_config(self):
          if not self.collectd_server.value() is "" and not self.collectd_port.value() is "":
              write_collectd_config(self.collectd_server.value(), self.collectd_port.value())
          return True

      def process_locked_screen(self):
          auth = PAM.pam()
          auth.start("passwd")
          auth.set_item(PAM.PAM_USER, self.login_username.value())
          global login_password
          login_password = self.login_password.value()
          auth.set_item(PAM.PAM_CONV, pam_conv)
          try:
              auth.authenticate()
          except PAM.error, (resp, code):
              log(resp)
              return False
          except:
              log("Internal error")
              return False
          else:
              self.screen_locked = False
              self.__current_page = STATUS_PAGE
              return True

      def process_config(self):
          if self.__current_page == NETWORK_PAGE:
              ret = self.process_network_config()
          if self.__current_page == AUTHENTICATION_PAGE:
              ret = self.process_authentication_config()
          if self.__current_page == MONITORING_PAGE:
              ret = self.process_monitoring_config()
          if self.__current_page == LOGGING_PAGE:
              ret = self.process_logging_config()
          if self.__current_page == NETWORK_DETAILS_PAGE:
              ret = self.process_nic_config()
          if self.__current_page == KDUMP_PAGE:
              ret = self.process_kdump_config()
          if self.__current_page == REMOTE_STORAGE_PAGE:
              ret = self.process_remote_storage_config()
          if self.__current_page == LOCKED_PAGE:
              ret = self.process_locked_screen()
          return

      def process_kdump_config(self):
          if self.kdump_nfs_type.value() == 1:
              log(self.kdump_nfs_config.value())
              write_kdump_config(self.kdump_nfs_config.value())
          if self.kdump_ssh_type.value() == 1:
              write_kdump_config(self.kdump_ssh_config.value())
              #service kdump propagate
          if self.kdump_restore_type.value() == 1:
              restore_kdump_config()
          ovirt_store_config("/etc/kdump.conf")
          os.system("service kdump restart &> /dev/null")

      def process_remote_storage_config(self):
          set_iscsi_initiator(self.iscsi_initiator_config.value())

      def start(self):
            active = True
            # check for screenlock status
            self.screen_locked = False 
            while active and (self.__finished == False):
                log("current page: " + str(self.__current_page))
                self.screen = SnackScreen()
                screen = self.screen
                # apply any colorsets that were provided.
                for item in self.__colorset.keys():
                    colors = self.__colorset.get(item)
                    screen.setColor(item, colors[0], colors[1])
                self.set_console_colors()
                screen.pushHelpLine(" ")
                elements = self.get_elements_for_page(screen, self.__current_page)
                gridform = GridForm(screen, "", 4, 2) # 5,2
                screen.drawRootText(1,0, "                                                                              ")
                screen.drawRootText(1,1, "   oVirt Hypervisor                                                           ")
                screen.drawRootText(1,2, "                                                                              ")
                content = Grid(1, len(elements) + 3)
                self.menuo = 1
                self.menu_list = Listbox(18, width = 20, returnExit = 1, border = 0, showCursor = 0)
                self.menu_list.append("Status", 1)
                self.menu_list.append("", 2)
                self.menu_list.append("Network", 3)
                self.menu_list.append("", 4)
                self.menu_list.append("Security", 5)
                self.menu_list.append("", 6)
                self.menu_list.append("Monitoring", 7)
                self.menu_list.append("", 8)
                self.menu_list.append("Logging", 9)
                self.menu_list.append("", 10)
                self.menu_list.append("KDump", 11)
                self.menu_list.append("", 12)
                self.menu_list.append("Remote Storage", 13)
                self.menu_list.append("", 14)
                self.menu_list.append("", 15)
                self.menu_list.append("", 16)

                self.menu_list.setCallback(self.menuSpacing)
                if self.__current_page != LOCKED_PAGE and self.__current_page != NETWORK_DETAILS_PAGE:
                    self.menu_list.setCurrent(self.__current_page)
                if not self.screen_locked:
                    if not self.__current_page == NETWORK_DETAILS_PAGE:
                        gridform.add(self.menu_list, 1, 0,
                                     anchorTop = 1, anchorLeft = 1,
                                     growx = 0)
                current_element = 0
                for element in elements:
                    content.setField(element, 0, current_element, anchorLeft = 1)
                    current_element += 1
                (fullwidth, fullheight) = _snack.size()
                screen.height = fullheight
                content.setField(Label(""), 0, current_element,
                                 padding = ((fullwidth / 2) - 15, 0,
                                            (fullwidth / 2) - 16, 0),
                                 growx = 1)
                current_element += 1
                buttons = []
                if self.__current_page == NETWORK_PAGE:
                    buttons.append (["Flash Lights to Identify", IDENTIFY_BUTTON])
                if self.__current_page != STATUS_PAGE and self.__current_page < 20 :
                    buttons.append (["Apply", APPLY_BUTTON])
                if self.__current_page == NETWORK_DETAILS_PAGE:
                    buttons.append(["Back", BACK_BUTTON])
                if self.__current_page == STATUS_PAGE:
                    if not pwd_lock_check("admin"):
                        buttons.append(["Lock", LOCK_BUTTON])
                    buttons.append(["Restart", RESTART_BUTTON])
                    buttons.append(["Power Off", POWER_OFF_BUTTON])
                if self.__current_page == LOCKED_PAGE:
                    buttons.append(["Login", LOGIN_BUTTON])
                if self.__current_page != STATUS_PAGE and self.__current_page < 20:
                    buttons.append(["Reset", RESET_BUTTON])
                buttonbar = ButtonBar(screen, buttons, compact = 1)
                content.setField(buttonbar, 0, current_element, anchorLeft = 1)
                current_element += 1
                gridform.add(Label("  "), 2, 0, anchorTop = 1)
                current_element += 1
                gridform.add(content, 3, 0, anchorTop = 1)
                gridform.addHotKey("F2")
                try:
                    (top, left) = (1, 4)
                    result = gridform.runOnce(top, left)
                    menu_choice = self.menu_list.current()
                    pressed = buttonbar.buttonPressed(result)
                    self.menu_list.setCurrent(menu_choice)
                    warn_message = ""
                    try:
                        conn = libvirt.openReadOnly(None)
                        self.dom_count = conn.numOfDomains()
                    except:
                        self.dom_count = "Failed to connect"
                    if str(self.dom_count).isdigit():
                        warn_message= "There are %s Virtual Machines running\n\n" % str(self.dom_count)
                    else:
                        warn_message= "Unable to verify any running vms\n\n"
                    self.screen.setColor("BUTTON", "black", "red")
                    self.screen.setColor("ACTBUTTON", "blue", "white")
                    if result == "F2" and self.__current_page != LOCKED_PAGE:
                        screen.popWindow()
                        screen.finish()
                        os.system("/usr/bin/clear;/bin/bash")
                    if pressed == IDENTIFY_BUTTON:
                        os.system("ethtool -p " + self.nic_lb.current() + " 10")
                    elif pressed == APPLY_BUTTON or pressed == LOGIN_BUTTON:
                        errors = []
                        self.process_config()
                    elif pressed == LOCK_BUTTON:
                        self.__current_page = LOCKED_PAGE
                    elif pressed == RESTART_BUTTON:
                        self.screen = SnackScreen()
                        for item in self.__colorset.keys():
                            colors = self.__colorset.get(item)
                            self.screen.setColor(item, colors[0], colors[1])
                        self.screen.pushHelpLine(" ")
                        self.screen.refresh()
                        self.screen.setColor("BUTTON", "black", "red")
                        self.screen.setColor("ACTBUTTON", "blue", "white")
                        warn = ButtonChoiceWindow(self.screen, "Confirm System Restart", warn_message + "This will restart the system, proceed?")
                        if warn == "ok":
                            screen.popWindow()
                            screen.finish()
                            os.system("reboot")
                    elif pressed == POWER_OFF_BUTTON:
                        self.screen = SnackScreen()
                        for item in self.__colorset.keys():
                            colors = self.__colorset.get(item)
                            self.screen.setColor(item, colors[0], colors[1])
                        self.screen.pushHelpLine(" ")
                        self.screen.refresh()
                        self.screen.setColor("BUTTON", "black", "red")
                        self.screen.setColor("ACTBUTTON", "blue", "white")
                        warn = ButtonChoiceWindow(self.screen, "Confirm System Shutdown", warn_message + "This will shutdown the system, proceed?")
                        if warn == "ok":
                            screen.popWindow()
                            screen.finish()
                            os.system("/usr/bin/clear;shutdown -h now")
                    if self.__current_page == LOCKED_PAGE:
                        self.screen_locked = True
                    else:
                        if self.__current_page == NETWORK_PAGE:
                            if menu_choice == NETWORK_PAGE:
                                self.__current_page = NETWORK_DETAILS_PAGE
                            else:
                                self.__current_page = menu_choice
                        elif self.__current_page == NETWORK_DETAILS_PAGE:
                            if pressed == BACK_BUTTON:
                                self.__current_page = NETWORK_PAGE
                            elif is_managed(OVIRT_VARS["OVIRT_BOOTPARAMS"]):
                                dev_interface,dev_bootproto,dev_vendor,dev_address,dev_driver,dev_conf_status = self.nic_dict[self.nic_lb.current()].split(",", 5)
                                if self.configured_nics >= 1 and dev_conf_status != "Configured" :
                                    ButtonChoiceWindow(self.screen, "Network", "Hypervisor is already managed, unable to configure additional nics", buttons = ['Ok'])
                                    self.__current_page = NETWORK_PAGE
                            elif self.__nic_config_failed == 1:
                                self.__current_page = NETWORK_DETAILS_PAGE
                            else:
                               self.__current_page = NETWORK_PAGE
                        else:
                            self.__current_page = menu_choice
                except Exception, error:
                    self.screen.setColor("BUTTON", "black", "red")
                    self.screen.setColor("ACTBUTTON", "blue", "white")
                    ButtonChoiceWindow(screen,
                                       "An Exception Has Occurred",
                                       str(error) + "\n" + traceback.format_exc(),
                                       buttons = ["OK"])
                screen.popWindow()
                screen.finish()
                self.restore_console_colors()

if __name__ == "__main__":
   screen = NodeConfigScreen()
   screen.start()