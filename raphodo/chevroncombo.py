# Copyright (C) 2016-2021 Damon Lynch <damonlynch@gmail.com>

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
Combo box with a chevron selector
"""

__author__ = "Damon Lynch"
__copyright__ = "Copyright 2011-2021, Damon Lynch"

from PyQt5.QtWidgets import QComboBox, QLabel, QSizePolicy
from PyQt5.QtGui import QFontMetrics, QFont, QPainter, QColor
from PyQt5.QtCore import Qt, QSize, QPoint, QPointF

import raphodo.qrc_resources as qrc_resources
from raphodo.viewutils import darkModePixmap


class ChevronCombo(QComboBox):
    """
    Combo box with a chevron selector
    """

    def __init__(self, in_panel: bool = False, parent=None) -> None:
        """
        :param in_panel: if True, widget color set to background color,
         else set to window color
        """
        super().__init__(parent)
        # if in_panel:
        #     color = "background"
        # else:
        #     color = "window"
        # self.label_style = """
        # QLabel {border-color: palette(%(color)s); border-width: 1px; border-style: solid;}
        # """ % dict(
        #     color=color
        # )

    def paintEvent(self, event):
        painter = QPainter(self)
        width = int(QFontMetrics(QFont()).height() * (2 / 3))
        size = QSize(width, width)
        pixmap = darkModePixmap(path=":/icons/chevron-down.svg", size=size)
        x = self.rect().width() - width - 5
        y = self.rect().center().y() - width / 2
        p = QPointF(x, y)
        painter.drawPixmap(p, pixmap)

        painter.setPen(self.palette().windowText().color())
        # painter.drawText(self.rect().bottomLeft(), self.currentText())
        # print(self.currentText())
        # painter.drawRect(self.rect())
        # print(QFontMetrics(self.font()).height())

        painter.drawText(
            self.rect(), Qt.AlignVCenter | Qt.AlignLeft, self.currentText()
        )

    def makeLabel(self, text: str) -> QLabel:
        label = QLabel(text)
        # Add an invisible border to make the label vertically align with the comboboxes
        # Otherwise it's off by 1px
        # TODO perhaps come up with a better way to solve this alignment problem
        # label.setStyleSheet(self.label_style)
        label.setAlignment(Qt.AlignBottom)
        label.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Maximum)
        return label
