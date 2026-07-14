from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import mimetypes
import os
import re
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
from PySide6.QtCore import QPointF, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QPainter,
    QRadialGradient,
    QTextDocument,
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clickup_client import ClickUpClient, ClickUpError


load_dotenv(Path(__file__).with_name(".env"))

LIST_ID_PATTERNS = [
    re.compile(r"/li(?:st)?/(\d+)", re.IGNORECASE),
    re.compile(r"/list/(\d+)", re.IGNORECASE),
    re.compile(r"/v/l/([^/?#]+)", re.IGNORECASE),
    re.compile(r"[?&]list(?:_id)?=(\d+)", re.IGNORECASE),
]
TASK_QUERY_KEYS = (
    "task_id",
    "taskId",
    "task",
    "id",
    "tid",
    "ct",
    "selected_task",
    "selectedTask",
    "current_task",
    "currentTask",
)
WORKSPACE_QUERY_KEYS = ("team_id", "teamId", "team", "workspace_id", "workspaceId")
TASK_PATH_MARKERS = ("t", "task", "tasks")
NON_TASK_CLICKUP_PATH_PARTS = {"v", "l", "li", "b", "board", "list", "s", "space", "f", "folder"}

APP_STYLE = """
* {
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    letter-spacing: 0;
}

QMainWindow,
QWidget#root {
    background: #0a1408;
    color: #e5e2e1;
}

QFrame#formPanel {
    background: rgba(32, 31, 31, 205);
    border: 1px solid #353534;
    border-radius: 16px;
}

QFrame#inputFrame,
QFrame#editorFrame {
    background: #201f1f;
    border: 1px solid #353534;
    border-radius: 8px;
}

QFrame#inputFrame[focused="true"],
QFrame#editorFrame[focused="true"] {
    border: 1px solid #97d787;
}

QFrame#statusPill {
    background: rgba(20, 79, 16, 70);
    border: 1px solid rgba(151, 215, 135, 90);
    border-radius: 12px;
}

QFrame#toast {
    background: rgba(20, 79, 16, 150);
    border: 1px solid rgba(151, 215, 135, 110);
    border-radius: 12px;
}

QFrame#iconBox {
    background: rgba(151, 215, 135, 35);
    border: 1px solid rgba(151, 215, 135, 80);
    border-radius: 8px;
}

QFrame#divider {
    background: rgba(65, 73, 61, 70);
    min-height: 1px;
    max-height: 1px;
}

QFrame#tabDivider {
    background: #353534;
    min-height: 1px;
    max-height: 1px;
}

QLabel {
    color: #e5e2e1;
}

QLabel#muted,
QLabel#fieldLabel,
QLabel#smallText {
    color: #c1c9ba;
}

QLabel#title {
    color: #e5e2e1;
    font-size: 32px;
    font-weight: 700;
}

QLabel#subtitle {
    color: #c1c9ba;
    font-size: 18px;
}

QLabel#accent {
    color: #97d787;
    font-weight: 700;
}

QLabel#iconText {
    color: #c1c9ba;
    font-size: 20px;
    font-weight: 700;
}

QLineEdit {
    background: transparent;
    color: #e5e2e1;
    border: none;
    padding: 0;
    min-height: 24px;
    selection-background-color: #316b29;
}

QLineEdit::placeholder {
    color: #41493d;
}

QLineEdit:focus {
    border: none;
}

QTextEdit {
    background: transparent;
    color: #e5e2e1;
    border: none;
    border-radius: 0;
    padding: 0;
    line-height: 1.4;
    selection-background-color: #316b29;
}

QPushButton {
    background: #201f1f;
    color: #e5e2e1;
    border: 1px solid rgba(65, 73, 61, 96);
    border-radius: 8px;
    padding: 12px 28px;
    font-weight: 700;
}

QPushButton:hover {
    background: #2a2a2a;
}

QPushButton:pressed {
    background: #1c1b1b;
}

QPushButton:disabled {
    color: #8b9385;
    background: #1c1b1b;
    border-color: rgba(65, 73, 61, 70);
}

QPushButton#primaryButton {
    background: #144f10;
    border: 1px solid rgba(151, 215, 135, 100);
    color: #ffffff;
    border-radius: 8px;
    padding: 12px 30px;
    font-size: 16px;
}

QPushButton#primaryButton:hover {
    background: #175c12;
}

QPushButton#ghostButton {
    background: transparent;
    border: none;
    color: #c1c9ba;
    padding: 8px 14px;
}

QPushButton#ghostButton:hover {
    background: #2a2a2a;
    color: #97d787;
}

QPushButton#tabButton {
    background: transparent;
    border: none;
    border-radius: 0;
    color: #c1c9ba;
    padding: 0 0 14px 0;
    font-size: 14px;
    font-weight: 500;
}

QPushButton#tabButton:hover {
    color: #e5e2e1;
    background: transparent;
}

QPushButton#tabButton[active="true"] {
    color: #97d787;
    border-bottom: 2px solid #97d787;
    font-weight: 700;
}

QScrollBar:vertical {
    background: #1c1b1b;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #41493d;
    min-height: 32px;
    border-radius: 6px;
}

QScrollBar::handle:vertical:hover {
    background: #8b9385;
}

QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical {
    height: 0;
}

QMessageBox {
    background: #201f1f;
    color: #e5e2e1;
}
"""


@dataclass(frozen=True)
class TaskReference:
    task_id: str
    workspace_id: str = ""


class AtmosphericRoot(QWidget):
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0a1408"))

        width = max(self.width(), 1)
        height = max(self.height(), 1)

        first_glow = QRadialGradient(QPointF(width * 0.2, height * 0.3), width * 0.55)
        first_glow.setColorAt(0.0, QColor(20, 79, 16, 58))
        first_glow.setColorAt(1.0, QColor(20, 79, 16, 0))
        painter.fillRect(self.rect(), first_glow)

        second_glow = QRadialGradient(QPointF(width * 0.8, height * 0.7), width * 0.45)
        second_glow.setColorAt(0.0, QColor(20, 79, 16, 42))
        second_glow.setColorAt(1.0, QColor(20, 79, 16, 0))
        painter.fillRect(self.rect(), second_glow)

        corner_glow = QRadialGradient(QPointF(-80, -80), 360)
        corner_glow.setColorAt(0.0, QColor(151, 215, 135, 28))
        corner_glow.setColorAt(1.0, QColor(151, 215, 135, 0))
        painter.fillRect(self.rect(), corner_glow)

        painter.setPen(QColor(151, 215, 135, 18))
        for index in range(260):
            digest = hashlib.blake2b(str(index).encode(), digest_size=4).digest()
            x = int.from_bytes(digest[:2], "big") % width
            y = int.from_bytes(digest[2:], "big") % height
            painter.drawPoint(x, y)

        super().paintEvent(event)


class FocusLineEdit(QLineEdit):
    def __init__(self) -> None:
        super().__init__()
        self.focus_frame: QFrame | None = None

    def set_focus_frame(self, frame: QFrame) -> None:
        self.focus_frame = frame

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_focus_state(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_focus_state(False)
        super().focusOutEvent(event)

    def _set_focus_state(self, focused: bool) -> None:
        if self.focus_frame:
            self.focus_frame.setProperty("focused", focused)
            self.focus_frame.style().unpolish(self.focus_frame)
            self.focus_frame.style().polish(self.focus_frame)


class DescriptionEditor(QTextEdit):
    def __init__(self, paste_dir: Path) -> None:
        super().__init__()
        self.paste_dir = paste_dir
        self.paste_dir.mkdir(parents=True, exist_ok=True)
        self.focus_frame: QFrame | None = None

    def set_focus_frame(self, frame: QFrame) -> None:
        self.focus_frame = frame

    def focusInEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_focus_state(True)
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_focus_state(False)
        super().focusOutEvent(event)

    def _set_focus_state(self, focused: bool) -> None:
        if self.focus_frame:
            self.focus_frame.setProperty("focused", focused)
            self.focus_frame.style().unpolish(self.focus_frame)
            self.focus_frame.style().polish(self.focus_frame)

    def insertFromMimeData(self, source) -> None:  # noqa: N802 - Qt override name
        if source.hasImage():
            image = self._coerce_image(source.imageData())
            if not image.isNull():
                path = self.paste_dir / f"pasted-screenshot-{uuid.uuid4().hex}.png"
                if image.save(str(path), "PNG"):
                    self._insert_image_preview(path, image)
                    return

        image_paths = [
            Path(url.toLocalFile())
            for url in source.urls()
            if url.isLocalFile() and _is_image_file(Path(url.toLocalFile()))
        ]
        if image_paths:
            for path in image_paths:
                image = QImage(str(path))
                if not image.isNull():
                    self._insert_image_preview(path, image)
            return

        super().insertFromMimeData(source)

    def image_paths(self) -> list[Path]:
        paths: list[Path] = []
        block = self.document().begin()

        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    image_format = fragment.charFormat().toImageFormat()
                    path = _path_from_image_name(image_format.name())
                    if path:
                        paths.append(path)
                iterator += 1
            block = block.next()

        return paths

    def _insert_image_preview(self, path: Path, image: QImage) -> None:
        image_url = QUrl.fromLocalFile(str(path))
        self.document().addResource(
            QTextDocument.ResourceType.ImageResource,
            image_url,
            image,
        )

        image_format = QTextImageFormat()
        image_format.setName(image_url.toString())
        if image.width() > 720:
            image_format.setWidth(720)

        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertImage(image_format)
        cursor.insertBlock()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _coerce_image(self, value) -> QImage:
        if isinstance(value, QImage):
            return value
        if hasattr(value, "toImage"):
            return value.toImage()
        return QImage()


class CreateTaskWorker(QObject):
    status_changed = Signal(str)
    finished = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        token: str,
        target_id: str,
        title: str,
        description: str,
        *,
        mode: str,
        target_workspace_id: str = "",
    ) -> None:
        super().__init__()
        self.token = token
        self.target_id = target_id
        self.title = title
        self.description = description
        self.mode = mode
        self.target_workspace_id = target_workspace_id

    def run(self) -> None:
        try:
            client = ClickUpClient(lambda: self.token)
            self.status_changed.emit("Creating subtask..." if self.mode == "subtask" else "Creating task...")

            if self.mode == "subtask":
                parent_task = resolve_parent_task(
                    client,
                    self.target_id,
                    workspace_id=self.target_workspace_id,
                )
                list_id = extract_task_list_id(parent_task)
                parent_task_id = str(parent_task.get("id") or self.target_id)
                payload = {
                    "name": self.title,
                    "markdown_content": self.description,
                    "tags": ["bug"],
                    "parent": parent_task_id,
                }
            else:
                list_id = resolve_board_list_id(client, self.target_id)
                payload = {
                    "name": self.title,
                    "markdown_content": self.description,
                    "tags": ["bug"],
                }

            task = client.create_task(
                list_id,
                payload,
            )
            self.finished.emit(task.get("url", ""))
        except (ClickUpError, KeyError, ValueError) as error:
            self.failed.emit(str(error))


class BugReporterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ClickUp Bug Reporter")
        self.setMinimumSize(1024, 760)
        self.resize(1280, 900)

        self.token = os.getenv("CLICKUP_API_TOKEN", "").strip()
        self.attachments: list[Path] = []
        self.status_message = "Ready"
        self.mode = "subtask"
        self.worker_thread: QThread | None = None
        self.worker: CreateTaskWorker | None = None
        self.toast: QFrame | None = None

        self.setCentralWidget(self._build_ui())
        self._set_status("Ready" if self.token else "CLICKUP_API_TOKEN missing")

    def _build_ui(self) -> QWidget:
        root = AtmosphericRoot()
        root.setObjectName("root")

        layout = QVBoxLayout(root)
        layout.setContentsMargins(40, 32, 40, 24)
        layout.setSpacing(0)

        actions = QHBoxLayout()
        actions.addStretch(1)
        history_button = QPushButton("View History")
        history_button.setObjectName("ghostButton")
        actions.addWidget(history_button)
        layout.addLayout(actions)

        content = QWidget()
        content.setFixedWidth(680)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(56)

        content_layout.addLayout(self._build_header())
        content_layout.addWidget(self._build_task_panel())

        layout.addStretch(2)
        layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)
        layout.addWidget(self._build_footer(), 0, Qt.AlignmentFlag.AlignHCenter)

        self.toast = self._build_toast(root)
        self.toast.hide()

        return root

    def _build_header(self) -> QVBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(16)

        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.addStretch(1)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        self.page_title = QLabel("Report as Subtask")
        self.page_title.setObjectName("title")
        self.page_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_block.addWidget(self.page_title)

        title_row.addLayout(title_block, 1)
        title_row.addStretch(1)
        header.addLayout(title_row)

        self.page_subtitle = QLabel("Submit high-priority technical issues to the DevOps Suite console.")
        self.page_subtitle.setObjectName("subtitle")
        self.page_subtitle.setWordWrap(True)
        self.page_subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.page_subtitle)

        return header

    def _build_task_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("formPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(40, 34, 40, 40)
        layout.setSpacing(28)

        layout.addLayout(self._build_tabs())

        self.target_input = FocusLineEdit()
        self.target_field_label = "PARENT TASK URL"
        target_field = self._build_input_field(
            self.target_field_label,
            "↔",
            "https://devops.suite/task/DEV-12345",
            self.target_input,
        )
        layout.addWidget(target_field)

        self.title_input = FocusLineEdit()
        title_field = self._build_input_field(
            "TASK TITLE",
            "",
            "Short descriptive summary of the issue",
            self.title_input,
        )
        layout.addWidget(title_field)

        layout.addWidget(self._build_description_field())
        layout.addWidget(self._build_divider())
        layout.addLayout(self._build_controls())

        return panel

    def _build_tabs(self) -> QVBoxLayout:
        wrapper = QVBoxLayout()
        wrapper.setSpacing(0)

        tabs = QHBoxLayout()
        tabs.setSpacing(0)

        self.board_tab = QPushButton("Report via Board")
        self.board_tab.setObjectName("tabButton")
        self.board_tab.clicked.connect(lambda: self._set_mode("board"))
        tabs.addWidget(self.board_tab, 1)

        self.subtask_tab = QPushButton("Report as Subtask")
        self.subtask_tab.setObjectName("tabButton")
        self.subtask_tab.clicked.connect(lambda: self._set_mode("subtask"))
        tabs.addWidget(self.subtask_tab, 1)

        wrapper.addLayout(tabs)

        divider = QFrame()
        divider.setObjectName("tabDivider")
        wrapper.addWidget(divider)

        self._refresh_tabs()
        return wrapper

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        if hasattr(self, "target_input"):
            if mode == "subtask":
                self.page_title.setText("Report as Subtask")
                self.page_subtitle.setText("Submit high-priority technical issues to the DevOps Suite console.")
                self.target_label.setText("PARENT TASK URL")
                self.target_input.setPlaceholderText("https://app.clickup.com/t/...")
            else:
                self.page_title.setText("Create New Task")
                self.page_subtitle.setText("Log a new sprint item or developer task into the DevOps Suite ecosystem.")
                self.target_label.setText("BOARD / LIST URL")
                self.target_input.setPlaceholderText("ClickUp List URL or List ID")
        self._refresh_tabs()

    def _refresh_tabs(self) -> None:
        for button, active in (
            (self.board_tab, self.mode == "board"),
            (self.subtask_tab, self.mode == "subtask"),
        ):
            button.setProperty("active", active)
            button.style().unpolish(button)
            button.style().polish(button)

    def _build_input_field(
        self,
        label_text: str,
        icon_text: str,
        placeholder: str,
        input_widget: FocusLineEdit,
    ) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        if input_widget is getattr(self, "target_input", None):
            self.target_label = label
        layout.addWidget(label)

        frame = QFrame()
        frame.setObjectName("inputFrame")
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(18, 11, 18, 11)
        frame_layout.setSpacing(10)

        icon = QLabel(icon_text)
        icon.setObjectName("iconText")
        icon.setFixedWidth(24 if icon_text else 0)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if icon_text:
            frame_layout.addWidget(icon)

        input_widget.setPlaceholderText(placeholder)
        input_widget.set_focus_frame(frame)
        frame_layout.addWidget(input_widget, 1)

        layout.addWidget(frame)
        return field

    def _build_description_field(self) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        label = QLabel("TASK DESCRIPTION")
        label.setObjectName("fieldLabel")
        layout.addWidget(label)

        self.description_input = DescriptionEditor(
            Path(tempfile.gettempdir()) / "clickup-bug-reporter"
        )
        self.description_input.setPlaceholderText(
            "Provide steps to reproduce, environment details, and expected vs actual behavior..."
        )
        self.description_input.setFixedHeight(96)
        self.description_input.setFrameShape(QFrame.Shape.NoFrame)
        self.description_input.textChanged.connect(self._sync_screenshots_from_editor)

        editor_frame = QFrame()
        editor_frame.setObjectName("editorFrame")
        editor_frame.setFixedHeight(132)
        self.description_input.set_focus_frame(editor_frame)
        editor_layout = QHBoxLayout(editor_frame)
        editor_layout.setContentsMargins(18, 14, 18, 14)
        editor_layout.setSpacing(10)

        editor_layout.addWidget(self.description_input, 1)

        layout.addWidget(editor_frame)
        return field

    def _build_divider(self) -> QFrame:
        divider = QFrame()
        divider.setObjectName("divider")
        return divider

    def _build_controls(self) -> QHBoxLayout:
        controls = QHBoxLayout()
        controls.setSpacing(16)

        controls.addStretch(1)

        discard_button = QPushButton("Discard")
        discard_button.clicked.connect(self._discard_form)
        controls.addWidget(discard_button)

        self.create_button = QPushButton("Submit  >")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self.submit)
        controls.addWidget(self.create_button)

        return controls

    def _build_footer(self) -> QLabel:
        footer = QLabel("DevOps Suite Power User Console   •   Secure Session Active")
        footer.setObjectName("muted")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return footer

    def _build_toast(self, parent: QWidget) -> QFrame:
        toast = QFrame(parent)
        toast.setObjectName("toast")
        toast.setFixedSize(320, 86)

        layout = QHBoxLayout(toast)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        icon_box = QFrame()
        icon_box.setObjectName("iconBox")
        icon_box.setFixedSize(40, 40)
        icon_layout = QHBoxLayout(icon_box)
        icon_layout.setContentsMargins(0, 0, 0, 0)
        icon = QLabel("ok")
        icon.setObjectName("accent")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_layout.addWidget(icon)
        layout.addWidget(icon_box)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        title = QLabel("Task Submitted")
        title.setObjectName("accent")
        text_layout.addWidget(title)

        self.toast_detail = QLabel("Task has been initialized.")
        self.toast_detail.setObjectName("muted")
        text_layout.addWidget(self.toast_detail)

        layout.addLayout(text_layout, 1)

        return toast

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().resizeEvent(event)
        self._position_toast()

    def _position_toast(self) -> None:
        if not self.toast:
            return

        margin = 28
        x = max(margin, self.centralWidget().width() - self.toast.width() - margin)
        y = max(margin, self.centralWidget().height() - self.toast.height() - margin)
        self.toast.move(x, y)

    def _show_toast(self, detail: str) -> None:
        if not self.toast:
            return

        self.toast_detail.setText(detail)
        self._position_toast()
        self.toast.show()
        self.toast.raise_()
        QTimer.singleShot(4000, self.toast.hide)

    def _sync_screenshots_from_editor(self) -> None:
        previous_count = len(self.attachments)
        self.attachments = self.description_input.image_paths()
        count = len(self.attachments)

        if count != previous_count:
            self._set_status(f"{count} screenshot(s) in description")

    def _discard_form(self) -> None:
        self.target_input.clear()
        self.title_input.clear()
        self.description_input.clear()
        self.attachments = []
        self._set_status("Ready" if self.token else "CLICKUP_API_TOKEN missing")

    def submit(self) -> None:
        try:
            target_value = self.target_input.text()
            target_workspace_id = ""
            if self.mode == "subtask":
                target_reference = extract_task_reference(target_value)
                target_id = target_reference.task_id
                target_workspace_id = target_reference.workspace_id
                target_error = "Paste a ClickUp parent task URL or enter the parent task ID directly."
            else:
                target_id = extract_list_id(target_value)
                target_error = (
                    "Paste a ClickUp Board/List URL like "
                    "https://app.clickup.com/9003001220/v/l/8c9xtc4-115316, "
                    "or enter the List ID directly."
                )

            title = self.title_input.text().strip()
            description = self._description_markdown()

            if not self.token:
                raise ValueError("Add CLICKUP_API_TOKEN to .env and restart the app.")
            if not target_id:
                raise ValueError(target_error)
            if not title:
                raise ValueError("Task title is required.")
            if not description:
                raise ValueError("Task description or a pasted screenshot is required.")
        except ValueError as error:
            self._show_error("Missing information", str(error))
            return

        self.create_button.setEnabled(False)
        self.create_button.setText("Processing...")
        self._set_status("Creating task...")

        self.worker_thread = QThread()
        self.worker = CreateTaskWorker(
            self.token,
            target_id,
            title,
            description,
            mode=self.mode,
            target_workspace_id=target_workspace_id,
        )
        self.worker.moveToThread(self.worker_thread)

        self.worker_thread.started.connect(self.worker.run)
        self.worker.status_changed.connect(self._set_status)
        self.worker.finished.connect(self._task_created)
        self.worker.failed.connect(self._task_failed)
        self.worker.finished.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.finished.connect(self._clear_worker)
        self.worker_thread.start()

    def _description_markdown(self) -> str:
        text = self.description_input.toPlainText()
        image_paths = iter(self.description_input.image_paths())
        screenshot_index = 1
        parts: list[str] = []

        for char in text:
            if char == "\ufffc":
                image_path = next(image_paths, None)
                if not image_path:
                    parts.append("[Screenshot]")
                    continue

                parts.append(
                    f"\n\n![Screenshot {screenshot_index}]"
                    f"({_image_data_uri(image_path)})\n\n"
                )
                screenshot_index += 1
            else:
                parts.append(char)

        return "".join(parts).strip()

    def _task_created(self, task_url: str) -> None:
        self.create_button.setEnabled(True)
        self.create_button.setText("Submit  >")
        self._set_status("Task created.")
        self._show_toast("ClickUp task has been initialized.")

    def _task_failed(self, message: str) -> None:
        self.create_button.setEnabled(True)
        self.create_button.setText("Submit  >")
        self._set_status("Task creation failed.")
        self._show_error("ClickUp error", message)

    def _clear_worker(self) -> None:
        self.worker = None
        self.worker_thread = None

    def _set_status(self, message: str) -> None:
        self.status_message = message

    def _show_error(self, title: str, message: str) -> None:
        QMessageBox.critical(self, title, message)

    def _show_info(self, title: str, message: str) -> None:
        QMessageBox.information(self, title, message)


def extract_list_id(value: str) -> str:
    text = value.strip()

    if text.isdigit():
        return text

    for pattern in LIST_ID_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)

    return ""


def extract_task_id(value: str) -> str:
    return extract_task_reference(value).task_id


def extract_task_reference(value: str) -> TaskReference:
    text = value.strip()

    if not text:
        return TaskReference("")

    if "://" not in text and "/" not in text and " " not in text:
        return TaskReference(text)

    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    workspace_id = _query_value(query, WORKSPACE_QUERY_KEYS)

    task_id = _query_value(query, TASK_QUERY_KEYS)
    if task_id:
        return TaskReference(task_id, workspace_id)

    segments = [unquote(segment).strip() for segment in parsed.path.split("/") if segment]
    lower_segments = [segment.lower() for segment in segments]
    workspace_id = workspace_id or _workspace_id_from_segments(segments)

    if lower_segments and lower_segments[0] == "t":
        if len(segments) >= 3 and segments[1].isdigit():
            return TaskReference(segments[2], segments[1])
        if len(segments) >= 2:
            return TaskReference(segments[1], workspace_id)

    for marker in TASK_PATH_MARKERS:
        if marker in lower_segments:
            index = lower_segments.index(marker)
            if index + 1 < len(segments):
                return TaskReference(segments[index + 1], workspace_id)

    if "clickup.com" in parsed.netloc.lower():
        lower_segments = {segment.lower() for segment in segments}
        if lower_segments.intersection(NON_TASK_CLICKUP_PATH_PARTS):
            return TaskReference("", workspace_id)

    return TaskReference(segments[-1], workspace_id) if segments else TaskReference("")


def resolve_parent_task(
    client: ClickUpClient,
    parent_task_id: str,
    *,
    workspace_id: str = "",
) -> dict:
    try:
        return client.get_task(parent_task_id)
    except ClickUpError as standard_error:
        workspace_ids: list[str] = []
        if workspace_id:
            workspace_ids.append(workspace_id)

        if not _looks_like_custom_task_id(parent_task_id) and not workspace_ids:
            raise standard_error

        if _looks_like_custom_task_id(parent_task_id) and not workspace_ids:
            try:
                workspaces = client.get_workspaces()
            except ClickUpError:
                workspaces = []

            for workspace in workspaces:
                found_workspace_id = str(workspace.get("id", "")).strip()
                if found_workspace_id and found_workspace_id not in workspace_ids:
                    workspace_ids.append(found_workspace_id)

        for candidate_workspace_id in workspace_ids:
            try:
                return client.get_task(
                    parent_task_id,
                    custom_task_ids=True,
                    team_id=candidate_workspace_id,
                )
            except ClickUpError:
                continue

        raise ValueError(
            "Could not resolve the parent task. Paste the internal ClickUp task URL/ID, "
            "or make sure the custom task ID belongs to an accessible Workspace."
        ) from standard_error


def resolve_board_list_id(client: ClickUpClient, target_id: str) -> str:
    if _looks_like_clickup_view_id(target_id):
        view = client.get_view(target_id)
        return extract_view_list_id(view)

    return target_id


def extract_view_list_id(view: dict) -> str:
    if isinstance(view.get("view"), dict):
        view = view["view"]

    parent = view.get("parent")

    if isinstance(parent, dict):
        parent_id = str(parent.get("id", "")).strip()
        parent_type = str(parent.get("type", "")).strip().lower()
        if parent_id and parent_type in {"6", "list"}:
            return parent_id

    for key in ("list", "list_id"):
        value = view.get(key)
        if isinstance(value, dict) and value.get("id"):
            return str(value["id"])
        if isinstance(value, str) and value:
            return value

    raise ValueError(
        "That Board URL points to a ClickUp view, but the app could not resolve its parent List. "
        "Use a List-level view URL or paste the raw List ID."
    )


def extract_task_list_id(task: dict) -> str:
    task_list = task.get("list")

    if isinstance(task_list, dict) and task_list.get("id"):
        return str(task_list["id"])

    if isinstance(task_list, str) and task_list:
        return task_list

    if task.get("list_id"):
        return str(task["list_id"])

    raise ValueError("Could not determine the parent task's List ID from ClickUp.")


def _looks_like_custom_task_id(task_id: str) -> bool:
    return any(char.isalpha() for char in task_id) and "-" in task_id


def _looks_like_clickup_view_id(value: str) -> bool:
    return "-" in value and any(char.isalpha() for char in value)


def _query_value(query: dict[str, list[str]], keys: tuple[str, ...]) -> str:
    for key in keys:
        values = query.get(key)
        if values and values[0].strip():
            return values[0].strip()
    return ""


def _workspace_id_from_segments(segments: list[str]) -> str:
    for segment in segments:
        if segment.isdigit():
            return segment
    return ""


def _is_image_file(path: Path) -> bool:
    return path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


def _path_from_image_name(name: str) -> Path | None:
    if not name:
        return None

    url = QUrl(name)
    if url.isLocalFile():
        return Path(url.toLocalFile())

    path = Path(name)
    return path if path.exists() else None


def _image_data_uri(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"Could not read pasted screenshot: {path.name}") from exc

    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("ClickUp Bug Reporter")
    app.setWindowIcon(QIcon())
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = BugReporterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
