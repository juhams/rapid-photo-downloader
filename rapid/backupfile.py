#!/usr/bin/python
# -*- coding: latin1 -*-

### Copyright (C) 2011 - 2014 Damon Lynch <damonlynch@gmail.com>

### This program is free software; you can redistribute it and/or modify
### it under the terms of the GNU General Public License as published by
### the Free Software Foundation; either version 2 of the License, or
### (at your option) any later version.

### This program is distributed in the hope that it will be useful,
### but WITHOUT ANY WARRANTY; without even the implied warranty of
### MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
### GNU General Public License for more details.

### You should have received a copy of the GNU General Public License
### along with this program; if not, write to the Free Software
### Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301
### USA

import tempfile
import os
import errno
import hashlib

import shutil
import io

import logging

import rpdfile
import problemnotification as pn
import config

PHOTO_BACKUP = 1
VIDEO_BACKUP = 2
PHOTO_VIDEO_BACKUP = 3

from gettext import gettext as _

# from copyfiles import copy_file_metadata


# class BackupFiles(multiprocessing.Process):
#     def __init__(self, path, name,
#                  batch_size_MB, results_pipe, terminate_queue,
#                  run_event):
#         multiprocessing.Process.__init__(self)
#         self.results_pipe = results_pipe
#         self.terminate_queue = terminate_queue
#         self.batch_size_bytes = batch_size_MB * 1048576 # * 1024 * 1024
#         self.io_buffer = 1048576
#         self.path = path
#         self.mount_name = name
#         self.run_event = run_event
#
#
#     def check_termination_request(self):
#         """
#         Check to see this process has not been requested to immediately terminate
#         """
#         if not self.terminate_queue.empty():
#             x = self.terminate_queue.get()
#             # terminate immediately
#             logger.info("Terminating file backup")
#             return True
#         return False
#
#
#     def update_progress(self, amount_downloaded, total):
#         self.amount_downloaded = amount_downloaded
#         chunk_downloaded = amount_downloaded - self.bytes_downloaded
#         if (chunk_downloaded > self.batch_size_bytes) or (amount_downloaded == total):
#             self.bytes_downloaded = amount_downloaded
#             self.results_pipe.send((rpdmp.CONN_PARTIAL, (rpdmp.MSG_BYTES, (self.scan_pid, self.pid, self.total_downloaded + amount_downloaded, chunk_downloaded))))
#             if amount_downloaded == total:
#                 self.bytes_downloaded = 0
#
#     def backup_additional_file(self, dest_dir, full_file_name):
#         """Backs up small files like XMP or THM files"""
#         dest_name = os.path.join(dest_dir, os.path.split(full_file_name)[1])
#
#         try:
#             logger.debug("Backing up additional file %s...", dest_name)
#             shutil.copyfile(full_file_name, dest_name)
#             logger.debug("...backing up additional file %s succeeded", dest_name)
#         except:
#             logger.error("Backup of %s failed", full_file_name)
#
#         try:
#             copy_file_metadata(full_file_name, dest_name, logger)
#         except:
#             logger.error("Unknown error updating filesystem metadata when copying %s", full_file_name)
#
#     def run(self):
#
#         self.bytes_downloaded = 0
#         self.total_downloaded = 0
#
#         while True:
#
#             self.amount_downloaded = 0
#             move_succeeded, do_backup, rpd_file, path_suffix, backup_duplicate_overwrite, verify_file, download_count = self.results_pipe.recv()
#             if rpd_file is None:
#                 # this is a termination signal
#                 return None
#             # pause if instructed by the caller
#             self.run_event.wait()
#
#             if self.check_termination_request():
#                 return None
#
#             backup_succeeded = False
#             self.scan_pid = rpd_file.scan_pid
#
#             if move_succeeded and do_backup:
#                 self.total_reached = False
#
#                 if path_suffix is None:
#                     dest_base_dir = self.path
#                 else:
#                     dest_base_dir = os.path.join(self.path, path_suffix)
#
#
#                 dest_dir = os.path.join(dest_base_dir, rpd_file.download_subfolder)
#                 backup_full_file_name = os.path.join(
#                                     dest_dir,
#                                     rpd_file.download_name)
#
#                 if not os.path.isdir(dest_dir):
#                     # create the subfolders on the backup path
#                     try:
#                         logger.debug("Creating subfolder %s on backup device %s...", dest_dir, self.mount_name)
#                         os.makedirs(dest_dir)
#                         logger.debug("...backup subfolder created")
#                     except IOError as inst:
#                         # There is a tiny chance directory may have been created by
#                         # another process between the time it takes to query and
#                         # the time it takes to create a new directory.
#                         # Ignore such errors.
#                         if inst.errno <> errno.EEXIST:
#                             logger.error("Failed to create backup subfolder: %s", dest_dir)
#                             msg = "%s %s", inst.errno, inst.strerror
#                             logger.error(msg)
#                             rpd_file.add_problem(None, pn.BACKUP_DIRECTORY_CREATION, self.mount_name)
#                             rpd_file.add_extra_detail('%s%s' % (pn.BACKUP_DIRECTORY_CREATION, self.mount_name), msg)
#                             rpd_file.error_title = _('Backing up error')
#                             rpd_file.error_msg = \
#                                  _("Destination directory could not be created: %(directory)s\n") % \
#                                   {'directory': dest_dir,  } + \
#                                  _("Source: %(source)s\nDestination: %(destination)s") % \
#                                   {'source': rpd_file.download_full_file_name,
#                                    'destination': backup_full_file_name} + "\n" + \
#                                  _("Error: %(inst)s") % {'inst': msg}
#
#
#                 backup_already_exists = os.path.exists(backup_full_file_name)
#                 if backup_already_exists:
#                     if backup_duplicate_overwrite:
#                         rpd_file.add_problem(None, pn.BACKUP_EXISTS_OVERWRITTEN, self.mount_name)
#                         msg = _("Backup %(file_type)s overwritten") % {'file_type': rpd_file.title}
#                     else:
#                         rpd_file.add_problem(None, pn.BACKUP_EXISTS, self.mount_name)
#                         msg = _("%(file_type)s not backed up") % {'file_type': rpd_file.title_capitalized}
#
#                     rpd_file.error_title = _("Backup of %(file_type)s already exists") % {'file_type': rpd_file.title}
#                     rpd_file.error_msg = \
#                             _("Source: %(source)s\nDestination: %(destination)s") % \
#                              {'source': rpd_file.download_full_file_name, 'destination': backup_full_file_name} + "\n" + msg
#
#                 if backup_already_exists and not backup_duplicate_overwrite:
#                     logger.warning(msg)
#                 else:
#                     try:
#                         logger.debug("Backing up file %s on device %s...", download_count, self.mount_name)
#
#                         dest = io.open(backup_full_file_name, 'wb', self.io_buffer)
#                         src = io.open(rpd_file.download_full_file_name, 'rb', self.io_buffer)
#                         total = rpd_file.size
#                         amount_downloaded = 0
#                         while True:
#                             # first check if process is being terminated
#                             if self.check_termination_request():
#                                 logger.debug("Closing partially written temporary file")
#                                 dest.close()
#                                 src.close()
#                                 return None
#                             else:
#                                 chunk = src.read(self.io_buffer)
#                                 if chunk:
#                                     dest.write(chunk)
#                                     amount_downloaded += len(chunk)
#                                     self.update_progress(amount_downloaded, total)
#                                 else:
#                                     break
#                         dest.close()
#                         src.close()
#                         backup_succeeded = True
#                         if verify_file:
#                             md5 = hashlib.md5(open(backup_full_file_name).read()).hexdigest()
#                             if md5 <> rpd_file.md5:
#                                 backup_succeeded = False
#                                 logger.critical("%s file verification FAILED", rpd_file.name)
#                                 logger.critical("The %s did not back up correctly!", rpd_file.title)
#                                 rpd_file.add_problem(None, pn.BACKUP_VERIFICATION_FAILED, self.mount_name)
#                                 rpd_file.error_title = rpd_file.problem.get_title()
#                                 rpd_file.error_msg = _("%(problem)s\nFile: %(file)s") % \
#                                   {'problem': rpd_file.problem.get_problems(),
#                                    'file': rpd_file.download_full_file_name}
#
#                         logger.debug("...backing up file %s on device %s succeeded", download_count, self.mount_name)
#                         if backup_already_exists:
#                             logger.warning(msg)
#                     except (IOError, OSError) as inst:
#                         logger.error("Backup of %s failed", backup_full_file_name)
#                         msg = "%s %s", inst.errno, inst.strerror
#                         rpd_file.add_problem(None, pn.BACKUP_ERROR, self.mount_name)
#                         rpd_file.add_extra_detail('%s%s' % (pn.BACKUP_ERROR, self.mount_name), msg)
#                         rpd_file.error_title = _('Backing up error')
#                         rpd_file.error_msg = \
#                                 _("Source: %(source)s\nDestination: %(destination)s") % \
#                                  {'source': rpd_file.download_full_file_name, 'destination': backup_full_file_name} + "\n" + \
#                                 _("Error: %(inst)s") % {'inst': msg}
#                         logger.error("%s:\n%s", rpd_file.error_title, rpd_file.error_msg)
#
#                     if backup_succeeded:
#                         try:
#                             copy_file_metadata(rpd_file.download_full_file_name, backup_full_file_name, logger)
#                         except:
#                             logger.error("Unknown error updating filesystem metadata when copying %s", rpd_file.download_full_file_name)
#
#
#                 if not backup_succeeded:
#                     if rpd_file.status ==  config.STATUS_DOWNLOAD_FAILED:
#                         rpd_file.status = config.STATUS_DOWNLOAD_AND_BACKUP_FAILED
#                     else:
#                         rpd_file.status = config.STATUS_BACKUP_PROBLEM
#                 else:
#                     # backup any THM, audio or XMP files
#                     if rpd_file.download_thm_full_name:
#                         self.backup_additional_file(dest_dir,
#                                         rpd_file.download_thm_full_name)
#                     if rpd_file.download_audio_full_name:
#                         self.backup_additional_file(dest_dir,
#                                         rpd_file.download_audio_full_name)
#                     if rpd_file.download_xmp_full_name:
#                         self.backup_additional_file(dest_dir,
#                                         rpd_file.download_xmp_full_name)
#
#             self.total_downloaded += rpd_file.size
#             bytes_not_downloaded = rpd_file.size - self.amount_downloaded
#             if bytes_not_downloaded and do_backup:
#                 self.results_pipe.send((rpdmp.CONN_PARTIAL, (rpdmp.MSG_BYTES, (self.scan_pid, self.pid, self.total_downloaded, bytes_not_downloaded))))
#
#             self.results_pipe.send((rpdmp.CONN_PARTIAL, (rpdmp.MSG_FILE,
#                                    (backup_succeeded, do_backup, rpd_file))))
#
#
#
#
#
#
