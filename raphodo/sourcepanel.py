# Copyright (C) 2017-2022 Damon Lynch <damonlynch@gmail.com>

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
Display photo and video sources -- Devices and This Computer, as well as the Timeline
"""

__author__ = "Damon Lynch"
__copyright__ = "Copyright 2017-2022, Damon Lynch"

import logging
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QSplitter, QWidget, QVBoxLayout, QSizePolicy

from raphodo.viewutils import ScrollAreaNoFrame
from raphodo.proximity import TemporalProximityControls


class SourcePanel(ScrollAreaNoFrame):
    """
    Display Devices and This Computer sources, as well as the timeline
    """

    def __init__(self, rapidApp) -> None:
        super().__init__()
        assert rapidApp is not None
        self.rapidApp = rapidApp
        self.prefs = self.rapidApp.prefs

        self.setObjectName("sourcePanelScrollArea")

        self.sourcePanelWidget = QWidget(parent=self)
        self.sourcePanelWidget.setObjectName("sourcePanelWidget")

        self.splitter = QSplitter(parent=self.sourcePanelWidget)
        self.splitter.setObjectName("sourcePanelSplitter")
        self.splitter.setOrientation(Qt.Vertical)
        self.setWidget(self.sourcePanelWidget)
        self.setWidgetResizable(True)

        self.sourcePanelWidgetLayout = QVBoxLayout()
        self.sourcePanelWidgetLayout.setContentsMargins(0, 0, 0, 0)
        self.sourcePanelWidgetLayout.setSpacing(self.splitter.handleWidth())
        self.sourcePanelWidget.setLayout(self.sourcePanelWidgetLayout)

        self.temporalProximityInSplitter = True

        self.thisComputerBottomFrameConnection = None
        self.thisComputerAltBottomFrameConnection = None

    def showTemporalProximityOnly(self) -> bool:
        return not (
            self.rapidApp.sourceButton.isChecked()
            # on startup, the button state has not yet been set, so read the setting
            # directly
            or self.rapidApp.sourceButtonSetting()
        )

    def addSourceViews(self) -> None:
        """
        Add source widgets and timeline
        """

        self.rapidApp.deviceToggleView.setSizePolicy(
            QSizePolicy.MinimumExpanding, QSizePolicy.Fixed
        )

        self.sourcePanelWidgetLayout.addWidget(self.rapidApp.deviceToggleView, 0)
        self.splitter.addWidget(self.rapidApp.thisComputerToggleView)

        self.splitter.setCollapsible(0, False)

        if self.showTemporalProximityOnly():
            self.placeTemporalProximityInSourcePanel()
        else:
            self.placeTemporalProximityInSplitter()

        for widget in (
            self.rapidApp.deviceView,
            self.rapidApp.thisComputer,
            self.rapidApp.thisComputerToggleView.alternateWidget,
        ):
            self.verticalScrollBarVisible.connect(widget.containerVerticalScrollBar)

        for widget in self.rapidApp.temporalProximity.flexiFrameWidgets():
            self.verticalScrollBarVisible.connect(widget.containerVerticalScrollBar)
            self.horizontalScrollBarVisible.connect(widget.containerHorizontalScrollBar)

    def placeTemporalProximityInSplitter(self) -> None:
        self.splitter.addWidget(self.rapidApp.temporalProximity)
        self.sourcePanelWidgetLayout.addWidget(self.splitter)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.temporalProximityInSplitter = True

    def placeTemporalProximityInSourcePanel(self) -> None:
        self.sourcePanelWidgetLayout.addWidget(self.rapidApp.temporalProximity)
        self.temporalProximityInSplitter = False

    def exchangeTemporalProximityContainer(self) -> None:
        if self.temporalProximityInSplitter:
            self.placeTemporalProximityInSourcePanel()
        else:
            self.placeTemporalProximityInSplitter()

    def setDeviceToggleViewVisible(self, visible: bool) -> None:
        self.rapidApp.deviceToggleView.setVisible(visible)
        self.splitter.setVisible(visible)

    def setThisComputerToggleViewVisible(self, visible: bool) -> None:
        self.rapidApp.thisComputerToggleView.setVisible(visible)

    def setThisComputerBottomFrame(self, temporalProximityVisible: bool) -> None:
        """
        Connect or disconnect reaction of This Computer widget to the Scroll Area
        horizontal scroll bar becoming visible or not.

        Idea is to not rect when the Timeline is visible, and react when it is hidden,
        which is when the This Computer widget is the bottommost widget.
        :param temporalProximityVisible: whether the timeline is visible
        """

        if temporalProximityVisible:
            if self.thisComputerBottomFrameConnection:
                self.horizontalScrollBarVisible.disconnect(
                    self.thisComputerBottomFrameConnection
                )
                self.thisComputerBottomFrameConnection = None
            if self.thisComputerAltBottomFrameConnection:
                self.horizontalScrollBarVisible.disconnect(
                    self.thisComputerAltBottomFrameConnection
                )
                self.thisComputerAltBottomFrameConnection = None
            # Always show the bottom edge frame, regardless of what the scroll area
            # scrollbar is doing
            self.rapidApp.thisComputer.containerHorizontalScrollBar(False)
            self.rapidApp.thisComputerToggleView.alternateWidget.containerHorizontalScrollBar(
                False
            )
        else:
            if self.thisComputerBottomFrameConnection is None:
                self.thisComputerBottomFrameConnection = (
                    self.horizontalScrollBarVisible.connect(
                        self.rapidApp.thisComputer.containerHorizontalScrollBar
                    )
                )
            if self.thisComputerAltBottomFrameConnection is None:
                self.thisComputerAltBottomFrameConnection = (
                    self.horizontalScrollBarVisible.connect(
                        self.rapidApp.thisComputerToggleView.alternateWidget.containerHorizontalScrollBar
                    )
                )
            self.rapidApp.thisComputer.containerHorizontalScrollBar(
                self.horizontalScrollBar().isVisible()
            )
            self.rapidApp.thisComputerToggleView.alternateWidget.containerHorizontalScrollBar(
                self.horizontalScrollBar().isVisible()
            )


class LeftPanelContainer(QWidget):
    def __init__(
        self,
        sourcePanel: SourcePanel,
        temporalProximityControls: TemporalProximityControls,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(sourcePanel)
        layout.addWidget(temporalProximityControls)
        self.setLayout(layout)
