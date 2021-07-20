# Copyright (C) 2021 Damon Lynch <damonlynch@gmail.com>

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

"""
Utility functions that use subprocess to call primarily libimobiledevice utilities
to handle iOS devices.

There is a Python binding to libimobiledevice, but at the time of writing it is
undocumented and very difficult to use.
"""

__author__ = 'Damon Lynch'
__copyright__ = "Copyright 2021, Damon Lynch."

import logging
import shutil
from typing import Optional
import subprocess

from raphodo.cameraerror import iOSDeviceError
from raphodo.constants import CameraErrorCode
from raphodo.utilities import create_temp_dir

# Utilities for identifying, pairing, and mounting iOS devices
idevicename_cmd = shutil.which('idevicename')
idevicepair_cmd = shutil.which('idevicepair')
ifuse_cmd = shutil.which('ifuse')
fusermount_cmd = shutil.which('fusermount')

# True if all iOS related utility programs are present on the system
utilities_present = not None in (idevicename_cmd, idevicepair_cmd, ifuse_cmd, fusermount_cmd)


def idevice_serial_to_udid(serial: str) -> str:
    """
    Generate udid for imobiledevice utilities from serial number

    :param serial: udev device serial number
    :return: udid suitable for imobiledevice utilities
    """

    try:
        assert len(serial) == 24
    except Exception:
        logging.error("Could not generate Apple udid from device serial number %s", serial)
        return ''

    return '{}-{}'.format(serial[:8], serial[8:])


def idevice_run_command(command: str,
                        udid: str,
                        argument_before_option: Optional[str] = '',
                        argument: Optional[str] = '',
                        display_name: Optional[str] = '',
                        warning_only: Optional[bool] = False,
                        supply_udid_as_arg: Optional[bool] = True,
                        camera_error_code: Optional[CameraErrorCode] = CameraErrorCode.pair) -> str:
    """
    Run a command and raise an error if it fails

    :param command: command to run, e.g. idevicename_cmd
    :param udid: iOS device udid, used to perform operations on specific device
    :param argument_before_option: argument to pass command before any '-u udid' argument
    :param argument: argument to pass command after any '-u udid' argument
    :param display_name: iOS name for use in error messages
    :param warning_only: do not raise an error, but instead log a warning
    :param supply_udid_as_arg: if True, add '-u udid' argument to command
    :param camera_error_code: error code to raise when something goes wrong
    :return: command's stdout / stderr
    """

    cmd = [command]
    if command == fusermount_cmd:
        cmd.append('-u')  # Note: nothing to to with udid. Simply instructs fusermount to unmount.
    if argument_before_option:
        cmd.append(argument_before_option)
    if supply_udid_as_arg:
        cmd.append('-u')
        cmd.append(udid)
    if argument:
        cmd.append(argument)

    try:
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True
        )
    except subprocess.CalledProcessError as e:
        if warning_only:
            logging.warning('Error running program for %s', display_name or udid)
            return ''
        else:
            raise iOSDeviceError(
                camera_error_code, e.returncode, e.output.decode(), udid, display_name
            )
    return result.stdout.decode()


def idevice_get_name(udid: str) -> str:
    """
    Determine name of idevice using its udid

    :param udid: Apple device udid in format used by imobiledevice utilities
    :return: output of idevicename
    """

    return idevice_run_command(
        udid=udid, command=idevicename_cmd, warning_only=True,
        camera_error_code=CameraErrorCode.devicename
    ).strip()


def idevice_run_idevicepair_command(udid: str, display_name: str, argument: str) -> str:
    """
    Run idevicepair with argument and return result
    :param udid: iOS device udid, used to perform operations on specific device
    :param display_name: iOS name for use in error messages
    :param argument: idevicepair argument to run
    :return: command's stdout / stderr
    """

    assert argument in ('validate', 'list', 'pair')
    return idevice_run_command(
        udid=udid, display_name=display_name, command=idevicepair_cmd, argument=argument
    )


def idevice_in_pairing_list(udid: str, display_name: str) -> bool:
    """Check if iOS device is in list of paired devices"""

    logging.debug("Checking if '%s' is already in iOS device pairing list", display_name)
    result = idevice_run_idevicepair_command(udid=udid, display_name=display_name, argument='list')
    return udid in result


def idevice_validate_pairing(udid: str, display_name: str):
    """
    Validate if iOS device has already been paired.
    Raises error on failure.
    """

    idevice_run_idevicepair_command(udid=udid, display_name=display_name, argument='validate')
    logging.info("Successfully validated pairing of '%s'", display_name)


def idevice_pair(udid: str, display_name: str):
    """Pair iOS device"""

    idevice_run_idevicepair_command(udid=udid, display_name=display_name, argument='pair')
    logging.debug("Successfully paired '%s'", display_name)


def idevice_do_mount(udid: str, display_name: str) -> str:
    """
    Mount an iOS device that has already been paired.
    :param udid: iOS device udid, used to perform operations on specific device
    :param display_name: display_name: iOS name for use in error messages
    :return: FUSE mount point
    """

    logging.info("Mounting iOS device '%s' using FUSE", display_name)

    mount_point = idevice_generate_mount_point(udid=udid)

    idevice_run_command(
        udid=udid, command=ifuse_cmd, display_name=display_name,
        argument_before_option=mount_point, camera_error_code=CameraErrorCode.mount
    )
    return mount_point


def idevice_do_unmount(udid: str, display_name: str, mount_point: str):
    """
    Unmount an iOS device that was mounted using FUSE
    :param udid: iOS device udid, used to perform operations on specific device
    :param display_name: display_name: iOS name for use in error messages
    :return: FUSE mount point
    """

    logging.info("Unmounting iOS device '%s' from FUSE mount", display_name)

    idevice_run_command(
        udid=udid, command=fusermount_cmd, display_name=display_name,
        argument=mount_point, camera_error_code=CameraErrorCode.mount,
        supply_udid_as_arg=False,
    )


def idevice_generate_mount_point(udid: str) -> str:
    """
    Create a temporary directory in which to mount iOS device using FUSE
    :param udid: iOS device udid, used to perform operations on specific device
    :return: full path to the temp dir
    """

    temp_dir = create_temp_dir(prefix='rpd-tmp-{}-'.format(udid))
    logging.debug("Created temp mount point %s", temp_dir)
    return temp_dir