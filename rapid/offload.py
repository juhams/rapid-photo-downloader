#!/usr/bin/python3
__author__ = 'Damon Lynch'

# Copyright (C) 2015 Damon Lynch <damonlynch@gmail.com>

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

import pickle
import logging

from interprocess import DaemonProcess, OffloadData, OffloadResults
from proximity import TemporalProximityGroups
from viewutils import SortedListItem

logging.basicConfig(format='%(levelname)s:%(asctime)s:%(message)s',
                    datefmt='%H:%M:%S',
                    level=logging.DEBUG)


class OffloadWorker(DaemonProcess):
    def __init__(self):
        super().__init__('Offload')

    def run(self):
        while True:
            directive, content = self.receiver.recv_multipart()

            self.check_for_command(directive, content)

            data = pickle.loads(content) # type: OffloadData
            if data.thumbnail_rows:
                groups = TemporalProximityGroups(data.thumbnail_rows,
                                                 data.proximity_seconds)
                self.content = pickle.dumps(OffloadResults(
                    proximity_groups=groups),
                    pickle.HIGHEST_PROTOCOL)
                self.send_message_to_sink()

if __name__ == '__main__':
    offload = OffloadWorker()
    offload.run()