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


__author__ = "Damon Lynch"
__copyright__ = "Copyright 2021, Damon Lynch."

from collections import OrderedDict
import configparser
import enum
from pathlib import Path, PurePosixPath
import logging
import os
import re
import shlex
import subprocess
from typing import NamedTuple, Optional, Tuple, Set, List, Dict
import webbrowser

from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot, QTimer, Qt
from PyQt5.QtGui import QTextDocument
from PyQt5.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QGridLayout,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QAbstractScrollArea,
    QDialogButtonBox,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QButtonGroup,
    QAbstractButton,
    QTextBrowser,
    QLabel,
    QSplitter,
    QWidget,
    QMessageBox,
)

from raphodo.constants import WindowsDriveType
from raphodo.preferences import Preferences, WSLWindowsDrivePrefs
from raphodo.viewutils import (
    translateDialogBoxButtons,
    CheckBoxDelegate,
    standardMessageBox,
)
from raphodo.utilities import existing_parent_for_new_dir, make_internationalized_list
from raphodo.sudocommand import run_commands_as_sudo, SudoException, SudoExceptionCode


class WindowsDrive(NamedTuple):
    drive_letter: str
    label: str
    drive_type: WindowsDriveType


class WindowsDriveMount(NamedTuple):
    drive_letter: str
    label: str
    mount_point: str
    drive_type: WindowsDriveType
    system_mounted: bool


class MountTask(enum.Enum):
    remove_existing_file = enum.auto()
    create_directory = enum.auto()
    mount_drive = enum.auto()
    unmount_drive = enum.auto()


class MountOp(NamedTuple):
    task: MountTask
    path: Path
    drive: str
    cmd: str


class MountPref(NamedTuple):
    auto_mount: bool
    auto_unmount: bool


class MountOpHumanReadable:
    human_hr = {
        MountTask.remove_existing_file: _("Remove existing file <tt>%(path)s</tt>"),
        MountTask.create_directory: _("Create directory <tt>%(path)s</tt>"),
        MountTask.mount_drive: _(
            "Mount drive <tt>%(drive)s:</tt> at <tt>%(path)s</tt>"
        ),
        MountTask.unmount_drive: _(
            "Unmount drive <tt>%(drive)s:</tt> from <tt>%(path)s</tt>"
        ),
    }

    def mount_task_human_readable(self, op: MountOp) -> str:
        """
        Create human readable versions of mount operations
        :param op: operation to perform and its parameters
        :return: operation in human readable form
        """

        task_hr = self.human_hr[op.task]
        if op.task in (MountTask.unmount_drive, MountTask.mount_drive):
            task_hr = task_hr % {"drive": op.drive, "path": op.path}
        else:
            task_hr = task_hr % {"path": op.path}
        return task_hr


def make_mount_op_cmd(
    task: MountTask,
    drive_letter: str,
    path: Path,
    uid: Optional[int] = None,
    gid: Optional[int] = None,
) -> str:
    """
    Create command to be via subprocess.Popen() call.

    :param task: task to perform
    :param drive_letter: windows drive letter
    :param path: path of mount point, directory or file
    :param uid: user's user id
    :param gid: user's group id
    :return:  the command to run
    """

    if task == MountTask.mount_drive:
        if has_fstab_entry(drive_letter=drive_letter, mount_point=str(path)):
            return f"mount {path}"
        else:
            return rf"mount -t drvfs -o uid={uid},gid={gid},noatime {drive_letter.upper()}:\\ {path}"
    elif task == MountTask.unmount_drive:
        return f"umount {path}"
    elif task == MountTask.create_directory:
        return f"mkdir {path}"
    elif task == MountTask.remove_existing_file:
        # TODO add code to move file to user's home directory first, or else just remove this altogether
        return f"rm {path}"
    raise NotImplementedError


def has_fstab_entry(drive_letter: str, mount_point: str) -> bool:
    """
    Determine if the drive letter and mount point are in /etc/fstab

    :param drive_letter: Windows drive letter
    :param mount_point: mount point the drive should be mounted at
    :return: True if located, else False
    """

    with open("/etc/fstab") as f:
        fstab = f.read()
    # strip any extraneous trailing slash
    mount_point = str(PurePosixPath(mount_point))
    regex = rf"^{drive_letter}:\\?\s+{mount_point}/?\s+drvfs"
    m = re.search(regex, fstab, re.IGNORECASE | re.MULTILINE)
    return m is not None


def determine_mount_ops(
    do_mount: bool,
    drive_letter: str,
    mount_point: str,
    uid: int,
    gid: int,
    wsl_mount_root: Path,
) -> List[MountOp]:
    """
    Generator sequence of operations to mount or unmount a Windows drive

    :param do_mount: Whether to mount or unmount
    :param drive_letter: Windows drive letter
    :param mount_point: Existing or desired mount point
    :param uid: User's user ID
    :param gid: User's group ID
    :param wsl_mount_root: where WSL mounts drives, e.g. /mnt
    :return: List of operations required to mount or unmount the windows drive
    """

    tasks = []  # type: List[MountOp]
    if not mount_point:
        mount_point = wsl_standard_mount_point(wsl_mount_root, drive_letter)
    if do_mount:
        mp = Path(mount_point)
        if mp.is_mount():
            return tasks
        create_dir = False
        if mp.exists():
            if not mp.is_dir():
                tasks.append(
                    MountOp(
                        task=MountTask.remove_existing_file,
                        path=mp,
                        drive=drive_letter,
                        cmd=make_mount_op_cmd(
                            task=MountTask.remove_existing_file,
                            drive_letter=drive_letter,
                            path=mp,
                        ),
                    )
                )
                create_dir = True
        else:
            create_dir = True
        if create_dir:
            tasks.append(
                MountOp(
                    task=MountTask.create_directory,
                    path=mp,
                    drive=drive_letter,
                    cmd=make_mount_op_cmd(
                        task=MountTask.create_directory,
                        drive_letter=drive_letter,
                        path=mp,
                    ),
                )
            )
        tasks.append(
            MountOp(
                task=MountTask.mount_drive,
                path=mp,
                drive=drive_letter,
                cmd=make_mount_op_cmd(
                    task=MountTask.mount_drive,
                    drive_letter=drive_letter,
                    path=mp,
                    uid=uid,
                    gid=gid,
                ),
            )
        )
    else:
        mp = Path(mount_point)
        if mp.is_mount():
            tasks.append(
                MountOp(
                    task=MountTask.unmount_drive,
                    path=mp,
                    drive=drive_letter,
                    cmd=make_mount_op_cmd(
                        task=MountTask.unmount_drive,
                        drive_letter=drive_letter,
                        path=mp,
                    ),
                )
            )

    return tasks


def do_mount_drives_op(
    drives: List[WindowsDriveMount], pending_ops: OrderedDict, parent, is_do_mount: bool
) -> bool:
    """
    Mount or unmount the Windows drives, prompting the user for the root password if
    necessary.

    If the user cancels the operation, an SudoException is raised.

    :param drives: List of drives to mount or unmount
    :param pending_ops: The operations required to mount unmount the drives
    :param parent: Parent window to attach the password entry message box to
    :param is_do_mount: True if mounting the drives, else False
    :return: True if the operations all completed successfully, else False
    """

    if is_do_mount:
        op_lower = "mount"
        op_cap = "Mount"
    else:
        op_lower = "unmount"
        op_cap = "Unmount"

    drive_info = [f"{drive.drive_letter}: ({drive.label})" for drive in drives]
    info_list = make_internationalized_list(drive_info)
    if is_do_mount:
        if len(drive_info) > 1:
            title = _("Mount drives %s") % info_list
        else:
            title = _("Mount drive %s") % info_list
    else:
        if len(drive_info) > 1:
            title = _("Unmount drives %s") % info_list
        else:
            title = _("Unmount drive %s") % info_list
    logging.info("%s drives %s", op_cap, info_list)

    icon = ":/icons/drive-removable-media.svg"
    failures = []
    all_drive_ops_completed_ok = True

    for drive, mount_ops in pending_ops.items():
        cmds = [op.cmd for op in mount_ops]
        try:
            results = run_commands_as_sudo(
                cmds=cmds, parent=parent, title=title, icon=icon
            )
        except SudoException as e:
            assert e.code == SudoExceptionCode.command_cancelled
            logging.debug(
                "%s %s (%s): cancelled by user. " "Not mounting any remaining drives.",
                op_cap,
                drive.drive_letter,
                drive.label,
            )
            # raise the exception to be handled by the caller
            raise
        else:
            return_code = results[-1].return_code
            if return_code != 0:
                # a command failed
                logging.warning(
                    "Failed to %s %s: (%s) : %s",
                    op_lower,
                    drive.drive_letter.upper(),
                    drive.label,
                    results[-1].stderr,
                )
                failures.append((drive, results[-1].stderr))
            else:
                logging.debug(
                    "Successfully %sed %s: (%s)",
                    op_lower,
                    drive.drive_letter.upper(),
                    drive.label,
                )

    if failures:
        failure_info = [
            f"{failure[0].drive_letter}: ({failure[0].label})" for failure in failures
        ]
        fail_list = make_internationalized_list(failure_info)
        failure_messages = "; ".join([failure[1] for failure in failures])
        if len(failures) > 1:
            if is_do_mount:
                # Translators: this error message is displayed when more than one Windows drive fails to mount within Windows Subsystem for Linux
                message = (
                    _("Sorry, an error occurred when mounting drives %s.") % fail_list
                )
            else:
                # Translators: this error message is displayed when more than one Windows drive fails to unmount within Windows Subsystem for Linux
                message = (
                    _("Sorry, an error occurred when unmounting drives %s.") % fail_list
                )
        else:
            if is_do_mount:
                # Translators: this error message is displayed when one Windows drive fails to mount within Windows Subsystem for Linux
                message = (
                    _("Sorry, an error occurred when mounting drive %s.") % fail_list
                )
            else:
                # Translators: this error message is displayed when one Windows drive fails to unmount within Windows Subsystem for Linux
                message = (
                    _("Sorry, an error occurred when unmounting drive %s.") % fail_list
                )

        message = f"{message}<br><pre>{failure_messages}.</pre>"
        msgBox = standardMessageBox(
            message=message,
            standardButtons=QMessageBox.Ok,
            parent=parent,
            rich_text=True,
            iconType=QMessageBox.Warning,
        )
        msgBox.exec()
        all_drive_ops_completed_ok = False

    return all_drive_ops_completed_ok


class WSLWindowsDrivePrefsInterface:
    """
    An interface to the QSettings based method to store whether to auto mount or
    unmount Windows drives.

    Abstraction layer so program preferences do not need to know about implementation
    details in the UI.
    """

    def __init__(self, prefs: Preferences) -> None:
        self.prefs = prefs
        # Keep a copy of the live preferences.
        # If something else changes the prefs, then this will be stale.
        # Currently do not check to verify this is not stale.
        self.drives = prefs.get_wsl_drives()

    def drive_prefs(self, drive: WindowsDriveMount) -> MountPref:
        """
        Get auto mount and auto unmount prefs for this Windows drive.

        :param drive: drive to get prefs for
        :return: Tuple of auto mount and auto unmount
        """

        for d in self.drives:
            if d.drive_letter == drive.drive_letter and d.label == drive.label:
                return MountPref(auto_mount=d.auto_mount, auto_unmount=d.auto_unmount)
        return MountPref(auto_mount=False, auto_unmount=False)

    def set_prefs(
        self, drive: WindowsDriveMount, auto_mount: bool, auto_unmount: bool
    ) -> None:
        """
        Set auto mount and auto unmount prefs for this Windows drive.

        :param drive: drive to get prefs for
        :param auto_mount: auto mount pref
        :param auto_unmount: auto unmount pref
        """

        if auto_mount or auto_unmount:
            updated_pref = WSLWindowsDrivePrefs(
                drive_letter=drive.drive_letter,
                label=drive.label,
                auto_mount=auto_mount,
                auto_unmount=auto_unmount,
            )
        else:
            # Filter out default value of False, False
            updated_pref = None

        updated_drives_prefs = [
            d
            for d in self.drives
            if d.drive_letter != drive.drive_letter or d.label != drive.label
        ]
        if updated_pref is not None:
            updated_drives_prefs.append(updated_pref)
        self.drives = updated_drives_prefs
        self.prefs.set_wsl_drives(drives=self.drives)


class WslMountDriveDialog(QDialog):
    """
    Dialog window containing Windows drives and mounting options.

    Deals with "System" drives (drives mounted by WSL before this program was run),
    and "User" drives (drives mounted by the user in this program).
    """

    def __init__(
        self,
        drives: List[WindowsDriveMount],
        prefs: Preferences,
        windrive_prefs: WSLWindowsDrivePrefsInterface,
        wsl_mount_root: Path,
        parent: "RapidWindow" = None,
    ) -> None:
        """
        Open the dialogue window to show Windows drive mounts

        :param drives: List of Windows drives detected on the system
        :param prefs: main program preferences
        :param windrive_prefs: Interface to the windows drives preferences
        :param wsl_mount_root: where WSL mounts Windows drives
        :param parent: RapidApp main window
        """

        super().__init__(parent=parent)

        self.prefs = prefs
        self.windrive_prefs = windrive_prefs
        self.wsl_mount_root = wsl_mount_root

        self.driveTable = None  # type: Optional[QTableWidget]

        #  OrderedDict[drive: List[MountOp]]
        self.pending_mount_ops = OrderedDict()
        self.pending_unmount_ops = OrderedDict()

        self.uid = os.getuid()
        self.gid = os.getgid()

        self.make_mount_op_hr = MountOpHumanReadable()

        self.setWindowTitle(_("Windows Drives"))

        self.autoMountCheckBox = QCheckBox(
            _("Enable automatic mounting of Windows drives")
        )
        self.autoMountAllButton = QRadioButton(
            _("Automatically mount all Windows drives")
        )
        self.autoMountManualButton = QRadioButton(
            _("Only automatically mount Windows drives that are configured below")
        )
        self.autoMountGroup = QButtonGroup()
        self.autoMountGroup.addButton(self.autoMountAllButton)
        self.autoMountGroup.addButton(self.autoMountManualButton)
        self.setAutoMountWidgetValues()
        self.autoMountCheckBox.stateChanged.connect(self.autoMountChanged)
        self.autoMountGroup.buttonToggled.connect(self.autoMountGroupToggled)

        autoMountLayout = QGridLayout()
        autoMountLayout.addWidget(self.autoMountCheckBox, 0, 0, 1, 2)
        autoMountLayout.addWidget(self.autoMountAllButton, 1, 1, 1, 1)
        autoMountLayout.addWidget(self.autoMountManualButton, 2, 1, 1, 1)
        checkbox_width = self.autoMountCheckBox.style().pixelMetric(
            QStyle.PM_IndicatorWidth
        )
        autoMountLayout.setColumnMinimumWidth(0, checkbox_width)
        autoMountLayout.setVerticalSpacing(8)
        autoMountLayout.setContentsMargins(0, 0, 0, 8)

        self.driveTable = QTableWidget(len(drives), 6, self)
        self.driveTable.setHorizontalHeaderLabels(
            [
                _("User Mounted"),
                _("System Mounted"),
                _("Drive"),
                _("Mount Point"),
                _("Automatic Mount"),
                _("Automatic Unmount at Exit"),
            ]
        )
        self.userMountCol = 0
        self.systemMountCol = 1
        self.mountPointCol = 3
        self.windowsDriveCol = 2
        self.autoMountCol = 4
        self.autoUnmountCol = 5

        self.driveTable.verticalHeader().setVisible(False)
        delegate = CheckBoxDelegate(None)
        for col in (
            self.userMountCol,
            self.systemMountCol,
            self.autoMountCol,
            self.autoUnmountCol,
        ):
            self.driveTable.setItemDelegateForColumn(col, delegate)

        self.driveTable.setSizeAdjustPolicy(QAbstractScrollArea.AdjustToContents)
        row = 0
        for drive in drives:
            self.addDriveAtRow(row, drive)
            row += 1

        self.setDriveAutoMountColStates()
        self.driveTable.resizeColumnsToContents()
        self.driveTable.sortItems(self.mountPointCol)

        self.driveTable.itemChanged.connect(self.driveTableItemChanged)

        self.pendingOpsLabel = QLabel(_("Pending Operations:"))
        sheet = """
        tt {
            font-weight: bold;
            color: gray;
        }
        """
        self.pendingOpsBox = QTextBrowser()
        self.pendingOpsBox.setReadOnly(True)
        document = self.pendingOpsBox.document()  # type: QTextDocument
        document.setDefaultStyleSheet(sheet)

        buttonBox = QDialogButtonBox(
            QDialogButtonBox.Apply | QDialogButtonBox.Close | QDialogButtonBox.Help
        )
        translateDialogBoxButtons(buttonBox)
        buttonBox.rejected.connect(self.reject)
        self.helpButton = buttonBox.button(QDialogButtonBox.Help)  # type: QPushButton
        self.helpButton.clicked.connect(self.helpButtonClicked)
        self.helpButton.setToolTip(_("Get help online..."))
        self.applyButton = buttonBox.button(QDialogButtonBox.Apply)  # type: QPushButton
        self.applyButton.clicked.connect(self.applyButtonClicked)
        self.applyButton.setText(_("&Apply Pending Operations"))

        configWidget = QWidget()
        opsWidget = QWidget()
        splitter = QSplitter()
        splitter.setOrientation(Qt.Vertical)
        splitter.addWidget(configWidget)
        splitter.addWidget(opsWidget)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        configLayout = QVBoxLayout()
        configLayout.addLayout(autoMountLayout)
        configLayout.addWidget(self.driveTable)
        configWidget.setLayout(configLayout)

        opsLayout = QVBoxLayout()
        opsLayout.addWidget(self.pendingOpsLabel)
        opsLayout.addWidget(self.pendingOpsBox)
        opsWidget.setLayout(opsLayout)

        layout = QVBoxLayout()
        margin = configLayout.contentsMargins().left() + 2
        layout.setSpacing(margin)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.addWidget(splitter)
        layout.addWidget(buttonBox)
        self.setLayout(layout)
        self.setApplyButtonState()

    @pyqtSlot()
    def helpButtonClicked(self) -> None:
        webbrowser.open_new_tab("https://damonlynch.net/rapid/documentation/#wslmount")

    @pyqtSlot()
    def applyButtonClicked(self) -> None:
        """ "
        Initiate mount or unmount operations after the user clicked the apply button
        """

        logging.debug("Applying WSL mount ops")
        validate = False
        cancelled = False
        if self.pending_mount_ops:
            drives = list(self.pending_mount_ops.keys())
            try:
                if not do_mount_drives_op(
                    drives=drives,
                    pending_ops=self.pending_mount_ops,
                    parent=self,
                    is_do_mount=True,
                ):
                    logging.debug("Not all drives mounted successfully")
                    validate = True
            except SudoException as e:
                assert e.code == SudoExceptionCode.command_cancelled
                validate = True
                cancelled = True

        if self.pending_unmount_ops and not cancelled:
            drives = list(self.pending_unmount_ops.keys())
            try:
                if not do_mount_drives_op(
                    drives=drives,
                    pending_ops=self.pending_unmount_ops,
                    parent=self,
                    is_do_mount=False,
                ):
                    logging.debug("Not all drives unmounted successfully")
                    validate = True
            except SudoException as e:
                assert e.code == SudoExceptionCode.command_cancelled
                validate = True

        if validate:
            drives = list(self.pending_mount_ops.keys()) + list(
                self.pending_unmount_ops.keys()
            )
            # block signal being emitted when programmatically changing checkbox
            # states
            blocked = self.driveTable.blockSignals(True)
            for drive in drives:
                mounted = wsl_mount_point(drive_letter=drive.drive_letter) != ""
                for row in range(self.driveTable.rowCount()):
                    item = self.driveTable.item(row, self.userMountCol)
                    d = item.data(Qt.UserRole)  # type: WindowsDriveMount
                    if d.drive_letter == drive.drive_letter:
                        item.setCheckState(Qt.Checked if mounted else Qt.Unchecked)
                        break
            # restore signal state
            self.driveTable.blockSignals(blocked)

        self.pending_mount_ops.clear()
        self.pending_unmount_ops.clear()
        self.updatePendingOps()
        self.setApplyButtonState()

    @pyqtSlot(QTableWidgetItem)
    def driveTableItemChanged(self, item: QTableWidgetItem) -> None:
        """
        Respond to the user checking or unchecking a checkbox in the table of drives

        :param item: the table item checked or unchecked
        """

        column = item.column()
        if column == self.userMountCol:
            drive = item.data(Qt.UserRole)  # type: WindowsDriveMount
            do_mount = item.checkState() == Qt.Checked
            tasks = determine_mount_ops(
                do_mount=do_mount,
                drive_letter=drive.drive_letter,
                mount_point=drive.mount_point,
                uid=self.uid,
                gid=self.gid,
                wsl_mount_root=self.wsl_mount_root,
            )
            if tasks:
                if do_mount:
                    self.pending_mount_ops[drive] = tasks
                else:
                    self.pending_unmount_ops[drive] = tasks
            else:
                del self.pending_mount_ops[drive]
            self.updatePendingOps()
            self.setApplyButtonState()
        elif not self.prefs.wsl_automount_all_removable_drives and column in (
            self.autoMountCol,
            self.autoUnmountCol,
        ):
            row = item.row()
            drive = self.driveTable.item(row, self.userMountCol).data(
                Qt.UserRole
            )  # type: WindowsDriveMount
            if column == self.autoUnmountCol:
                auto_mount = (
                    self.driveTable.item(row, self.autoMountCol).checkState()
                    == Qt.Checked
                )
                auto_unmount = item.checkState() == Qt.Checked
            else:
                auto_mount = item.checkState() == Qt.Checked
                auto_unmount = (
                    self.driveTable.item(row, self.autoUnmountCol).checkState()
                    == Qt.Checked
                )
            self.windrive_prefs.set_prefs(drive, auto_mount, auto_unmount)

    def updatePendingOps(self) -> None:
        """
        Update the list of pending operations displayed to the user at the bottom of the
        Windows Drive Mount window
        """
        self.pendingOpsBox.clear()
        lines = []
        for mount_ops in self.pending_mount_ops.values():
            for op in mount_ops:
                lines.append(self.make_mount_op_hr.mount_task_human_readable(op))
        for mount_ops in self.pending_unmount_ops.values():
            for op in mount_ops:
                lines.append(self.make_mount_op_hr.mount_task_human_readable(op))

        text = "<br>".join(lines)
        self.pendingOpsBox.setHtml(text)

    def setApplyButtonState(self) -> None:
        """
        Change the apply button state depending on whether there are any pending
        mount or unmount operations
        """

        enabled = len(self.pending_mount_ops) > 0 or len(self.pending_unmount_ops) > 0
        self.applyButton.setEnabled(enabled)

    @pyqtSlot(int)
    def autoMountChanged(self, state: int) -> None:
        """
        Respond to the user checking or unchecking the automatically mount Windows
        drives option, adjusting the preferences and setting other control states

        :param state: Whether the new state is checked or unchecked
        """

        auto_mount = state == Qt.Checked
        self.prefs.wsl_automount_removable_drives = auto_mount
        self.setAutoMountGroupState()

    @pyqtSlot(QAbstractButton, bool)
    def autoMountGroupToggled(self, button: QAbstractButton, checked: bool) -> None:
        """
        Respond to the user checking or unchecking one of the order auto mount radio
        buttons

        :param button: Radio button modified
        :param checked: Whether the button was checked or unchecked
        """

        self.prefs.wsl_automount_all_removable_drives = (
            self.autoMountAllButton.isChecked()
        )
        self.driveTable.setEnabled(not self.prefs.wsl_automount_all_removable_drives)
        self.setAutoMountGroupState()

    def setAutoMountWidgetValues(self) -> None:
        """
        Set values for Auto mount and other controls based on program preferences
        """
        self.autoMountCheckBox.setChecked(self.prefs.wsl_automount_removable_drives)
        self.setAutoMountGroupState()

    def setAutoMountGroupState(self):
        """
        Set control states of controls depending on program preferences, including
        whether they are enabled or not
        """

        if self.prefs.wsl_automount_removable_drives:
            self.autoMountAllButton.setEnabled(True)
            self.autoMountManualButton.setEnabled(True)
            self.autoMountGroup.setExclusive(True)
            self.autoMountAllButton.setChecked(
                self.prefs.wsl_automount_all_removable_drives
            )
            self.autoMountManualButton.setChecked(
                not self.prefs.wsl_automount_all_removable_drives
            )
            self.setDriveAutoMountColStates()
        else:
            self.autoMountAllButton.setEnabled(False)
            self.autoMountManualButton.setEnabled(False)
            self.autoMountGroup.setExclusive(False)
            self.autoMountAllButton.setChecked(False)
            self.autoMountManualButton.setChecked(False)
            self.setDriveAutoMountColStates()

    def setDriveAutoMountColStates(self) -> None:
        """
        For each Windows drive in the drive table, enable or disable checkboxes and set
        their values
        """

        if self.driveTable is not None:
            # Set table state here rather than in setAutoMountGroupState() because
            # it does not exist early in window init
            self.driveTable.setEnabled(
                not self.prefs.wsl_automount_all_removable_drives
            )

            for row in range(self.driveTable.rowCount()):
                drive = self.driveTable.item(row, self.userMountCol).data(
                    Qt.UserRole
                )  # type: WindowsDriveMount

                if not drive.system_mounted:
                    if not self.prefs.wsl_automount_removable_drives:
                        auto_mount = auto_unmount = False
                    elif self.prefs.wsl_automount_all_removable_drives:
                        auto_mount = auto_unmount = True
                    else:
                        auto_mount, auto_unmount = self.windrive_prefs.drive_prefs(
                            drive=drive
                        )
                    autoMountItem = self.driveTable.item(row, self.autoMountCol)
                    autoUnmountItem = self.driveTable.item(row, self.autoUnmountCol)

                    # block signal being emitted when programmatically changing checkbox
                    # states
                    blocked = self.driveTable.blockSignals(True)
                    for item, value in (
                        (autoMountItem, auto_mount),
                        (autoUnmountItem, auto_unmount),
                    ):
                        item.setCheckState(Qt.Checked if value else Qt.Unchecked)
                        self.setItemState(
                            enabled=self.prefs.wsl_automount_removable_drives,
                            item=item,
                        )
                    # restore signal state
                    self.driveTable.blockSignals(blocked)

    @staticmethod
    def setItemState(enabled: bool, item: QTableWidgetItem) -> None:
        """
        Enable or disable an individual check box in the Windows drive mount table
        :param enabled: Whether the control should be enabled or disabled
        :param item: The item to apply the state to
        """

        if enabled:
            item.setFlags(
                item.flags()
                | Qt.ItemIsEnabled
                | Qt.ItemIsEditable
                | Qt.ItemIsSelectable
            )
        else:
            item.setFlags(
                item.flags()
                & ~Qt.ItemIsEditable
                & ~Qt.ItemIsEnabled
                & ~Qt.ItemIsSelectable
            )

    def addDriveAtRow(self, row: int, drive: WindowsDriveMount):
        """
        Add new windows mount drive to the drive table at the row indicated

        :param row: row to add the drive to
        :param drive: the drive to add
        """

        auto_mount = self.autoMountCheckBox.isChecked()
        auto_mount_all = self.autoMountAllButton.isChecked()

        if drive.mount_point:
            mount_point = drive.mount_point
            is_mounted = True
        else:
            is_mounted = False

        system_mounted = drive.system_mounted
        user_mounted = not system_mounted

        if not is_mounted:
            mount_point = wsl_standard_mount_point(
                self.wsl_mount_root, drive.drive_letter
            )

        # User Mounted Column
        userMountedItem = QTableWidgetItem()
        checked = user_mounted and is_mounted
        userMountedItem.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        if system_mounted:
            self.setItemState(enabled=False, item=userMountedItem)
        # Store the drive data in the first column
        userMountedItem.setData(Qt.UserRole, drive)

        # System Mounted Columns
        systemMountItem = QTableWidgetItem()
        systemMountItem.setCheckState(Qt.Checked if system_mounted else Qt.Unchecked)
        systemMountItem.setFlags(
            systemMountItem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable
        )

        # Mount Point Column
        mountPointItem = QTableWidgetItem(mount_point)
        mountPointItem.setFlags(
            mountPointItem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable
        )

        # Windows Drive Column
        windowsDriveItem = QTableWidgetItem(
            f"{drive.label} ({drive.drive_letter.upper()}:)"
        )
        windowsDriveItem.setFlags(
            windowsDriveItem.flags() & ~Qt.ItemIsEditable & ~Qt.ItemIsSelectable
        )

        # Automount and Auto Unmount at Exit Columns
        automountItem = QTableWidgetItem()
        autounmountItem = QTableWidgetItem()
        if system_mounted:
            automountItem.setCheckState(Qt.Checked)
            autounmountItem.setCheckState(Qt.Unchecked)
            self.setItemState(enabled=False, item=automountItem)
            self.setItemState(enabled=False, item=autounmountItem)
        elif auto_mount:
            if auto_mount_all:
                automountItem.setCheckState(Qt.Checked)
                autounmountItem.setCheckState(Qt.Checked)
        else:
            automountItem.setCheckState(Qt.Unchecked)
            autounmountItem.setCheckState(Qt.Unchecked)
            self.setItemState(enabled=False, item=automountItem)
            self.setItemState(enabled=False, item=autounmountItem)

        self.driveTable.setItem(row, self.userMountCol, userMountedItem)
        self.driveTable.setItem(row, self.systemMountCol, systemMountItem)
        self.driveTable.setItem(row, self.mountPointCol, mountPointItem)
        self.driveTable.setItem(row, self.windowsDriveCol, windowsDriveItem)
        self.driveTable.setItem(row, self.autoMountCol, automountItem)
        self.driveTable.setItem(row, self.autoUnmountCol, autounmountItem)

    def addMount(self, drive: WindowsDriveMount) -> None:
        """
        Add a new Windows drive mount to the table
        :param drive: drive to add
        """

        row = self.driveTable.rowCount()
        self.driveTable.insertRow(row)
        logging.debug(
            "Adding drive %s: to Mount Windows Drive table", drive.drive_letter
        )
        # block signal being emitted when programmatically changing checkbox
        # states
        blocked = self.driveTable.blockSignals(True)
        self.addDriveAtRow(row, drive)
        self.driveTable.sortItems(self.mountPointCol)
        # restore signal state
        self.driveTable.blockSignals(blocked)

    def removeMount(self, drive: WindowsDriveMount) -> None:
        """
        Remove a Windows drive from the table
        :param drive: Drive to remove
        """

        for row in range(self.driveTable.rowCount()):
            d = self.driveTable.item(row, 0).data(Qt.UserRole)
            if d == drive:
                logging.debug(
                    "Removing drive %s: from Mount Windows Drive table",
                    drive.drive_letter,
                )
                self.driveTable.removeRow(row)
                break


class WslDrives:
    """
    Manages Windows drive mounts under the Window Subsystem for Linux
    """

    def __init__(self, rapidApp: "RapidWindow") -> None:
        self.drives = []  # type: List[WindowsDriveMount]
        self.have_unmounted_drive = False
        self.rapidApp = rapidApp
        self.prefs = self.rapidApp.prefs
        self.windrive_prefs = WSLWindowsDrivePrefsInterface(prefs=self.prefs)
        self.mountDrivesDialog = None  # type: Optional[WslMountDriveDialog]
        self.uid = os.getuid()
        self.gid = os.getgid()
        self.wsl_mount_root = Path(self._load_wsl_conf_mnt_location())

    def _load_wsl_conf_mnt_location(self):
        config = configparser.ConfigParser()
        try:
            config.read_file(open("/etc/wsl.conf"))
        except Exception:
            logging.debug("Could not load wsl.conf")
        else:
            if config.has_option("automount", "root"):
                mount_dir = config.get("automount", "root")
                if Path(mount_dir).is_dir():
                    return mount_dir
                else:
                    logging.warning("WSL root mount point %s does not exist", mount_dir)
        return "/mnt"

    def add_drive(self, drive: WindowsDriveMount) -> None:
        """
        Add a new windows drive, which may be already mounted or not

        :param drive: the drive to add
        """

        self.drives.append(drive)
        if not drive.mount_point:
            self.have_unmounted_drive = True
        if self.mountDrivesDialog:
            self.mountDrivesDialog.addMount(drive)

    def remove_drive(self, drive: WindowsDriveMount) -> None:
        """
        Remove a windows drive

        :param drive: the drive to remove
        """

        self.drives.remove(drive)
        if self.mountDrivesDialog:
            self.mountDrivesDialog.removeMount(drive)

    def mount_drives(self) -> None:
        """
        Mount all drives that should be automatically mounted, and prompt the user for
        drives that are not automatically mounted
        """

        if self.have_unmounted_drive:
            unmounted_drives = (drive for drive in self.drives if not drive.mount_point)
            drives_to_mount = []
            show_dialog = False
            for drive in unmounted_drives:
                if self.prefs.wsl_automount_removable_drives:
                    if self.prefs.wsl_automount_all_removable_drives:
                        drives_to_mount.append(drive)
                    else:
                        if self.windrive_prefs.drive_prefs(drive).auto_mount:
                            drives_to_mount.append(drive)
                        else:
                            show_dialog = True

            if drives_to_mount:
                self.do_mount_drives(drives=drives_to_mount)

            # TODO handle opening drive mount dialog window after auto mount
            if show_dialog and self.mountDrivesDialog is None and False:
                self.show_mount_drives_dialog(refresh_drive_state=False)

        # TODO reset self.have_unmounted_drive

    def unmount_drives(self) -> bool:
        """
        Unmount drives that should be automatically unmounted at program exit

        :return: True if the user did not cancel the unmount operation when prompted to
        enter a password
        """

        if self.prefs.wsl_automount_removable_drives:
            auto_unmount_drives = []  # type: List[WindowsDriveMount]
            for drive in self.drives:
                if drive.mount_point and not drive.system_mounted:
                    if (
                        self.prefs.wsl_automount_all_removable_drives
                        or self.windrive_prefs.drive_prefs(drive=drive).auto_unmount
                    ):
                        auto_unmount_drives.append(drive)
            if auto_unmount_drives:
                pending_ops = OrderedDict()
                for drive in auto_unmount_drives:
                    tasks = determine_mount_ops(
                        do_mount=False,
                        drive_letter=drive.drive_letter,
                        mount_point=drive.mount_point,
                        uid=self.uid,
                        gid=self.gid,
                        wsl_mount_root=self.wsl_mount_root,
                    )
                    if tasks:
                        pending_ops[drive] = tasks
                try:
                    do_mount_drives_op(
                        drives=auto_unmount_drives,
                        pending_ops=pending_ops,
                        parent=self.rapidApp,
                        is_do_mount=False,
                    )
                except SudoException as e:
                    assert e.code == SudoExceptionCode.command_cancelled
                    return False
        return True

    def _refresh_drive_state(self) -> None:
        """
        Refresh the internally maintained list of drives and their mount status
        """

        refreshed_drives = []  # type: List[WindowsDriveMount]
        for drive in self.drives:
            mount_point = wsl_mount_point(drive_letter=drive.drive_letter)
            if mount_point != drive.mount_point:
                refreshed_drives.append(drive._replace(mount_point=mount_point))
            else:
                refreshed_drives.append(drive)
        self.drives = refreshed_drives

    def show_mount_drives_dialog(self, refresh_drive_state: bool = True) -> None:
        """
        Show the Dialogue window with a list of Windows drive mounts and associated
        options

        :param refresh_drive_state: if True, fefresh the internally maintained list of
         Windows drives and their states
        :return:
        """
        if refresh_drive_state:
            self._refresh_drive_state()

        if self.mountDrivesDialog is None:
            self.mountDrivesDialog = WslMountDriveDialog(
                parent=self.rapidApp,
                drives=self.drives,
                prefs=self.rapidApp.prefs,
                windrive_prefs=self.windrive_prefs,
                wsl_mount_root=self.wsl_mount_root,
            )
            self.mountDrivesDialog.exec()
            self.mountDrivesDialog = None

    def do_mount_drives(self, drives: List[WindowsDriveMount]) -> None:
        """
        Mount the list of drives that should be automatically mounted

        :param drives: the drives to mount
        """

        logging.debug("Auto mounting %s drives", len(drives))
        pending_ops = OrderedDict()

        for drive in drives:
            tasks = determine_mount_ops(
                do_mount=True,
                drive_letter=drive.drive_letter,
                mount_point="",
                uid=self.uid,
                gid=self.gid,
                wsl_mount_root=self.wsl_mount_root,
            )
            if tasks:
                pending_ops[drive] = tasks

        try:
            do_mount_drives_op(
                drives=drives,
                pending_ops=pending_ops,
                parent=self.rapidApp,
                is_do_mount=True,
            )
        except SudoException as e:
            assert e.code == SudoExceptionCode.command_cancelled
        self._refresh_drive_state()


class WslWindowsRemovableDriveMonitor(QObject):
    """
    Use wmic.exe to periodically probe for removable drives on Windows

    On Windows an actual removable drive, e.g. a USB drive, can be classified
    as a "local drive". Strange but true. Thus need to probe for both local and
    removable drives.
    """

    driveMounted = pyqtSignal("PyQt_PyObject")
    driveUnmounted = pyqtSignal("PyQt_PyObject")

    def __init__(self) -> None:
        super().__init__()
        self.known_drives = set()  # type: Set[WindowsDrive]
        # dict key is drive letter
        self.detected_drives = dict()  # type: Dict[str, WindowsDriveMount]

    @pyqtSlot()
    def startMonitor(self) -> None:
        logging.debug("Starting Wsl Removable Drive Monitor")
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.probeWindowsDrives)
        self.timer.setTimerType(Qt.CoarseTimer)
        self.timer.setInterval(1500)
        self.probeWindowsDrives()
        self.timer.start()

    @pyqtSlot()
    def stopMonitor(self) -> None:
        logging.debug("Stopping Wsl Removable Drive Monitor")
        self.timer.stop()

    @pyqtSlot()
    def probeWindowsDrives(self) -> None:
        timer_active = self.timer.isActive()
        if timer_active:
            self.timer.stop()
        current_drives = wsl_windows_drives(
            (WindowsDriveType.removable_disk, WindowsDriveType.local_disk)
        )
        new_drives = current_drives - self.known_drives
        removed_drives = self.known_drives - current_drives

        drives = []

        for drive in new_drives:
            if wsl_drive_valid(drive.drive_letter):
                mount_point = wsl_mount_point(drive.drive_letter)
                if mount_point:
                    assert os.path.ismount(mount_point)
                label = drive.label or (
                    _("Removable Drive")
                    if drive.drive_type == WindowsDriveType.removable_disk
                    else _("Local Drive")
                )
                windows_drive_mount = WindowsDriveMount(
                    drive_letter=drive.drive_letter,
                    label=label,
                    mount_point=mount_point,
                    drive_type=drive.drive_type,
                    system_mounted=drive.drive_type == WindowsDriveType.local_disk
                    and mount_point != "",
                )
                drives.append(windows_drive_mount)
                self.detected_drives[drive.drive_letter] = windows_drive_mount

        if drives:
            self.driveMounted.emit(drives)

        for drive in removed_drives:
            windows_drive_mount = self.detected_drives[drive.drive_letter]
            self.driveUnmounted.emit(windows_drive_mount)
            del self.detected_drives[drive.drive_letter]

        self.known_drives = current_drives
        if timer_active:
            self.timer.start()


def wsl_standard_mount_point(root: Path, drive_letter: str) -> str:
    """
    Return mount point for the driver letter
    :param root: WSL mount point root
    :param drive_letter: drive's driver letter
    :return: the standard mount point
    """

    return str(root / drive_letter.lower())


def wsl_mount_point(drive_letter: str) -> str:
    """
    Determine the existing mount point of a Windows drive

    :param drive_letter: windows drive letter
    :return: Linux mount point, or "" if it is not mounted
    """

    with open("/proc/mounts") as m:
        mounts = m.read()

    regex = fr"^drvfs (.+?) 9p .+?path={drive_letter}:\\?;"
    mnt = re.search(regex, mounts, re.MULTILINE | re.IGNORECASE)
    if mnt is not None:
        return mnt.group(1)
    else:
        return ""


def wsl_drive_valid(drive_letter: str) -> bool:
    """
    Use the Windows command 'vol' to determine if the drive letter indicates a valid
    drive

    :param drive_letter: drive letter to check in Windows
    :return: True if valid, False otherwise
    """

    try:
        subprocess.check_call(
            shlex.split(f"cmd.exe /c vol {drive_letter}:"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def wsl_windows_drives(
    drive_type_filter: Optional[Tuple[WindowsDriveType, ...]] = None,
) -> Set[WindowsDrive]:
    """
    Get Windows to report its drives and their types
    :param drive_type_filter: the type of drives to search for
    """

    # wmic is deprecated, but is much, much faster than calling powershell
    output = subprocess.run(
        shlex.split("wmic.exe logicaldisk get deviceid, volumename, drivetype"),
        universal_newlines=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()
    # Discard first line of output, which is a table header
    drives = set()
    for line in output.split("\n")[1:]:
        if line:  # expect blank lines
            components = line.split(maxsplit=2)

            drive_type = int(components[1])
            # 0 - Unknown
            # 1 - No Root Directory
            # 2 - Removable Disk
            # 3 - Local Disk
            # 4 - Network Drive
            # 5 - Compact Disk
            # 6 - RAM Disk

            if 2 <= drive_type <= 4:
                drive_type = WindowsDriveType(drive_type)
                if drive_type_filter is None or drive_type in drive_type_filter:
                    drive_letter = components[0][0]
                    if len(components) == 3:
                        label = components[2].strip()
                    else:
                        label = ""
                    drives.add(
                        WindowsDrive(
                            drive_letter=drive_letter,
                            label=label,
                            drive_type=drive_type,
                        )
                    )
    return drives


if __name__ == "__main__":
    # Application development test code:

    from PyQt5.QtWidgets import QApplication

    from raphodo.preferences import Preferences

    app = QApplication([])

    app.setOrganizationName("Rapid Photo Downloader")
    app.setOrganizationDomain("damonlynch.net")
    app.setApplicationName("Rapid Photo Downloader")

    prefs = Preferences()
    wdrive_prefs = WSLWindowsDrivePrefsInterface(prefs)

    all_drives = True
    if not all_drives:
        windows_drives = wsl_windows_drives(
            drive_type_filter=(
                WindowsDriveType.removable_disk,
                WindowsDriveType.local_disk,
            )
        )
    else:
        windows_drives = wsl_windows_drives()
    ddrives = []

    for wdrive in windows_drives:
        if wsl_drive_valid(wdrive.drive_letter):
            main_mount_point = wsl_mount_point(wdrive.drive_letter)
            if main_mount_point:
                assert os.path.ismount(main_mount_point)
                print(f"{wdrive.drive_letter}: is mounted at {main_mount_point}")
            else:
                print(f"{wdrive.drive_letter}: is not mounted")
            ddrives.append(
                WindowsDriveMount(
                    drive_letter=wdrive.drive_letter,
                    label=wdrive.label or _("Removable Drive"),
                    mount_point=main_mount_point,
                    drive_type=wdrive.drive_type,
                    system_mounted=wdrive.drive_type == WindowsDriveType.local_disk
                    and main_mount_point != "",
                )
            )

    w = WslMountDriveDialog(
        drives=ddrives, prefs=prefs, windrive_prefs=wdrive_prefs, wsl_mount_root="/mnt"
    )
    w.exec()
