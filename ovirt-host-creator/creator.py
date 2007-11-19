#!/usr/bin/python -tt
#
# livecd-creator : Creates Live CD based for Fedora.
#
# Copyright 2007, Red Hat  Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Library General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import os
import os.path
import stat
import glob
import sys
import errno
import string
import tempfile
import time
import traceback
import subprocess
import shutil
import optparse

import yum
import rpmUtils.arch
import pykickstart
import pykickstart.parser
import pykickstart.version

class MountError(Exception):
    def __init__(self, msg):
        Exception.__init__(self, msg)

class InstallationError(Exception):
    def __init__(self, msg):
        Exception.__init__(self, msg)

class BindChrootMount:
    """Represents a bind mount of a directory into a chroot."""
    def __init__(self, src, chroot, dest = None):
        self.src = src
        self.root = chroot

        if not dest:
            dest = src
        self.dest = self.root + "/" + dest

        self.mounted = False

    def mount(self):
        if not self.mounted:
            if not os.path.exists(self.dest):
                os.makedirs(self.dest)
            rc = subprocess.call(["/bin/mount", "--bind", self.src, self.dest])
            if rc != 0:
                raise MountError("Bind-mounting '%s' to '%s' failed" % (self.src, self.dest))
            self.mounted = True

    def umount(self):
        if self.mounted:
            rc = subprocess.call(["/bin/umount", self.dest])
            self.mounted = False
        

class LoopbackMount:
    def __init__(self, lofile, mountdir, fstype = None):
        self.lofile = lofile
        self.mountdir = mountdir
        self.fstype = fstype

        self.mounted = False
        self.losetup = False
        self.rmdir   = False
        self.loopdev = None

    def cleanup(self):
        self.umount()
        self.lounsetup()

    def umount(self):
        if self.mounted:
            rc = subprocess.call(["/bin/umount", self.mountdir])
            self.mounted = False

        if self.rmdir:
            try:
                os.rmdir(self.mountdir)
            except OSError, e:
                pass
            self.rmdir = False

    def lounsetup(self):
        if self.losetup:
            rc = subprocess.call(["/sbin/losetup", "-d", self.loopdev])
            self.losetup = False
            self.loopdev = None

    def loopsetup(self):
        if self.losetup:
            return

        losetupProc = subprocess.Popen(["/sbin/losetup", "-f"],
                                       stdout=subprocess.PIPE)
        losetupOutput = losetupProc.communicate()[0]

        if losetupProc.returncode:
            raise MountError("Failed to allocate loop device for '%s'" % self.lofile)
        else:
            self.loopdev = losetupOutput.split()[0]

        rc = subprocess.call(["/sbin/losetup", self.loopdev, self.lofile])
        if rc != 0:
            raise MountError("Failed to allocate loop device for '%s'" % self.lofile)

        self.losetup = True

    def mount(self):
        if self.mounted:
            return

        self.loopsetup()

        if not os.path.isdir(self.mountdir):
            os.makedirs(self.mountdir)
            self.rmdir = True

        args = [ "/bin/mount", self.loopdev, self.mountdir ]
        if self.fstype:
            args.extend(["-t", self.fstype])

        rc = subprocess.call(args)
        if rc != 0:
            raise MountError("Failed to mount '%s' to '%s'" % (self.loopdev, self.mountdir))

        self.mounted = True

class SparseExt3LoopbackMount(LoopbackMount):
    def __init__(self, lofile, mountdir, size, blocksize, fslabel):
        LoopbackMount.__init__(self, lofile, mountdir, fstype = "ext3")
        self.size = size
        self.blocksize = blocksize
        self.fslabel = fslabel

    def _createSparseFile(self):
        dir = os.path.dirname(self.lofile)
        if not os.path.isdir(dir):
            os.makedirs(dir)

        # create the sparse file
        fd = os.open(self.lofile, os.O_WRONLY | os.O_CREAT)
        off = long(self.size * 1024L * 1024L)
        os.lseek(fd, off, 0)
        os.write(fd, '\x00')
        os.close(fd)

    def _formatFilesystem(self):
        rc = subprocess.call(["/sbin/mkfs.ext3", "-F", "-L", self.fslabel,
                              "-m", "1", "-b", str(self.blocksize), self.lofile,
                              str(self.size *  1024L * 1024L / self.blocksize)])
        if rc != 0:
            raise MountError("Error creating ext3 filesystem")
        rc = subprocess.call(["/sbin/tune2fs", "-c0", "-i0", "-Odir_index",
                              "-ouser_xattr,acl", self.lofile])

    def mount(self):
        self._createSparseFile()
        self._formatFilesystem()
        return LoopbackMount.mount(self)

class LiveCDParser(pykickstart.parser.KickstartParser):
    def __init__(self, *args, **kwargs):
        pykickstart.parser.KickstartParser.__init__(self, *args, **kwargs)
        self.currentdir = {}

    def readKickstart(self, file, reset=True):
        # an %include might not specify a full path.  if we don't try to figure
        # out what the path should have been, then we're unable to find it
        # requiring full path specification, though, sucks.  so let's make
        # the reading "smart" by keeping track of what the path is at each
        # include depth.
        if not os.path.exists(file):
            if self.currentdir.has_key(self._includeDepth - 1):
                if os.path.exists(os.path.join(self.currentdir[self._includeDepth - 1], file)):
                    file = os.path.join(self.currentdir[self._includeDepth - 1], file)

        cd = os.path.dirname(file)
        if not cd.startswith("/"):
            cd = os.path.abspath(cd)
        self.currentdir[self._includeDepth] = cd
        return pykickstart.parser.KickstartParser.readKickstart(self, file, reset)

class TextProgress(object):
    def start(self, filename, url, *args, **kwargs):
        sys.stdout.write("Retrieving %s " % (url,))
        self.url = url
    def update(self, *args):
        pass
    def end(self, *args):
        sys.stdout.write("...OK\n")

class LiveCDYum(yum.YumBase):
    def __init__(self):
        yum.YumBase.__init__(self)

    def doFileLogSetup(self, uid, logfile):
        # don't do the file log for the livecd as it can lead to open fds
        # being left and an inability to clean up after ourself
        pass

    def _writeConf(self, datadir, installroot):
        conf  = "[main]\n"
        conf += "installroot=%s\n" % installroot
        conf += "cachedir=/var/cache/yum\n"
        conf += "plugins=0\n"
        conf += "reposdir=\n"

        path = datadir + "/yum.conf"

        f = file(path, "w+")
        f.write(conf)
        f.close()

        os.chmod(path, 0644)

        return path

    def setup(self, datadir, installroot):
        self.doConfigSetup(fn = self._writeConf(datadir, installroot),
                           root = installroot)
        self.conf.cache = 0
        self.doTsSetup()
        self.doRpmDBSetup()
        self.doRepoSetup()
        self.doSackSetup()

    def selectPackage(self, pkg):
        """Select a given package.  Can be specified with name.arch or name*"""
        return self.install(pattern = pkg)
        
    def deselectPackage(self, pkg):
        """Deselect package.  Can be specified as name.arch or name*"""
        sp = pkg.rsplit(".", 2)
        txmbrs = []
        if len(sp) == 2:
            txmbrs = self.tsInfo.matchNaevr(name=sp[0], arch=sp[1])

        if len(txmbrs) == 0:
            exact, match, unmatch = yum.packages.parsePackages(self.pkgSack.returnPackages(), [pkg], casematch=1)
            for p in exact + match:
                txmbrs.append(p)

        if len(txmbrs) > 0:
            map(lambda x: self.tsInfo.remove(x.pkgtup), txmbrs)
        else:
            print >> sys.stderr, "No such package %s to remove" %(pkg,)

    def selectGroup(self, grp, include = pykickstart.parser.GROUP_DEFAULT):
        yum.YumBase.selectGroup(self, grp)
        if include == pykickstart.parser.GROUP_REQUIRED:
            map(lambda p: self.deselectPackage(p), grp.default_packages.keys())
        elif include == pykickstart.parser.GROUP_ALL:
            map(lambda p: self.selectPackage(p), grp.optional_packages.keys())

    def addRepository(self, name, url = None, mirrorlist = None):
        def _varSubstitute(option):
            # takes a variable and substitutes like yum configs do
            option = option.replace("$basearch", rpmUtils.arch.getBaseArch())
            option = option.replace("$arch", rpmUtils.arch.getCanonArch())
            return option

        repo = yum.yumRepo.YumRepository(name)
        if url:
            repo.baseurl.append(_varSubstitute(url))
        if mirrorlist:
            repo.mirrorlist = _varSubstitute(mirrorlist)
        conf = yum.config.RepoConf()
        for k, v in conf.iteritems():
            if v or not hasattr(repo, k):
                repo.setAttribute(k, v)
        repo.basecachedir = self.conf.cachedir
        repo.metadata_expire = 0
        # disable gpg check???
        repo.gpgcheck = 0
        repo.enable()
        repo.setup(0)
        repo.setCallback(TextProgress())
        self.repos.add(repo)
        return repo
            
    def runInstall(self):
        try:
            (res, resmsg) = self.buildTransaction()
        except yum.Errors.RepoError, e:
            raise InstallationError("Unable to download from repo : %s" %(e,))
        if res != 2 and False:
            raise InstallationError("Failed to build transaction : %s" % str.join("\n", resmsg))
        
        dlpkgs = map(lambda x: x.po, filter(lambda txmbr: txmbr.ts_state in ("i", "u"), self.tsInfo.getMembers()))
        self.downloadPkgs(dlpkgs)
        # FIXME: sigcheck?
        
        self.initActionTs()
        self.populateTs(keepold=0)
        self.ts.check()
        self.ts.order()
        # FIXME: callback should be refactored a little in yum 
        sys.path.append('/usr/share/yum-cli')
        import callback
        cb = callback.RPMInstallCallback()
        cb.tsInfo = self.tsInfo
        cb.filelog = False
        return self.runTransaction(cb)

def mksquashfs(output, filelist, cwd = None):
    args = ["/sbin/mksquashfs"]
    args.extend(filelist)
    args.append(output)
    if not sys.stdout.isatty():
        args.append("-no-progress")
    if cwd is None:
        cwd = os.getcwd()
    return subprocess.call(args, cwd = cwd,  env={"PWD": cwd})

class ImageCreator(object):
    def __init__(self, fs_label):
        self.ayum = None
        self.fs_label = fs_label
        self.skip_compression = False
        self.tmpdir = "/var/tmp"

        self._checkIsoMD5 = False

        self.build_dir = None
        self.instloop = None
        self.bindmounts = []
        self.ksparser = None

        self.image_size = 4096 # in megabytes
        self.blocksize = 4096 # in kilobytes
        self.minimized_image_size = 0 # in kilobytes

    def _getRequiredPackages(self):
        return []
    def _getRequiredExcludePackages(self):
        return []

    def _getKernelOptions(self):
        r = "ro liveimg"
        if os.path.exists("%s/install_root/usr/bin/rhgb" %(self.build_dir,)):
            r += " rhgb"
        return r
        
    def parse(self, kscfg):
        ksversion = pykickstart.version.makeVersion()
        self.ksparser = LiveCDParser(ksversion)
        if kscfg:
            try:
                self.ksparser.readKickstart(kscfg)
            except IOError, (err, msg):
                raise InstallationError("Failed to read kickstart file '%s' : %s" % (kscfg, msg))
            except pykickstart.errors.KickstartError, e:
                raise InstallationError("Failed to parse kickstart file '%s' : %s" % (kscfg, e))

        for p in self.ksparser.handler.partition.partitions:
            if p.mountpoint == "/" and p.size:
                self.image_size = int(p.size)

        if len(self.ksparser.handler.packages.packageList) == 0 and len(self.ksparser.handler.packages.groupList) == 0:
            raise InstallationError("No packages or groups specified")

        if not self.ksparser.handler.repo.repoList:
            raise InstallationError("No repositories specified")

        # more sanity checks
        if self.ksparser.handler.selinux.selinux and not \
               os.path.exists("/selinux/enforce"):
            raise InstallationError("SELinux requested but not enabled on host system")

    def base_on_iso(self, base_on):
        """helper function to extract ext3 file system from a live CD ISO"""

        isoloop = LoopbackMount(base_on, "%s/base_on_iso" %(self.build_dir,))

        try:
            isoloop.mount()
        except MountError, e:
            raise InstallationError("Failed to loopback mount '%s' : %s" % (base_on, e))

        # legacy LiveOS filesystem layout support, remove for F9 or F10
        if os.path.exists("%s/LiveOS/squashfs.img" %(isoloop.mountdir,)):
            squashloop = LoopbackMount("%s/LiveOS/squashfs.img" %(isoloop.mountdir,),
                                       "%s/base_on_squashfs" %(self.build_dir,),
                                       "squashfs")
        else:
            squashloop = LoopbackMount("%s/squashfs.img" %(isoloop.mountdir,),
                                       "%s/base_on_squashfs" %(self.build_dir,),
                                       "squashfs")

        try:

            if not os.path.exists(squashloop.lofile):
                raise InstallationError("'%s' is not a valid live CD ISO : squashfs.img doesn't exist" % base_on)

            try:
                squashloop.mount()
            except MountError, e:
                raise InstallationError("Failed to loopback mount squashfs.img from '%s' : %s" % (base_on, e))

            # legacy LiveOS filesystem layout support, remove for F9 or F10
            if os.path.exists(self.build_dir + "/base_on_squashfs/os.img"):
                os_image = self.build_dir + "/base_on_squashfs/os.img"
            elif os.path.exists(self.build_dir + "/base_on_squashfs/LiveOS/ext3fs.img"):
                os_image = self.build_dir + "/base_on_squashfs/LiveOS/ext3fs.img"
            else:
                raise InstallationError("'%s' is not a valid live CD ISO : os.img doesn't exist" % base_on)

            shutil.copyfile(os_image, self.build_dir + "/data/LiveOS/ext3fs.img")
        finally:
            # unmount and tear down the mount points and loop devices used
            squashloop.cleanup()
            isoloop.cleanup()

    def write_fstab(self):
        fstab = open(self.build_dir + "/install_root/etc/fstab", "w")
        fstab.write("/dev/mapper/livecd-rw   /                       ext3    defaults,noatime 0 0\n")
        fstab.write("devpts                  /dev/pts                devpts  gid=5,mode=620  0 0\n")
        fstab.write("tmpfs                   /dev/shm                tmpfs   defaults        0 0\n")
        fstab.write("proc                    /proc                   proc    defaults        0 0\n")
        fstab.write("sysfs                   /sys                    sysfs   defaults        0 0\n")
        fstab.close()

    def setup(self, base_on = None, cachedir = None):
        """setup target ext3 file system in preparation for an install"""

        # setup temporary build dirs
        try:
            self.build_dir = tempfile.mkdtemp(dir=self.tmpdir, prefix="livecd-creator-")
        except OSError, (err, msg):
            raise InstallationError("Failed create build directory in %s: %s" % (self.tmpdir, msg))

        os.makedirs(self.build_dir + "/out/LiveOS")
        os.makedirs(self.build_dir + "/data/LiveOS")
        os.makedirs(self.build_dir + "/install_root")
        os.makedirs(self.build_dir + "/yum-cache")

        if base_on:
            # get backing ext3 image if we're based this build on an existing live CD ISO
            self.base_on_iso(base_on)

            self.instloop = LoopbackMount("%s/data/LiveOS/ext3fs.img" %(self.build_dir,),
                                          "%s/install_root" %(self.build_dir,))
        else:
            self.instloop = SparseExt3LoopbackMount("%s/data/LiveOS/ext3fs.img"
                                                    %(self.build_dir,),
                                                    "%s/install_root"
                                                    %(self.build_dir,),
                                                    self.image_size,
                                                    self.blocksize,
                                                    self.fs_label)

        try:
            self.instloop.mount()
        except MountError, e:
            raise InstallationError("Failed to loopback mount '%s' : %s" % (self.instloop.lofile, e))

        if not base_on:
            # create a few directories needed if it's a new image
            os.makedirs(self.build_dir + "/install_root/etc")
            os.makedirs(self.build_dir + "/install_root/boot")
            os.makedirs(self.build_dir + "/install_root/var/log")
            os.makedirs(self.build_dir + "/install_root/var/cache/yum")

        # bind mount system directories into install_root/
        for (f, dest) in [("/sys", None), ("/proc", None), ("/dev", None),
                          ("/dev/pts", None), ("/selinux", None),
                          ((cachedir or self.build_dir) + "/yum-cache", "/var/cache/yum")]:
            self.bindmounts.append(BindChrootMount(f, self.build_dir + "/install_root", dest))

        for b in self.bindmounts:
            b.mount()

        # make sure /etc/mtab is current inside install_root
        os.symlink("../proc/mounts", self.build_dir + "/install_root/etc/mtab")

        self.write_fstab()

        self.ayum = LiveCDYum()
        self.ayum.setup(self.build_dir + "/data",
                        self.build_dir + "/install_root")

    def unmount(self):
        """detaches system bind mounts and install_root for the file system and tears down loop devices used"""
        if self.ayum:
            self.ayum.close()
            self.ayum = None

        try:
            os.unlink(self.build_dir + "/install_root/etc/mtab")
        except OSError:
            pass

        self.bindmounts.reverse()
        for b in self.bindmounts:
            b.umount()

        if self.instloop:
            self.instloop.cleanup()
            self.instloop = None

    def teardown(self):
        if self.build_dir:
            self.unmount()
            shutil.rmtree(self.build_dir, ignore_errors = True)

    def addRepository(self, name, url):
        """adds a yum repository to temporary yum.conf file used"""
        self.ayum.addRepository(name, url)

    def run_in_root(self):
        os.chroot("%s/install_root" %(self.build_dir,))
        os.chdir("/")

    def installPackages(self):
        """install packages into target file system"""
        try:
            for pkg in (self.ksparser.handler.packages.packageList + 
                        self._getRequiredPackages()):
                try:
                    self.ayum.selectPackage(pkg)
                except yum.Errors.InstallError, e:
                    if self.ksparser.handler.packages.handleMissing != \
                           pykickstart.constants.KS_MISSING_IGNORE:
                        raise InstallationError("Failed to find package '%s' : %s" % (pkg, e))
                    else:
                        print >> sys.stderr, "Unable to find package '%s'; skipping" %(pkg,)

            for group in self.ksparser.handler.packages.groupList:
                try:
                    self.ayum.selectGroup(group.name, group.include)
                except (yum.Errors.InstallError, yum.Errors.GroupsError), e:
                    if self.ksparser.handler.packages.handleMissing != \
                           pykickstart.constants.KS_MISSING_IGNORE:
                        raise InstallationError("Failed to find group '%s' : %s" % (group.name, e))
                    else:
                        print >> sys.stderr, "Unable to find group '%s'; skipping" %(group.name,)

            map(lambda pkg: self.ayum.deselectPackage(pkg),
                self.ksparser.handler.packages.excludedList +
                self._getRequiredExcludePackages())

            self.ayum.runInstall()
        except yum.Errors.RepoError, e:
            raise InstallationError("Unable to download from repo : %s" %(e,))
        except yum.Errors.YumBaseError, e:
            raise InstallationError("Unable to install: %s" %(e,))
        finally:
            self.ayum.closeRpmDB()

    def writeNetworkIfCfg(self, instroot, network):
        path = instroot + "/etc/sysconfig/network-scripts/ifcfg-" + network.device

        f = file(path, "w+")
        os.chmod(path, 0644)

        f.write("DEVICE=%s\n" % network.device)
        f.write("BOOTPROTO=%s\n" % network.bootProto)

        if network.bootProto.lower() == "static":
            if network.ip:
                f.write("IPADDR=%s\n" % network.ip)
            if network.netmask:
                f.write("NETMASK=%s\n" % network.netmask)

        if network.onboot:
            f.write("ONBOOT=on\n")
        else:
            f.write("ONBOOT=off\n")

        if network.essid:
            f.write("ESSID=%s\n" % network.essid)

        if network.ethtool:
            if network.ethtool.find("autoneg") == -1:
                network.ethtool = "autoneg off " + network.ethtool
            f.write("ETHTOOL_OPTS=%s\n" % network.ethtool)

        if network.bootProto.lower() == "dhcp":
            if network.hostname:
                f.write("DHCP_HOSTNAME=%s\n" % network.hostname)
            if network.dhcpclass:
                f.write("DHCP_CLASSID=%s\n" % network.dhcpclass)

        if network.mtu:
            f.write("MTU=%s\n" % network.mtu)

        f.close()

    def writeNetworkKey(self, instroot, network):
        if not network.wepkey:
            return

        path = instroot + "/etc/sysconfig/network-scripts/keys-" + network.device
        f = file(path, "w+")
        os.chmod(path, 0600)
        f.write("KEY=%s\n" % network.wepkey)
        f.close()

    def writeNetworkConfig(self, instroot, useipv6, hostname, gateway):
        path = instroot + "/etc/sysconfig/network"
        f = file(path, "w+")
        os.chmod(path, 0644)

        f.write("NETWORKING=yes\n")

        if useipv6:
            f.write("NETWORKING_IPV6=yes\n")
        else:
            f.write("NETWORKING_IPV6=no\n")

        if hostname:
            f.write("HOSTNAME=%s\n" % hostname)
        else:
            f.write("HOSTNAME=localhost.localdomain\n")

        if gateway:
            f.write("GATEWAY=%s\n" % gateway)

        f.close()

    def writeNetworkHosts(self, instroot, hostname):
        localline = ""
        if hostname and hostname != "localhost.localdomain":
            localline += hostname + " "
            l = string.split(hostname, ".")
            if len(l) > 1:
                localline += l[0] + " "
        localline += "localhost.localdomain localhost"

        path = instroot + "/etc/hosts"
        f = file(path, "w+")
        os.chmod(path, 0644)
        f.write("127.0.0.1\t\t%s\n" % localline)
        f.write("::1\t\tlocalhost6.localdomain6 localhost6\n")
        f.close()

    def writeNetworkResolv(self, instroot, nodns, nameservers):
        if nodns or not nameservers:
            return

        path = instroot + "/etc/resolv.conf"
        f = file(path, "w+")
        os.chmod(path, 0644)

        for ns in (nameservers):
            if ns:
                f.write("nameserver %s\n" % ns)

        f.close()

    def configureNetwork(self):
        instroot = self.build_dir + "/install_root"

        try:
            os.makedirs(instroot + "/etc/sysconfig/network-scripts")
        except OSError, (err, msg):
            if err != errno.EEXIST:
                raise

        useipv6 = False
        nodns = False
        hostname = None
        gateway = None
        nameservers = None

        for network in self.ksparser.handler.network.network:
            if not network.device:
                raise InstallationError("No --device specified with network kickstart command")

            if network.onboot and network.bootProto.lower() != "dhcp" and \
               not (network.ip and network.netmask):
                raise InstallationError("No IP address and/or netmask specified with static " +
                                        "configuration for '%s'" % network.device)

            self.writeNetworkIfCfg(instroot, network)
            self.writeNetworkKey(instroot, network)

            if network.ipv6:
                useipv6 = True
            if network.nodns:
                nodns = True

            if network.hostname:
                hostname = network.hostname
            if network.gateway:
                gateway = network.gateway

            if network.nameserver:
                nameservers = string.split(network.nameserver, ",")

        self.writeNetworkConfig(instroot, useipv6, hostname, gateway)
        self.writeNetworkHosts(instroot, hostname)
        self.writeNetworkResolv(instroot, nodns, nameservers)

    def configureSystem(self):
        instroot = "%s/install_root" %(self.build_dir,)
        
        # FIXME: this is a bit ugly, but with the current pykickstart
        # API, we don't really have a lot of choice.  it'd be nice to
        # be able to do something different, but so it goes

        # set up the language
        lang = self.ksparser.handler.lang.lang or "en_US.UTF-8"
        f = open("%s/etc/sysconfig/i18n" %(instroot,), "w+")
        f.write("LANG=\"%s\"\n" %(lang,))
        f.close()

        # next, the keyboard
        # FIXME: should this impact the X keyboard config too???
        # or do we want to make X be able to do this mapping
        import rhpl.keyboard
        k = rhpl.keyboard.Keyboard()
        if self.ksparser.handler.keyboard.keyboard:
            k.set(self.ksparser.handler.keyboard.keyboard)
        k.write(instroot)

        # next up is timezone
        tz = self.ksparser.handler.timezone.timezone or "America/New_York"
        utc = self.ksparser.handler.timezone.isUtc
        f = open("%s/etc/sysconfig/clock" %(instroot,), "w+")
        f.write("ZONE=\"%s\"\n" %(tz,))
        f.write("UTC=%s\n" %(utc,))
        f.close()

        # do any authconfig bits
        auth = self.ksparser.handler.authconfig.authconfig or "--useshadow --enablemd5"
        if os.path.exists("%s/usr/sbin/authconfig" %(instroot,)):
            args = ["/usr/sbin/authconfig", "--update", "--nostart"]
            args.extend(auth.split())
            subprocess.call(args, preexec_fn=self.run_in_root)

        # firewall.  FIXME: should handle the rest of the options
        if self.ksparser.handler.firewall.enabled and os.path.exists("%s/usr/sbin/lokkit" %(instroot,)):
            subprocess.call(["/usr/sbin/lokkit", "-f", "--quiet",
                             "--nostart", "--enabled"],
                            preexec_fn=self.run_in_root)

        # selinux
        if os.path.exists("%s/usr/sbin/lokkit" %(instroot,)):
            args = ["/usr/sbin/lokkit", "-f", "--quiet", "--nostart"]
            if self.ksparser.handler.selinux.selinux:
                args.append("--selinux=enforcing")
            else:
                args.append("--selinux=disabled")
            subprocess.call(args, preexec_fn=self.run_in_root)

        # Set the root password
        if self.ksparser.handler.rootpw.isCrypted:
            subprocess.call(["/usr/sbin/usermod", "-p", self.ksparser.handler.rootpw.password, "root"], preexec_fn=self.run_in_root)
        elif self.ksparser.handler.rootpw.password == "":
            # Root password is not set and not crypted, empty it
            subprocess.call(["/usr/bin/passwd", "-d", "root"], preexec_fn=self.run_in_root)
        else:
            # Root password is set and not crypted
            p1 = subprocess.Popen(["/bin/echo", self.ksparser.handler.rootpw.password], stdout=subprocess.PIPE, preexec_fn=self.run_in_root)
            p2 = subprocess.Popen(["/usr/bin/passwd", "--stdin", "root"], stdin=p1.stdout, stdout=subprocess.PIPE, preexec_fn=self.run_in_root)
            output = p2.communicate()[0]

        # enable/disable services appropriately
        if os.path.exists("%s/sbin/chkconfig" %(instroot,)):
            for s in self.ksparser.handler.services.enabled:
                subprocess.call(["/sbin/chkconfig", s, "on"],
                                preexec_fn=self.run_in_root)
            for s in self.ksparser.handler.services.disabled:
                subprocess.call(["/sbin/chkconfig", s, "off"],
                                preexec_fn=self.run_in_root)

        # x by default?
        if self.ksparser.handler.xconfig.startX:
            f = open("%s/etc/inittab" %(instroot,), "rw+")
            buf = f.read()
            buf = buf.replace("id:3:initdefault", "id:5:initdefault")
            f.seek(0)
            f.write(buf)
            f.close()

    def runPost(self):
        instroot = "%s/install_root" %(self.build_dir,)        
        # and now, for arbitrary %post scripts
        for s in filter(lambda s: s.type == pykickstart.parser.KS_SCRIPT_POST,
                        self.ksparser.handler.scripts):
            (fd, path) = tempfile.mkstemp("", "ks-script-", "%s/tmp" %(instroot,))
            os.write(fd, s.script)
            os.close(fd)
            os.chmod(path, 0700)

            if not s.inChroot:
                env = {"BUILD_DIR": self.build_dir,
                       "INSTALL_ROOT": "%s/install_root" %(self.build_dir,),
                       "LIVE_ROOT": "%s/out" %(self.build_dir,)}
                preexec = lambda: os.chdir(self.build_dir,)
                script = path
            else:
                env = {}
                preexec = self.run_in_root
                script = "/tmp/%s" %(os.path.basename(path),)

            try:
                subprocess.call([s.interp, script],
                                preexec_fn = preexec, env = env)
            except OSError, (err, msg):
                os.unlink(path)
                raise InstallationError("Failed to execute %%post script with '%s' : %s" % (s.interp, msg))
            os.unlink(path)

    def get_kernel_version(self):
        #
        # FIXME: this doesn't handle multiple kernels - we should list
        #        them all in the isolinux menu
        #
        kernels = []
        modules_dir = "%s/install_root/lib/modules" % self.build_dir

        if os.path.isdir(modules_dir):
            kernels = os.listdir(modules_dir)

        if not kernels:
            raise InstallationError("No kernels installed: /lib/modules is empty")

        return kernels[0]

    def createInitramfs(self):
        mpath = "/usr/lib/livecd-creator/mayflower"

        # look to see if we're running from a git tree; in which case,
        # we should use the git mayflower too
        if globals().has_key("__file__") and \
           not os.path.abspath(__file__).startswith("/usr/bin"):
            f = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                             "mayflower")
            if os.path.exists(f):
                mpath = f

        # Create initramfs
        if not os.path.isfile(mpath):
            raise InstallationError("livecd-creator not correctly installed : "+
                                    "/usr/lib/livecd-creator/mayflower not found")
        shutil.copy(mpath, "%s/install_root/sbin/mayflower" %(self.build_dir,))
        # modules we want to support for booting
        mcfg = open(self.build_dir + "/install_root/etc/mayflower.conf", "a")
        mcfg.write('MODULES+="squashfs ext3 ext2 vfat msdos "\n')
        mcfg.write('MODULES+="ehci_hcd uhci_hcd ohci_hcd usb_storage usbhid "\n')
        mcfg.write('MODULES+="firewire-sbp2 firewire-ohci "\n')
        mcfg.write('MODULES+="sr_mod sd_mod ide-cd "\n')
        mcfg.write('MODULES+="=ata "\n')
        mcfg.write('MODULES+="sym53c8xx aic7xxx "\n')
        mcfg.close()

        subprocess.call(["/sbin/mayflower", "-f", "/boot/livecd-initramfs.img",
                        self.get_kernel_version()],
                        preexec_fn=self.run_in_root)
        for f in ("/sbin/mayflower", "/etc/mayflower.conf"):
            os.unlink("%s/install_root/%s" %(self.build_dir, f))

    def relabelSystem(self):
        # finally relabel all files
        if self.ksparser.handler.selinux.selinux:
            instroot = "%s/install_root" %(self.build_dir,)
            if os.path.exists("%s/sbin/restorecon" %(instroot,)):
                subprocess.call(["/sbin/restorecon", "-v", "-r", "/"],
                                preexec_fn=self.run_in_root)

    def launchShell(self):
        subprocess.call(["/bin/bash"], preexec_fn=self.run_in_root)

    def install(self):
        try:
            self.ksparser.handler.repo.methodToRepo()
        except:
            pass

        for repo in self.ksparser.handler.repo.repoList:
            yr = self.ayum.addRepository(repo.name, repo.baseurl, repo.mirrorlist)
            if hasattr(repo, "includepkgs"):
                yr.includepkgs = repo.includepkgs
            if hasattr(repo, "excludepkgs"):
                yr.exclude = repo.excludepkgs

        self.installPackages()
        if os.path.exists("%s/install_root/usr/lib/anaconda-runtime/checkisomd5" %(self.build_dir,)) or os.path.exists("%s/install_root/usr/bin/checkisomd5" %(self.build_dir,)):        
            self._checkIsoMD5 = True

        try:
            self.configureSystem()
        except Exception, e: #FIXME: we should be a little bit more fine-grained
            raise InstallationError("Error configuring live image: %s" %(e,))
        self.configureNetwork()
        self.relabelSystem()
        self.createInitramfs()
        self.configureBootloader()
        self.runPost()

    def configureBootloader(self):
        raise InstallationError("Bootloader configuration is arch-specific, but not implemented for this arch!")

    def createIso(self):
        # WARNING: if you don't override this, your CD probably won't be
        # bootable
        rc = subprocess.call(["/usr/bin/mkisofs", "-o", "%s.iso" %(self.fs_label,),
                         "-J", "-r", "-hide-rr-moved", "-hide-joliet-trans-tbl",
                         "-V", "%s" %(self.fs_label,),
                         "%s/out" %(self.build_dir)])
        if rc != 0:
            import pdb; pdb.set_trace()
        import pdb; pdb.set_trace()

    def implantIsoMD5(self):
        """Implant an isomd5sum."""
        if os.path.exists("/usr/bin/implantisomd5"):
            subprocess.call(["/usr/bin/implantisomd5",
                             "%s.iso" %(self.fs_label,)])
        elif os.path.exists("/usr/lib/anaconda-runtime/implantisomd5"):
            subprocess.call(["/usr/lib/anaconda-runtime/implantisomd5",
                             "%s.iso" %(self.fs_label,)])
        else:
            print >> sys.stderr, "isomd5sum not installed; not setting up mediacheck"

    def createSquashFS(self):
        """create compressed squashfs file system"""
        if not self.skip_compression:
            ret = mksquashfs("out/LiveOS/squashfs.img", ["data"],
                             self.build_dir)
            if ret != 0:
                raise InstallationError("mksquashfs exited with error (%d)" %(ret,))
        else:
            shutil.move("%s/data/LiveOS/ext3fs.img" %(self.build_dir,),
                        "%s/out/LiveOS/ext3fs.img" %(self.build_dir,))

    def _getBlockCountOfExt2FS(self, filesystem):
        def parseField(output, field):
            for line in output.split("\n"):
                if line.startswith(field + ":"):
                    return line[len(field) + 1:].strip()

            raise KeyError("Failed to find field '%s' in output" % field)

        output = subprocess.Popen(['/sbin/dumpe2fs', '-h', filesystem],
                                  stdout=subprocess.PIPE,
                                  stderr=open('/dev/null', 'w')
                                  ).communicate()[0]

        return int(parseField(output, "Block count"))

    def _resize2fs(self, image, n_blocks):
        dev_null = os.open("/dev/null", os.O_WRONLY)
        try:
            return subprocess.call(["/sbin/resize2fs", image, str(n_blocks)],
                                   stdout = dev_null,
                                   stderr = dev_null)
        finally:
            os.close(dev_null)

    #
    # resize2fs doesn't have any kind of minimal setting, so use
    # a binary search to get it to minimal size.
    #
    def _resize2fsToMinimal(self, image):
        bot = 0
        top = self._getBlockCountOfExt2FS(image)
        while top != (bot + 1):
            t = bot + ((top - bot) / 2)

            if not self._resize2fs(image, t):
                top = t
            else:
                bot = t

        return top

    #
    # cleanupDeleted removes unused data from the sparse ext3 os image file.
    # The process involves: resize2fs-to-minimal, truncation,
    # resize2fs-to-uncompressed-size (with implicit resparsification)
    #
    def cleanupDeleted(self):
        image = "%s/data/LiveOS/ext3fs.img" %(self.build_dir,)

        subprocess.call(["/sbin/e2fsck", "-f", "-y", image])

        n_blocks = os.stat(image)[stat.ST_SIZE] / self.blocksize

        min_blocks = self._resize2fsToMinimal(image)

        # truncate the unused excess portion of the sparse file
        fd = os.open(image, os.O_WRONLY )
        os.ftruncate(fd, min_blocks * self.blocksize)
        os.close(fd)

        self.minimized_image_size = min_blocks * self.blocksize / 1024L
        print >> sys.stderr, "Installation target minimized to %dK" % (self.minimized_image_size)

        self._resize2fs(image, n_blocks)


    #
    # genMinInstDelta: generates an osmin overlay file to sit alongside
    #                  ext3fs.img.  liveinst may then detect the existence of
    #                  osmin, and use it to create a minimized ext3fs.img
    #                  which can be installed more quickly, and to smaller
    #                  destination volumes.
    #
    def genMinInstDelta(self):
        # create the sparse file for the minimized overlay
        fd = os.open("%s/out/LiveOS/osmin" %(self.build_dir,),
                     os.O_WRONLY | os.O_CREAT)
        off = long(64L * 1024L * 1024L)
        os.lseek(fd, off, 0)
        os.write(fd, '\x00')
        os.close(fd)

        # associate os image with loop device
        osloop = LoopbackMount("%s/data/LiveOS/ext3fs.img" %(self.build_dir,), \
                               "None")
        osloop.loopsetup()

        # associate overlay with loop device
        minloop = LoopbackMount("%s/out/LiveOS/osmin" %(self.build_dir,), \
                                "None")
        minloop.loopsetup()

        # create a snapshot device
        rc = subprocess.call(["/sbin/dmsetup",
                              "--table",
                              "0 %d snapshot %s %s p 8"
                              %(self.image_size * 1024L * 2L,
                                osloop.loopdev, minloop.loopdev),
                              "create",
                              "livecd-creator-%d" %(os.getpid(),) ])
        if rc != 0:
            raise InstallationError("Could not create genMinInstDelta snapshot device")
        # resize snapshot device back to minimal (self.minimized_image_size)
        rc = subprocess.call(["/sbin/resize2fs",
                              "/dev/mapper/livecd-creator-%d" %(os.getpid(),),
                              "%dK" %(self.minimized_image_size,)])
        if rc != 0:
            raise InstallationError("Could not shrink ext3fs image")

        # calculate how much delta data to keep
        dmsetupOutput = subprocess.Popen(['/sbin/dmsetup', 'status',
                                          "livecd-creator-%d" %(os.getpid(),)],
                                         stdout=subprocess.PIPE,
                                         stderr=open('/dev/null', 'w')
                                         ).communicate()[0]

        # The format for dmsetup status on a snapshot device that we are
        # counting on here is as follows.
        # e.g. "0 8388608 snapshot 416/1048576" or "A B snapshot C/D"
        try:
            minInstDeltaDataLength = int((dmsetupOutput.split()[3]).split('/')[0])
            print >> sys.stderr, "genMinInstDelta data length is %d 512 byte sectors" % (minInstDeltaDataLength)
        except ValueError:
            raise InstallationError("Could not calculate amount of data used by genMinInstDelta")

        # tear down snapshot and loop devices
        rc = subprocess.call(["/sbin/dmsetup", "remove",
                              "livecd-creator-%d" %(os.getpid(),) ])
        if rc != 0:
            raise InstallationError("Could not remove genMinInstDelta snapshot device")
        osloop.lounsetup()
        minloop.lounsetup()

        # truncate the unused excess portion of the sparse file
        fd = os.open("%s/out/LiveOS/osmin" %(self.build_dir,), os.O_WRONLY )
        os.ftruncate(fd, minInstDeltaDataLength * 512)
        os.close(fd)

        ret = mksquashfs("osmin.img", ["osmin"],
                         "%s/out/LiveOS" %(self.build_dir,))
        if ret != 0:
                raise InstallationError("mksquashfs exited with error (%d)" %(ret,))
        try:
            os.unlink("%s/out/LiveOS/osmin" %(self.build_dir))
        except:
            pass

    def package(self):
        self.createSquashFS()
        self.createIso()
        self.implantIsoMD5()

class x86ImageCreator(ImageCreator):
    """ImageCreator for x86 machines"""
    def configureBootloader(self):
        """configure the boot loader"""
        os.makedirs(self.build_dir + "/out/isolinux")

        shutil.copyfile("%s/install_root/boot/vmlinuz-%s"
                        %(self.build_dir, self.get_kernel_version()),
                        "%s/out/isolinux/vmlinuz" %(self.build_dir,))

        shutil.copyfile("%s/install_root/boot/livecd-initramfs.img"
                        %(self.build_dir,),
                        "%s/out/isolinux/initrd.img" %(self.build_dir,))
        os.unlink("%s/install_root/boot/livecd-initramfs.img"
                  %(self.build_dir,))

        syslinuxfiles = ["isolinux.bin"]
        menus = ["vesamenu.c32", "menu.c32"]
        syslinuxMenu = None

        for m in menus:
            path = "%s/install_root/usr/lib/syslinux/%s" % (self.build_dir, m)
            if os.path.isfile(path):
                syslinuxfiles.append(m)
                syslinuxMenu=m
                break
        if syslinuxMenu is None:
            raise InstallationError("syslinux not installed : no suitable *menu.c32 found")

        isXen = False
        xen = glob.glob("%s/install_root/boot/xen.gz-*" %(self.build_dir,))
        if len(xen) > 0:
            shutil.copyfile(xen[0], "%s/out/isolinux/xen.gz" %(self.build_dir,))
            syslinuxfiles.append("mboot.c32")
            isXen = True

        for p in syslinuxfiles:
            path = "%s/install_root/usr/lib/syslinux/%s" % (self.build_dir, p)
            if not os.path.isfile(path):
                raise InstallationError("syslinux not installed : %s not found" % path)

            shutil.copy(path, "%s/out/isolinux/%s" % (self.build_dir, p))

        if os.path.exists("%s/install_root/usr/lib/anaconda-runtime/syslinux-vesa-splash.jpg" %(self.build_dir,)):
            shutil.copy("%s/install_root/usr/lib/anaconda-runtime/syslinux-vesa-splash.jpg" %(self.build_dir,),
                        "%s/out/isolinux/splash.jpg" %(self.build_dir,))
            have_background = "menu background splash.jpg"
        else:
            have_background = ""

        cfg = """
default %(menu)s
timeout 10

%(background)s
menu title Welcome to %(label)s!
menu color border 0 #ffffffff #00000000
menu color sel 7 #ffffffff #ff000000
menu color title 0 #ffffffff #00000000
menu color tabmsg 0 #ffffffff #00000000
menu color unsel 0 #ffffffff #00000000
menu color hotsel 0 #ff000000 #ffffffff
menu color hotkey 7 #ffffffff #ff000000
menu color timeout_msg 0 #ffffffff #00000000
menu color timeout 0 #ffffffff #00000000
menu color cmdline 0 #ffffffff #00000000
menu hidden
menu hiddenrow 5
""" %{"menu" : syslinuxMenu, "label": self.fs_label, "background" : have_background}

        stanzas = [("linux", "Boot %s" %(self.fs_label,), "")]
        if self._checkIsoMD5:
            stanzas.append( ("check", "Verify and boot %s" %(self.fs_label,), "check") )

        for (short, long, extra) in stanzas:
            if not isXen:
                cfg += """label %(short)s
  menu label %(long)s
  kernel vmlinuz
  append initrd=initrd.img root=CDLABEL=%(label)s rootfstype=iso9660 %(liveargs)s %(extra)s
""" %{"label": self.fs_label, "background" : have_background,
      "short": short, "long": long, "extra": extra,
      "liveargs": self._getKernelOptions()}
            else:
                cfg += """label %(short)s
  menu label %(long)s
  kernel mboot.c32
  append xen.gz --- vmlinuz --- initrd.img  root=CDLABEL=%(label)s rootfstype=iso9660 %(liveargs)s %(extra)s
""" %{"label": self.fs_label, "background" : have_background,
      "short": short, "long": long, "extra": extra,
      "liveargs": self._getKernelOptions()}


        memtest = glob.glob("%s/install_root/boot/memtest86*" %(self.build_dir,))
        if len(memtest) > 0:
            shutil.copy(memtest[0], "%s/out/isolinux/memtest" %(self.build_dir,))
            cfg += """label memtest
  menu label Memory Test
  kernel memtest
"""

        # add local boot
        #cfg += """label local
  #menu label Boot from local drive
  #localboot 0xffff"""

        cfgf = open("%s/out/isolinux/isolinux.cfg" %(self.build_dir,), "w")
        cfgf.write(cfg)
        cfgf.close()
        
        # TODO: enable external entitity to partipate in adding boot entries

    def createIso(self):
        """Write out the live CD ISO."""
        rc = subprocess.call(["/usr/bin/mkisofs", "-o", "%s.iso" %(self.fs_label,),
                         "-b", "isolinux/isolinux.bin",
                         "-c", "isolinux/boot.cat",
                         "-no-emul-boot", "-boot-load-size", "4",
                         "-boot-info-table",
                         "-J", "-r", "-hide-rr-moved", "-hide-joliet-trans-tbl",
                         "-V", "%s" %(self.fs_label,),
                         "%s/out" %(self.build_dir)])
        if rc != 0:
            raise InstallationError("ISO creation failed!")

    def _getRequiredPackages(self):
        ret = ["syslinux"]
        ret.extend(ImageCreator._getRequiredPackages(self))
        return ret

class ppcImageCreator(ImageCreator):
    def createIso(self):
        """write out the live CD ISO"""
        rc = subprocess.call(["/usr/bin/mkisofs", "-o", "%s.iso" %(self.fs_label,),
                         "-hfs", "-hfs-bless", "%s/out/ppc/mac" %(self.build_dir),
                         "-hfs-volid", "%s" %(self.fs_label,), "-part",
                         "-map", "%s/out/ppc/mapping" %(self.build_dir,),
                         "-J", "-r", "-hide-rr-moved", "-no-desktop",
                         "-V", "%s" %(self.fs_label,), "%s/out" %(self.build_dir)])
        if rc != 0:
            raise InstallationError("ISO creation failed!")

    def configureBootloader(self):
        """configure the boot loader"""
        havekernel = { 32: False, 64: False }

        os.makedirs(self.build_dir + "/out/ppc")

        # copy the mapping file to somewhere we can get to it later
        shutil.copyfile("%s/install_root/usr/lib/anaconda-runtime/boot/mapping" %(self.build_dir,),
                        "%s/out/ppc/mapping" %(self.build_dir,))

        # Copy yaboot and ofboot.b in to mac directory
        os.makedirs(self.build_dir + "/out/ppc/mac")
        shutil.copyfile("%s/install_root/usr/lib/anaconda-runtime/boot/ofboot.b" %(self.build_dir),
                        "%s/out/ppc/mac/ofboot.b" %(self.build_dir,))
        shutil.copyfile("%s/install_root/usr/lib/yaboot/yaboot" %(self.build_dir),
                        "%s/out/ppc/mac/yaboot" %(self.build_dir,))

        # Copy yaboot and ofboot.b in to chrp directory
        os.makedirs(self.build_dir + "/out/ppc/chrp")
        shutil.copyfile("%s/install_root/usr/lib/anaconda-runtime/boot/bootinfo.txt" %(self.build_dir),
                        "%s/out/ppc/bootinfo.txt" %(self.build_dir,))
        shutil.copyfile("%s/install_root/usr/lib/yaboot/yaboot" %(self.build_dir),
                        "%s/out/ppc/chrp/yaboot" %(self.build_dir,))
        subprocess.call(["/usr/sbin/addnote", "%s/out/ppc/chrp/yaboot" %(self.build_dir,)])

        os.makedirs(self.build_dir + "/out/ppc/ppc32")
        if not os.path.exists("%s/install_root/lib/modules/%s/kernel/arch/powerpc/platforms" %(self.build_dir, self.get_kernel_version())):
            havekernel[32] = True
            shutil.copyfile("%s/install_root/boot/vmlinuz-%s"
                            %(self.build_dir, self.get_kernel_version()),
                            "%s/out/ppc/ppc32/vmlinuz" %(self.build_dir,))
            shutil.copyfile("%s/install_root/boot/livecd-initramfs.img"
                            %(self.build_dir,),
                            "%s/out/ppc/ppc32/initrd.img" %(self.build_dir,))
            os.unlink("%s/install_root/boot/livecd-initramfs.img"
                      %(self.build_dir,))

        os.makedirs(self.build_dir + "/out/ppc/ppc64")
        if os.path.exists("%s/install_root/lib/modules/%s/kernel/arch/powerpc/platforms" %(self.build_dir, self.get_kernel_version())):
            havekernel[64] = True
            shutil.copyfile("%s/install_root/boot/vmlinuz-%s"
                            %(self.build_dir, self.get_kernel_version()),
                            "%s/out/ppc/ppc64/vmlinuz" %(self.build_dir,))
            shutil.copyfile("%s/install_root/boot/livecd-initramfs.img"
                            %(self.build_dir,),
                            "%s/out/ppc/ppc64/initrd.img" %(self.build_dir,))
            os.unlink("%s/install_root/boot/livecd-initramfs.img"
                      %(self.build_dir,))

        for bit in havekernel.keys():
            cfg = """
init-message = "Welcome to %(label)s"
timeout=6000

""" %{"label": self.fs_label}

            stanzas = [("linux", "Run from image", "")]
            if self._checkIsoMD5:
                stanzas.append( ("check", "Verify and run from image", "check") )

            for (short, long, extra) in stanzas:
                cfg += """

image=/ppc/ppc%(bit)s/vmlinuz
  label=%(short)s
  initrd=/ppc/ppc%(bit)s/initrd.img
  read-only
  append="root=CDLABEL=%(label)s rootfstype=iso9660 %(liveargs)s %(extra)s"
""" %{"label": self.fs_label, "short": short, "long": long, "extra": extra, "bit": bit, "liveargs": self._getKernelOptions()}

                if havekernel[bit]:
                    cfgf = open("%s/out/ppc/ppc%d/yaboot.conf" %(self.build_dir, bit), "w")
                    cfgf.write(cfg)
                    cfgf.close()
                else:
                    cfgf = open("%s/out/ppc/ppc%d/yaboot.conf" %(self.build_dir, bit), "w")
                    cfgf.write('init-message = "Sorry, this LiveCD does not support your hardware"')
                    cfgf.close()


        os.makedirs(self.build_dir + "/out/etc")
        if havekernel[32] and not havekernel[64]:
            shutil.copyfile("%s/out/ppc/ppc32/yaboot.conf" %(self.build_dir,),
                            "%s/out/etc/yaboot.conf" %(self.build_dir,))
        elif havekernel[64] and not havekernel[32]:
            shutil.copyfile("%s/out/ppc/ppc64/yaboot.conf" %(self.build_dir,),
                            "%s/out/etc/yaboot.conf" %(self.build_dir,))
        else:
            cfg = """
init-message = "\nWelcome to %(label)s!\nUse 'linux32' for 32-bit kernel.\n\n"
timeout=6000
default=linux

image=/ppc/ppc64/vmlinuz
	label=linux64
	alias=linux
	initrd=/ppc/ppc64/initrd.img
	read-only

image=/ppc/ppc32/vmlinuz
	label=linux32
	initrd=/ppc/ppc32/initrd.img
	read-only
""" %{"label": self.fs_label,}

            cfgf = open("%s/out/etc/yaboot.conf" %(self.build_dir,), "w")
            cfgf.write(cfg)
            cfgf.close()

        # TODO: build 'netboot' images with kernel+initrd, like mk-images.ppc

    def _getRequiredPackages(self):
        # For now we need anaconda-runtime, for bits like ofboot.b and
        # mapping files.
        ret = ["yaboot", "anaconda-runtime"]
        ret.extend(ImageCreator._getRequiredPackages(self))
        return ret

    def _getRequiredExcludePackages(self):
        # kind of hacky, but exclude memtest86+ on ppc so it can stay in cfg
        return ["memtest86+"]

class ppc64ImageCreator(ppcImageCreator):
    def _getRequiredExcludePackages(self):
        # FIXME: while kernel.ppc and kernel.ppc64 co-exist, we can't
        # have both
        return ["kernel.ppc", "memtest86+"]

def getImageCreator(fs_label):
    arch = rpmUtils.arch.getBaseArch()
    if arch in ("i386", "x86_64"):
        return x86ImageCreator(fs_label)
    elif arch in ("ppc",):
        return ppcImageCreator(fs_label)
    elif arch in ("ppc64",):
        return ppc64ImageCreator(fs_label)

    raise InstallationError("Architecture not supported!")

class Usage(Exception):
    def __init__(self, msg = None, no_error = False):
        Exception.__init__(self, msg, no_error)

def parse_options(args):
    parser = optparse.OptionParser()

    imgopt = optparse.OptionGroup(parser, "Image options",
                                  "These options define the created image.")
    imgopt.add_option("-c", "--config", type="string", dest="kscfg",
                      help="Path to kickstart config file")
    imgopt.add_option("-b", "--base-on", type="string", dest="base_on",
                      help="Add packages to an existing live CD iso9660 image.")
    imgopt.add_option("-f", "--fslabel", type="string", dest="fs_label",
                      help="File system label (default based on config name)")
    parser.add_option_group(imgopt)

    # options related to the config of your system
    sysopt = optparse.OptionGroup(parser, "System directory options",
                                  "These options define directories used on your system for creating the live image")
    sysopt.add_option("-t", "--tmpdir", type="string",
                      dest="tmpdir", default="/var/tmp",
                      help="Temporary directory to use (default: /var/tmp)")
    sysopt.add_option("", "--cache", type="string",
                      dest="cachedir", default=None,
                      help="Cache directory to use (default: private cache")
    parser.add_option_group(sysopt)

    # debug options not recommended for "production" images
    # Start a shell in the chroot for post-configuration.
    parser.add_option("-l", "--shell", action="store_true", dest="give_shell",
                      help=optparse.SUPPRESS_HELP)
    # Don't compress the image.
    parser.add_option("-s", "--skip-compression", action="store_true", dest="skip_compression",
                      help=optparse.SUPPRESS_HELP)

    (options, args) = parser.parse_args()
    if not options.kscfg or not os.path.isfile(options.kscfg):
        raise Usage("Kickstart config '%s' does not exist" %(options.kscfg,))
    if options.base_on and not os.path.isfile(options.base_on):
        raise Usage("Live CD ISO '%s' does not exist" %(options.base_on,))
    if options.fs_label and len(options.fs_label) > 32:
        raise Usage("CD labels are limited to 32 characters")

    return options

def main():
    try:
        options = parse_options(sys.argv[1:])
    except Usage, (msg, no_error):
        if no_error:
            out = sys.stdout
            ret = 0
        else:
            out = sys.stderr
            ret = 2
        if msg:
            print >> out, msg
        return ret

    if os.geteuid () != 0:
        print >> sys.stderr, "You must run livecd-creator as root"
        return 1

    if options.fs_label:
        fs_label = options.fs_label
    else:
        fs_label_prefix = 'livecd-'
        fs_label_suffix = time.strftime("%Y%m%d%H%M")
        name = ''
        if options.kscfg:
            name = os.path.basename(options.kscfg)
            idx = name.rfind('.')
            if idx >= 0:
                name = name[:idx]
        if name.startswith(fs_label_prefix):
            name = name[len(fs_label_prefix):]
        fs_label = fs_label_prefix + name + "-" + fs_label_suffix
        if len(fs_label) > 32:
            if name.startswith("livecd-"):
                name = name[len("livecd-"):]
            fs_label = name + "-" + fs_label_suffix
        if len(fs_label) > 32:
            name = name[:(32 - len(fs_label_suffix))]
            fs_label = name + "-" + fs_label_suffix
        print "Using label %s" % (fs_label,)

    target = getImageCreator(fs_label)
    target.skip_compression = options.skip_compression
    target.tmpdir = options.tmpdir

    try:
        target.parse(options.kscfg)

        target.setup(options.base_on, options.cachedir)

        target.install()

        if options.give_shell:
            print "Launching shell. Exit to continue."
            print "----------------------------------"
            target.launchShell()

        target.unmount()

        target.cleanupDeleted()
        target.genMinInstDelta()

        target.package()

    except InstallationError, e:
        print >> sys.stderr, "Error creating Live CD : %s" % e
        target.teardown()
        return 1
    except:
        exc = sys.exc_info()
        target.teardown()
        raise exc[0], exc[1], exc[2]
    target.teardown()

    return 0

if __name__ == "__main__":
    sys.exit(main())