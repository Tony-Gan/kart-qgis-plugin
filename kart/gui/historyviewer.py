import json
import os

from kart.kartapi import executeskart
from kart.gui import icons
from kart.gui.diffviewer import DiffViewerDialog
from kart.utils import setting, DIFFSTYLES

from qgis.core import Qgis, QgsProject, QgsVectorLayer, QgsWkbTypes
from qgis.utils import iface
from qgis.gui import QgsMessageBar

from qgis.PyQt import uic
from qgis.PyQt.QtCore import (
    Qt,
    QPoint,
    QRectF,
    QDateTime,
)
from qgis.PyQt.QtGui import (
    QPixmap,
    QPainter,
    QColor,
    QPainterPath,
    QPen,
    QPalette,
    QBrush,
)

from qgis.PyQt.QtWidgets import (
    QTreeWidget,
    QAbstractItemView,
    QAction,
    QMenu,
    QTreeWidgetItem,
    QWidget,
    QVBoxLayout,
    QSizePolicy,
    QLabel,
    QInputDialog,
    QHeaderView,
    QFileDialog,
)

COMMIT_GRAPH_HEIGHT = 20
RADIUS = 4
COL_SPACING = 20
PEN_WIDTH = 2
MARGIN = 50

COLORS = [
    QColor(Qt.red),
    QColor(Qt.green),
    QColor(Qt.blue),
    QColor(Qt.black),
    QColor(255, 166, 0),
    QColor(Qt.darkGreen),
    QColor(Qt.darkBlue),
    QColor(Qt.cyan),
    QColor(Qt.magenta),
]


class HistoryTree(QTreeWidget):
    def __init__(self, repo, dataset, parent):
        super(HistoryTree, self).__init__()
        self.repo = repo
        self.dataset = dataset
        self.parent = parent
        self.filterText = ""
        self.startDate = QDateTime.fromSecsSinceEpoch(0).date()
        self.endDate = QDateTime.currentDateTime().date()
        self.initGui()

    def initGui(self):
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        # self.header().setStretchLastSection(True)
        self.setHeaderLabels(
            ["Graph", "Refs", "Description", "Author", "Date", "CommitID"]
        )
        self.customContextMenuRequested.connect(self._showPopupMenu)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.populate()

    def _showPopupMenu(self, point):
        def _f(f, *args):
            def wrapper():
                f(*args)

            return wrapper

        point = self.mapToGlobal(point)
        selected = self.selectedItems()
        if any([isinstance(item, ShallowCloneWarningItem) for item in selected]):
            return
        if selected and len(selected) == 1:
            item = self.currentItem()
            actions = {}
            parents = item.commit["parents"]
            if len(parents) == 1:
                actions["Show changes introduced by this commit..."] = (
                    _f(
                        self.showChangesBetweenCommits,
                        item.commit["commit"],
                        parents[0],
                    ),
                    icons.diffIcon,
                )
                actions["Save changes as patch..."] = (
                    _f(
                        self.savePatch,
                        item.commit["commit"],
                    ),
                    icons.patchIcon,
                )
                actions["Add changes to current QGIS project as vector layer"] = (
                    _f(self.saveAsLayer, item.commit["commit"], parents[0]),
                    icons.addtoQgisIcon,
                )
            elif len(parents) > 1:
                for parent in parents:
                    actions[
                        f"Show diff between this commit and parent {parent[:7]}..."
                    ] = (
                        _f(
                            self.showChangesBetweenCommits,
                            item.commit["commit"],
                            parent,
                        ),
                        icons.diffIcon,
                    )
            actions.update(
                {
                    "Reset current branch to this commit": (
                        _f(self.resetBranch, item),
                        icons.resetIcon,
                    ),
                    "Create branch at this commit...": (
                        _f(self.createBranch, item),
                        icons.createBranchIcon,
                    ),
                    "Create tag at this commit...": (
                        _f(self.createTag, item),
                        icons.createTagIcon,
                    ),
                    "Restore working tree datasets to this version...": (
                        _f(self.restoreDatasets, item),
                        icons.restoreIcon,
                    ),
                }
            )

            for ref in item.commit["refs"]:
                if "HEAD" in ref:
                    continue
                elif "tag:" in ref:
                    tag = ref[4:].strip()
                    actions[f"Delete tag '{tag}'"] = (
                        _f(self.deleteTag, tag),
                        icons.deleteIcon,
                    )
                else:
                    actions[f"Switch to branch '{ref}'"] = (
                        _f(self.switchBranch, ref),
                        icons.checkoutIcon,
                    )
                    actions[f"Delete branch '{ref}'"] = (
                        _f(self.deleteBranch, ref),
                        icons.deleteIcon,
                    )
        elif selected and len(selected) == 2:
            itema = selected[0]
            itemb = selected[1]
            actions = {
                "Show changes between these commits...": (
                    _f(
                        self.showChangesBetweenCommits,
                        itema.commit["commit"],
                        itemb.commit["commit"],
                    ),
                    icons.diffIcon,
                )
            }
        else:
            actions = []
        self.menu = QMenu(self)
        for text in actions:
            func, icon = actions[text]
            action = QAction(icon, text, self.menu)
            action.triggered.connect(func)
            self.menu.addAction(action)
        if actions:
            self.menu.popup(point)

    @executeskart
    def createTag(self, item):
        name, ok = QInputDialog.getText(
            self, "Create tag", "Enter name of tag to create"
        )
        if ok and name:
            self.repo.createTag(name, item.commit["commit"])
            self.message("Tag correctly created", Qgis.Info)
            self.populate()

    @executeskart
    def deleteTag(self, tag):
        self.repo.deleteTag(tag)
        self.message(f"Correctly deleted tag '{tag}'", Qgis.Info)
        self.populate()

    @executeskart
    def switchBranch(self, branch):
        self.repo.checkoutBranch(branch)
        self.message(f"Correctly switched to branch '{branch}'", Qgis.Info)
        self.populate()

    @executeskart
    def deleteBranch(self, branch):
        self.repo.deleteBranch(branch)
        self.message(f"Correctly deleted branch '{branch}'", Qgis.Info)
        self.populate()

    @executeskart
    def createBranch(self, item):
        name, ok = QInputDialog.getText(
            self, "Create branch", "Enter name of branch to create"
        )
        if ok and name:
            self.repo.createBranch(name, item.commit["commit"])
            self.message("Branch correctly created", Qgis.Info)
            self.populate()

    @executeskart
    def showDiff(self, item, parent):
        refa = item.commit["commit"]
        hasSchemaChanges = self.repo.diffHasSchemaChanges(refa, parent)
        if hasSchemaChanges:
            self.message(
                "There are schema changes in the selected commit and changes cannot be shown",
                Qgis.Warning,
            )
            return
        diff = self.repo.diff(refa, parent)
        dialog = DiffViewerDialog(self, diff, self.repo)
        dialog.exec()

    @executeskart
    def showChangesBetweenCommits(self, refa, refb):
        hasSchemaChanges = self.repo.diffHasSchemaChanges(refa, refb)
        if hasSchemaChanges:
            self.message(
                "There are schema changes between the selected commits "
                "and changes cannot be shown",
                Qgis.Warning,
            )
            return
        diff = self.repo.diff(refa, refb)
        dialog = DiffViewerDialog(self, diff, self.repo)
        dialog.exec()

    @executeskart
    def savePatch(self, ref):
        filename, _ = QFileDialog.getSaveFileName(
            iface.mainWindow(),
            "Patch file",
            "",
            "Patch files (*.patch);;All files (*.*)",
        )
        self.repo.createPatch(ref, filename)

    @executeskart
    def saveAsLayer(self, refa, refb):
        hasSchemaChanges = self.repo.diffHasSchemaChanges(refb, refa)
        if hasSchemaChanges:
            self.message(
                "There are schema changes between the selected commits "
                "and changes cannot be saved as a layer",
                Qgis.Warning,
            )
            return
        diff = self.repo.diff(refb, refa)
        for dataset in diff:
            geojson = {"type": "FeatureCollection", "features": diff[dataset]}
            layer = QgsVectorLayer(
                json.dumps(geojson), f"{dataset}_diff_{refa[:7]}", "ogr"
            )
            styleName = setting(DIFFSTYLES) or "standard"
            typeString = QgsWkbTypes.geometryDisplayString(layer.geometryType()).lower()
            styleFolder = os.path.join(
                os.path.dirname(os.path.dirname(__file__)),
                "resources",
                "diff_styles",
                styleName,
            )
            stylePath = os.path.join(styleFolder, f"{typeString}.qml")
            layer.loadNamedStyle(stylePath)
            QgsProject.instance().addMapLayer(layer)

    @executeskart
    def resetBranch(self, item):
        self.repo.reset(item.commit["commit"])
        self.message("Branch correctly reset to selected commit", Qgis.Info)
        self.populate()

    @executeskart
    def restoreDatasets(self, item):
        ALL_DATASETS = "Restore all datasets"
        vectorLayers, tables = self.repo.datasets()
        datasets = [ALL_DATASETS]
        datasets.extend(vectorLayers)
        datasets.extend(tables)
        dataset, ok = QInputDialog.getItem(
            iface.mainWindow(),
            "Restore",
            "Select dataset to restore:",
            datasets,
            editable=False,
        )
        if ok:
            if dataset == ALL_DATASETS:
                dataset = None
            self.repo.restore(item.commit["commit"], dataset)
            self.message(
                "Selected dataset(s) correctly restored in working copy", Qgis.Info
            )

    def message(self, text, level):
        self.parent.bar.pushMessage(text, level, duration=5)

    @executeskart
    def populate(self):
        commits = self.repo.log(dataset=self.dataset)

        self.log = {c["commit"]: c for c in commits}
        self.clear()

        maxcol = 0
        for c in commits:
            graph = c["graph"]
            for i in range(3):
                for positions in graph[i].values():
                    if positions:
                        maxcol = max(maxcol, max(positions))

        # maxcol = max([c["commitColumn"] for c in commits])
        width = COL_SPACING * maxcol + 2 * RADIUS

        grafted = False
        for i, commit in enumerate(commits):
            item = CommitTreeItem(commit, self)
            self.addTopLevelItem(item)
            img = self.graphImage(commit, width)
            w = GraphWidget(img)
            w.setFixedHeight(COMMIT_GRAPH_HEIGHT)
            self.setItemWidget(item, 0, w)
            if "grafted" in commit["refs"]:
                grafted = True
        if grafted:
            item = ShallowCloneWarningItem(self)
            self.addTopLevelItem(item)
        for i in range(1, 6):
            self.resizeColumnToContents(i)
        self.setColumnWidth(0, width + MARGIN)
        self.header().setSectionResizeMode(0, QHeaderView.Fixed)
        self.header().setSectionResizeMode(1, QHeaderView.Fixed)
        self.filterCommits()

    def graphImage(self, commit, width):
        image = QPixmap(width, COMMIT_GRAPH_HEIGHT).toImage()
        qp = QPainter(image)
        palette = QWidget().palette()
        qp.fillRect(
            QRectF(0, 0, width, COMMIT_GRAPH_HEIGHT), palette.color(QPalette.Base)
        )

        path = QPainterPath()
        for col in commit["graph"][0][r"\|"]:
            x = RADIUS + COL_SPACING * col
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x, 0)
        for col in commit["graph"][2][r"\|"]:
            x = RADIUS + COL_SPACING * col
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x, COMMIT_GRAPH_HEIGHT)
        for col in commit["graph"][0][r"/"]:
            x = RADIUS + COL_SPACING * col
            x2 = RADIUS + COL_SPACING * (col + 0.5)
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x2, 0)
        for col in commit["graph"][2][r"/"]:
            x = RADIUS + COL_SPACING * (col + 1)
            x2 = RADIUS + COL_SPACING * (col + 0.5)
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x2, COMMIT_GRAPH_HEIGHT)
        for col in commit["graph"][0][r"\\"]:
            x = RADIUS + COL_SPACING * (col + 1)
            x2 = RADIUS + COL_SPACING * (col + 0.5)
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x2, 0)
        for col in commit["graph"][2][r"\\"]:
            x = RADIUS + COL_SPACING * (col)
            x2 = RADIUS + COL_SPACING * (col + 0.5)
            path.moveTo(x, COMMIT_GRAPH_HEIGHT / 2)
            path.lineTo(x2, COMMIT_GRAPH_HEIGHT)
        pen = QPen()
        pen.setWidth(PEN_WIDTH)
        pen.setBrush(palette.color(QPalette.WindowText))
        qp.setPen(pen)
        qp.drawPath(path)

        col = commit["commitColumn"]
        y = int(COMMIT_GRAPH_HEIGHT / 2)
        x = int(RADIUS + COL_SPACING * col)
        color = COLORS[col]
        qp.setPen(color)
        qp.setBrush(color)
        qp.drawEllipse(QPoint(x, y), RADIUS, RADIUS)
        qp.end()

        return image

    def filterCommits(self, text=None, startDate=None, endDate=None):
        self.filterText = text or self.filterText
        self.startDate = startDate or self.startDate
        self.endDate = endDate or self.endDate
        self.filterText = self.filterText.strip(" ").lower()
        root = self.invisibleRootItem()
        for i in range(root.childCount()):
            item = root.child(i)
            if isinstance(item, CommitTreeItem):
                values = [
                    item.commit["message"],
                    item.commit["authorName"],
                    item.commit["commit"],
                ]
                hide = bool(self.filterText) and not any(
                    self.filterText in t.lower() for t in values
                )
                date = QDateTime.fromString(
                    item.commit["authorTime"], Qt.ISODate
                ).date()
                withinDates = date >= self.startDate and date <= self.endDate
                hide = hide or not withinDates
                item.setHidden(hide)


class GraphWidget(QWidget):
    def __init__(self, img):
        QWidget.__init__(self)
        self.setFixedWidth(img.width())
        self.img = img

    def paintEvent(self, e):
        painter = QPainter(self)
        # painter.begin(self);
        painter.drawImage(0, 0, self.img)
        painter.end()


class CommitTreeItem(QTreeWidgetItem):
    def __init__(self, commit, parent):
        QTreeWidgetItem.__init__(self, parent)
        self.commit = commit
        if commit["refs"]:
            labelslist = []
            for label in commit["refs"]:
                if label == "grafted":
                    continue
                if "HEAD ->" in label:
                    labelslist.append(
                        '<span style="background-color:crimson; color:white"> '
                        f'&nbsp;&nbsp;{label.split("->")[-1].strip()}&nbsp;&nbsp;</span>'
                    )
                elif "tag:" in label:
                    labelslist.append(
                        '<span style="background-color:yellow; color:black"> '
                        f"&nbsp;&nbsp;{label[4:].strip()}&nbsp;&nbsp;</span>"
                    )
                else:
                    labelslist.append(
                        '<span style="background-color:salmon; color:white"> '
                        f"&nbsp;&nbsp;{label}&nbsp;&nbsp;</span>"
                    )
            if labelslist:
                labels = " ".join(labelslist) + "&nbsp;&nbsp;"
            else:
                labels = ""
        else:
            labels = ""
        qlabel = QLabel(labels)
        qlabel.setStyleSheet("QLabel {padding-left: 15px;}")
        parent.setItemWidget(self, 1, qlabel)
        self.setText(2, commit["message"].splitlines()[0])
        self.setText(3, commit["authorName"])
        self.setText(4, commit["authorTime"])
        self.setText(5, commit["abbrevCommit"])


WIDGET, BASE = uic.loadUiType(
    os.path.join(os.path.dirname(__file__), "historyviewer.ui")
)


class ShallowCloneWarningItem(QTreeWidgetItem):
    def __init__(self, parent):
        QTreeWidgetItem.__init__(self, parent)
        message = (
            "This repository is a shallow clone, "
            "history past this point is not available locally"
        )
        self.setText(2, message)
        self.setForeground(2, QBrush(QColor(100, 100, 100)))


class HistoryDialog(WIDGET, BASE):
    def __init__(self, repo, dataset=None):
        super(HistoryDialog, self).__init__(iface.mainWindow())
        self.setupUi(self)
        self.setWindowFlags(Qt.Window)

        self.dateEditStart.setDateTime(QDateTime.fromSecsSinceEpoch(0))
        self.dateEditEnd.setDateTime(QDateTime.currentDateTime())

        layout = QVBoxLayout()
        layout.setMargin(0)
        self.bar = QgsMessageBar()
        self.bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        layout.addWidget(self.bar)
        self.history = HistoryTree(repo, dataset, self)
        layout.addWidget(self.history)
        self.frameHistory.setLayout(layout)
        self.history.currentItemChanged.connect(self.commitSelected)
        self.txtFilter.textChanged.connect(self._filterCommmits)
        self.dateEditStart.valueChanged.connect(self._filterCommmits)
        self.dateEditEnd.valueChanged.connect(self._filterCommmits)
        self.resize(1024, 768)

    def commitSelected(self, new, old):
        if new is not None and isinstance(new, CommitTreeItem):
            commit = new.commit
            html = (
                f"<b>SHA-1:</b> {commit['commit']} <br>"
                f"<b>Message:</b> {commit['message']} <br>"
                f"<b>Parents:</b> {', '.join(commit['parents'])} <br>"
            )
        else:
            html = ""
        self.commitDetails.setHtml(html)

    def _filterCommmits(self, value):
        startDate = self.dateEditStart.date()
        endDate = self.dateEditEnd.date()
        self.history.filterCommits(self.txtFilter.text(), startDate, endDate)
