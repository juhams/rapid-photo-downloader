#!/usr/bin/python3
__author__ = 'Damon Lynch'

# Copyright (C) 2011-2015 Damon Lynch <damonlynch@gmail.com>

# This file is part of Rapid Photo Downloader.
#
# Rapid Photo Downloader is free software: you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Rapid Photo Downloader is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Rapid Photo Downloader.  If not,
# see <http://www.gnu.org/licenses/>.

import os
import errno
import io
import shutil
import stat
import hashlib
import logging
import pickle

from PyQt5.QtCore import QSize, QIODevice, QBuffer

import problemnotification as pn
from camera import (Camera, CopyChunks)
import gphoto2 as gp

from interprocess import (WorkerInPublishPullPipeline, CopyFilesArguments,
                          CopyFilesResults)
from constants import FileType, DownloadStatus
from thumbnail import Thumbnail
from utilities import (GenerateRandomFileName, create_temp_dirs)
from rpdfile import RPDFile

from gettext import gettext as _

logging.basicConfig(format='%(levelname)s:%(asctime)s:%(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)


def copy_file_metadata(src, dst):
    """
    Copy all stat info (mode bits, atime, mtime, flags) from src to
    dst.

    Adapted from python's shutil.copystat().

    Necessary because with some NTFS file systems, there can be
    problems with setting filesystem metadata like permissions and
    modification time
    """

    st = os.stat(src)
    mode = stat.S_IMODE(st.st_mode)
    try:
        os.utime(dst, (st.st_atime, st.st_mtime))
    except OSError as inst:
        logging.warning(
            "Couldn't adjust file modification time when copying %s. %s: %s",
            src, inst.errno, inst.strerror)
    try:
        os.chmod(dst, mode)
    except OSError as inst:
        if logging:
            logging.warning(
                "Couldn't adjust file permissions when copying %s. %s: %s",
                src, inst.errno, inst.strerror)

    if hasattr(os, 'chflags') and hasattr(st, 'st_flags'):
        try:
            os.chflags(dst, st.st_flags)
        except OSError as why:
            for err in 'EOPNOTSUPP', 'ENOTSUP':
                if hasattr(errno, err) and why.errno == getattr(errno, err):
                    break
            else:
                raise


class CopyFilesWorker(WorkerInPublishPullPipeline):

    def __init__(self):
        super(CopyFilesWorker, self).__init__('CopyFiles')

    def cleanup_pre_stop(self):
        if self.dest is not None:
            self.dest.close()
        if self.src is not None:
            self.src.close()
        if self.camera is not None:
            if self.camera.camera_initialized:
                self.camera.free_camera()

    def update_progress(self, amount_downloaded, total):

        chunk_downloaded = amount_downloaded - self.bytes_downloaded
        if (chunk_downloaded > self.batch_size_bytes) or (
            amount_downloaded == total):
            self.bytes_downloaded = amount_downloaded
            self.content= pickle.dumps(CopyFilesResults(
                scan_id=self.scan_id, total_downloaded=self.total_downloaded
                + amount_downloaded, chunk_downloaded=chunk_downloaded),
               pickle.HIGHEST_PROTOCOL)
            self.send_message_to_sink()

            if amount_downloaded == total:
                self.bytes_downloaded = 0

    def copy_from_filesystem(self, rpd_file:RPDFile) -> bool:
        src_chunks = []
        try:
            self.dest = io.open(rpd_file.temp_full_file_name, 'wb',
                                self.io_buffer)
            self.src = io.open(rpd_file.full_file_name, 'rb', self.io_buffer)
            total = rpd_file.size
            amount_downloaded = 0
            while True:
                # first check if process is being stopped or paused
                self.check_for_command()

                chunk = self.src.read(self.io_buffer)
                if chunk:
                    self.dest.write(chunk)
                    if self.verify_file:
                        src_chunks.append(chunk)
                    amount_downloaded += len(chunk)
                    self.update_progress(amount_downloaded, total)
                else:
                    break
            self.dest.close()
            self.src.close()

            if self.verify_file:
                src_bytes = b''.join(src_chunks)
                rpd_file.md5 = hashlib.md5(src_bytes).hexdigest()

            return True
        except (IOError, OSError) as inst:
            rpd_file.add_problem(None,
                                 pn.DOWNLOAD_COPYING_ERROR_W_NO,
                                 {'filetype': rpd_file.title})
            rpd_file.add_extra_detail(
                pn.DOWNLOAD_COPYING_ERROR_W_NO_DETAIL,
                {'errorno': inst.errno, 'strerror': inst.strerror})

            rpd_file.status = DownloadStatus.download_failed

            rpd_file.error_title = rpd_file.problem.get_title()
            rpd_file.error_msg = _("%(problem)s\nFile: %(file)s") % \
                                 {
                                 'problem':
                                     rpd_file.problem.get_problems(),
                                 'file': rpd_file.full_file_name}

            logging.error("Failed to download file: %s",
                          rpd_file.full_file_name)
            logging.error(inst)
            self.update_progress(rpd_file.size, rpd_file.size)
            return False
        # except:
        #     rpd_file.add_problem(None,
        #                          pn.DOWNLOAD_COPYING_ERROR,
        #                          {'filetype': rpd_file.title})
        #     rpd_file.add_extra_detail(
        #         pn.DOWNLOAD_COPYING_ERROR_DETAIL,
        #         _("An unknown error occurred"))
        #
        #     rpd_file.status = DownloadStatus.download_failed
        #
        #     rpd_file.error_title = rpd_file.problem.get_title()
        #     rpd_file.error_msg = _("%(problem)s\nFile: %(file)s") % \
        #                          {
        #                          'problem':
        #                              rpd_file.problem.get_problems(),
        #                          'file': rpd_file.full_file_name}
        #
        #     logging.error("Failed to download file: %s",
        #                   rpd_file.full_file_name)
        #     self.update_progress(rpd_file.size, rpd_file.size)

    def copy_from_camera(self, rpd_file: RPDFile) -> bool:

        copy_chunks = self.camera.save_file_by_chunks(
                             dir_name=rpd_file.path,
                             file_name=rpd_file.name,
                             size=rpd_file.size,
                             dest_full_filename=rpd_file.temp_full_file_name,
                             progress_callback=self.update_progress,
                             check_for_command=self.check_for_command)

        if copy_chunks.copy_succeeded and self.verify_file:
            rpd_file.md5 = hashlib.md5(copy_chunks.src_bytes).hexdigest()

        return copy_chunks.copy_succeeded

    def copy_associate_file(self, rpd_file: RPDFile, temp_name: str,
                            dest_dir: str, associate_file_fullname: str,
                            file_type: str) -> str:

        ext = os.path.splitext(associate_file_fullname)[1]
        temp_thm_ext = '{}{}'.format(temp_name, ext)
        temp_full_name = os.path.join(dest_dir, temp_thm_ext)
        try:
            if rpd_file.from_camera:
                dir_name, file_name = \
                    os.path.split(rpd_file.thm_full_name)
                succeeded = self.camera.save_file(dir_name, file_name,
                                      temp_full_name)
                if not succeeded:
                    raise
            else:
                shutil.copyfile(rpd_file.thm_full_name,
                            temp_full_name)
            logging.debug("Copied %s file %s", file_type,
                          rpd_file.temp_thm_full_name)
        except (IOError, OSError) as inst:
            logging.error("Failed to download %s file: %s", file_type,
                          associate_file_fullname)
            logging.error("%s: %s", inst.errno, inst.strerror)
            return None
        except:
            logging.error("Failed to download %s file: %s", file_type,
                          associate_file_fullname)
            return None

        # Adjust file modification times and other file system metadata
        try:
            if rpd_file.from_camera:
                os.utime(temp_full_name, (rpd_file.modification_time,
                                          rpd_file.modification_time))
            else:
                copy_file_metadata(associate_file_fullname,
                               temp_full_name)
        except:
            logging.warning(
                "Could not update filesystem metadata when "
                "copying %s",
                rpd_file.thm_full_name)
        return temp_full_name


    def do_work(self):
        args = pickle.loads(self.content)
        """:type : CopyFilesArguments"""

        self.scan_id = args.scan_id
        self.verify_file = args.verify_file

        # Initialize use of camera only if it's needed
        self.camera = None

        random_filename = GenerateRandomFileName()

        self.io_buffer = 1024 * 1024
        self.batch_size_bytes = 5 * 1024 * 1024

        self.bytes_downloaded = 0
        self.total_downloaded = 0

        photo_temp_dir, video_temp_dir = create_temp_dirs(
            args.photo_download_folder, args.video_download_folder)

        # Notify main process of temp directory names
        self.content = pickle.dumps(CopyFilesResults(
                    scan_id=args.scan_id,
                    photo_temp_dir=photo_temp_dir,
                    video_temp_dir=video_temp_dir),
                    pickle.HIGHEST_PROTOCOL)
        self.send_message_to_sink()

        for idx, rpd_file in enumerate(args.files):

            self.dest = self.src = None

            if rpd_file.file_type == FileType.photo:
                dest_dir = photo_temp_dir
            else:
                dest_dir = video_temp_dir

            # Three scenarios:
            # 1. Downloading from device with file system we can directly
            # access
            # 2. Downloading from camera using libgphoto2
            # 3. Downloading from camera where we've already cached at
            # least some of the files

            if rpd_file.cache_full_file_name:
                # Scenario 3
                temp_file_name = os.path.split(
                    rpd_file.cache_full_file_name)[1]
                temp_name = os.path.splitext(temp_file_name)[0]
                temp_full_file_name = os.path.join(dest_dir,temp_file_name)
                try:
                    # The download folder may have changed since the scan
                    # occurred, so cannot assume it's on the same filesystem.
                    # Fortunately that doesn't matter when using shutil.move().
                    # The assumption here is that most of these images will be
                    # relatively small jpegs being copied locally
                    shutil.move(rpd_file.cache_full_file_name,
                                temp_full_file_name)
                    os.utime(temp_full_file_name, (rpd_file.modification_time,
                                          rpd_file.modification_time))
                    copy_succeeded = True
                    if self.verify_file:
                        rpd_file.md5 = hashlib.md5(open(
                            temp_full_file_name).read()).hexdigest()
                except (IOError, OSError) as inst:
                    copy_succeeded = False
                    #TODO log error
                self.update_progress(rpd_file.size, rpd_file.size)

            else:
                # Scenario 1 or 2
                # Generate temporary name 5 digits long, because we cannot
                # guarantee the source does not have duplicate file names in
                # different directories, and here we are copying the files into
                # a single directory
                temp_name = random_filename.name()
                temp_name_ext = '{}.{}'.format(temp_name, rpd_file.extension)
                temp_full_file_name = os.path.join(dest_dir, temp_name_ext)


            rpd_file.temp_full_file_name = temp_full_file_name

            if not rpd_file.cache_full_file_name:
                if rpd_file.from_camera:
                    # Scenario 2
                    if self.camera is None:
                        self.camera = Camera(args.device.camera_model,
                                        args.device.camera_port)
                        if not self.camera.camera_initialized:
                            #TODO notify user using problem report
                            pass

                    if not self.camera.camera_initialized:
                        copy_succeeded = False
                        #TODO log error
                        self.update_progress(rpd_file.size, rpd_file.size)
                    else:
                        copy_succeeded = self.copy_from_camera(rpd_file)
                else:
                    # Scenario 1
                    copy_succeeded = self.copy_from_filesystem(rpd_file)


            # increment this amount regardless of whether the copy actually
            # succeeded or not. It's necessary to keep the user informed.
            self.total_downloaded += rpd_file.size

            if copy_succeeded:
                try:
                    copy_file_metadata(rpd_file.full_file_name,
                                       temp_full_file_name)
                except:
                    logging.warning(
                        "Could not update filesystem metadata when "
                        "copying %s",
                        rpd_file.full_file_name)

            # copy THM (video thumbnail file) if there is one
            if copy_succeeded and rpd_file.thm_full_name:
                rpd_file.temp_thm_full_name = self.copy_associate_file(
                    rpd_file, temp_name, dest_dir, rpd_file.thm_full_name,
                    'video THM')

            #copy audio file if there is one
            if copy_succeeded and rpd_file.audio_file_full_name:
                rpd_file.temp_audio_full_name = self.copy_associate_file(
                    rpd_file, temp_name, dest_dir,
                    rpd_file.audio_file_full_name, 'audio')


            if (copy_succeeded and rpd_file.generate_thumbnail and
                    args.generate_thumbnails):
                thumbnail_maker = Thumbnail(rpd_file, camera=None,
                        thumbnail_quality_lower=args.thumbnail_quality_lower,
                        use_temp_file=True)
                thumbnail_icon = thumbnail_maker.get_thumbnail(size=QSize(
                    100,100))
                buffer = QBuffer()
                buffer.open(QIODevice.WriteOnly)
                thumbnail_icon.save(buffer, "PNG")
                thumbnail_data = buffer.data()
            else:
                thumbnail_data = None

            if rpd_file.metadata is not None:
                rpd_file.metadata = None

            download_count = idx + 1

            self.content =  pickle.dumps(CopyFilesResults(
                                            copy_succeeded=copy_succeeded,
                                            rpd_file=rpd_file,
                                            download_count=download_count,
                                            png_data=thumbnail_data),
                                            pickle.HIGHEST_PROTOCOL)
            self.send_message_to_sink()


        if self.camera is not None:
            self.camera.free_camera()

        self.send_finished_command()


if __name__ == "__main__":
    copy = CopyFilesWorker()

