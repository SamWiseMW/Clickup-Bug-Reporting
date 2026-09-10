from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import mimetypes
import os
import random
import re
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import load_dotenv
from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QRectF,
    QObject,
    QStringListModel,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    QPropertyAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QRadialGradient,
    QTextDocument,
    QTextImageFormat,
)
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStyle,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from clickup_client import ClickUpClient, ClickUpError
from claude_client import (
    ClaudeClient, ClaudeError, normalise_page_url, title_from_formatted_description,
)


load_dotenv(Path(__file__).with_name(".env"))

LIST_ID_PATTERNS = [
    re.compile(r"/li(?:st)?/(\d+)", re.IGNORECASE),
    re.compile(r"/list/(\d+)", re.IGNORECASE),
    re.compile(r"/v/[lb]/([^/?#]+)", re.IGNORECASE),
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
IMAGE_PREVIEW_MAX_WIDTH = 320
IMAGE_PREVIEW_MAX_HEIGHT = 120
HOVER_FADE_MS = 180
DEFAULT_TASK_STATUS = "to do"
DEFAULT_CUSTOM_FIELDS = (
    ("Process Owner", "Developer"),
    ("Type of hours (ACCESS)", "ACCESS Tech"),
    ("Dev category", "Development"),
)
APP_ICON_PATH = Path(__file__).with_name("public") / (
    "favicon.ico" if sys.platform == "win32" else "favicon.png"
)
WINDOWS_APP_USER_MODEL_ID = "MW.ClickUpBugReporter"


def configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return

    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            WINDOWS_APP_USER_MODEL_ID
        )
    except (AttributeError, OSError):
        pass


@dataclass(frozen=True)
class AccentTheme:
    accent: str
    button: str
    button_hover: str
    button_text: str
    muted_icon: str
    selection: str
    accent_rgb: tuple[int, int, int]
    button_rgb: tuple[int, int, int]


def create_accent_theme(hue: int | None = None) -> AccentTheme:
    selected_hue = random.SystemRandom().randrange(360) if hue is None else hue % 360
    accent_color = QColor.fromHsv(selected_hue, 150, 200)
    button_color = QColor(accent_color)
    button_hover_color = QColor.fromHsv(selected_hue, 140, 215)
    muted_icon_color = QColor.fromHsv(selected_hue, 90, 150)
    selection_color = QColor.fromHsv(selected_hue, 170, 135)

    linear_channels = []
    for channel in (button_color.red(), button_color.green(), button_color.blue()):
        normalized = channel / 255
        linear_channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    luminance = (
        0.2126 * linear_channels[0]
        + 0.7152 * linear_channels[1]
        + 0.0722 * linear_channels[2]
    )
    button_text = "#111315" if luminance >= 0.18 else "#ffffff"

    return AccentTheme(
        accent=accent_color.name(),
        button=button_color.name(),
        button_hover=button_hover_color.name(),
        button_text=button_text,
        muted_icon=muted_icon_color.name(),
        selection=selection_color.name(),
        accent_rgb=(accent_color.red(), accent_color.green(), accent_color.blue()),
        button_rgb=(button_color.red(), button_color.green(), button_color.blue()),
    )


ACTIVE_THEME = create_accent_theme()

APP_STYLE_TEMPLATE = """
* {
    font-family: "Segoe UI", "Inter", Arial, sans-serif;
    letter-spacing: 0;
}

QMainWindow,
QWidget#root {
    background: #070809;
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

QFrame#statusPill {
    background: rgba(__BUTTON_RGB__, 70);
    border: 1px solid rgba(__ACCENT_RGB__, 90);
    border-radius: 12px;
}

QFrame#toast {
    background: rgba(__BUTTON_RGB__, 150);
    border: 1px solid rgba(__ACCENT_RGB__, 110);
    border-radius: 12px;
}

QFrame#divider {
    background: rgba(__ACCENT_RGB__, 45);
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
    color: #bfc1c3;
}

QLabel#title {
    color: #e5e2e1;
    font-size: 32px;
    font-weight: 700;
}

QLabel#accent {
    color: __ACCENT__;
    font-weight: 700;
}

QLabel#iconText {
    color: #bfc1c3;
    font-size: 20px;
    font-weight: 700;
}

QLineEdit {
    background: transparent;
    color: #e5e2e1;
    border: none;
    padding: 0;
    min-height: 24px;
    selection-background-color: __SELECTION__;
}

QLineEdit::placeholder {
    color: #55585b;
}

QLineEdit:focus {
    border: none;
}

QAbstractItemView {
    background: #201f1f;
    color: #e5e2e1;
    border: 1px solid #484a4d;
    selection-background-color: __SELECTION__;
}

QTextEdit {
    background: transparent;
    color: #e5e2e1;
    border: none;
    border-radius: 0;
    padding: 0;
    line-height: 1.4;
    selection-background-color: __SELECTION__;
}

QPushButton {
    background: #201f1f;
    color: #e5e2e1;
    border: 1px solid rgba(72, 74, 77, 96);
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
    color: #85888b;
    background: #1c1b1b;
    border-color: rgba(72, 74, 77, 70);
}

QPushButton#primaryButton {
    background: __BUTTON__;
    border: 1px solid rgba(__ACCENT_RGB__, 115);
    color: __BUTTON_TEXT__;
    border-radius: 8px;
    padding: 12px 30px;
    font-size: 16px;
}

QPushButton#primaryButton:hover {
    background: __BUTTON_HOVER__;
}

QPushButton#ghostButton {
    background: transparent;
    border: none;
    color: #bfc1c3;
    padding: 8px 14px;
}

QPushButton#ghostButton:hover {
    background: #2a2a2a;
    color: __ACCENT__;
}

QPushButton#compactGhostButton {
    background: transparent;
    border: none;
    color: #bfc1c3;
    padding: 3px 10px;
}

QPushButton#compactGhostButton:hover {
    background: #2a2a2a;
    color: __ACCENT__;
}

QPushButton#tabButton {
    background: transparent;
    border: none;
    border-radius: 0;
    color: #bfc1c3;
    padding: 0 0 14px 0;
    font-size: 14px;
    font-weight: 500;
}

QPushButton#tabButton:hover {
    color: #e5e2e1;
    background: transparent;
}

QPushButton#tabButton[active="true"] {
    color: __ACCENT__;
    border-bottom: 2px solid __ACCENT__;
    font-weight: 700;
}

QScrollBar:vertical {
    background: #1c1b1b;
    width: 12px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: #484a4d;
    min-height: 32px;
    border-radius: 6px;
}

QScrollBar::handle:vertical:hover {
    background: #85888b;
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


def build_app_style(theme: AccentTheme) -> str:
    replacements = {
        "__ACCENT__": theme.accent,
        "__BUTTON__": theme.button,
        "__BUTTON_HOVER__": theme.button_hover,
        "__BUTTON_TEXT__": theme.button_text,
        "__SELECTION__": theme.selection,
        "__ACCENT_RGB__": ", ".join(map(str, theme.accent_rgb)),
        "__BUTTON_RGB__": ", ".join(map(str, theme.button_rgb)),
    }
    style = APP_STYLE_TEMPLATE
    for token, value in replacements.items():
        style = style.replace(token, value)
    return style


APP_STYLE = build_app_style(ACTIVE_THEME)


def tint_icon(icon: QIcon, color: QColor, size: int = 16) -> QIcon:
    source = icon.pixmap(size, size)
    tinted = QPixmap(source.size())
    tinted.fill(Qt.GlobalColor.transparent)

    painter = QPainter(tinted)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(
        QPainter.CompositionMode.CompositionMode_SourceIn
    )
    painter.fillRect(tinted.rect(), color)
    painter.end()

    return QIcon(tinted)


@dataclass(frozen=True)
class TaskReference:
    task_id: str
    workspace_id: str = ""


class AtmosphericRoot(QWidget):
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#070809"))

        width = max(self.width(), 1)
        height = max(self.height(), 1)
        accent_red, accent_green, accent_blue = ACTIVE_THEME.accent_rgb
        button_red, button_green, button_blue = ACTIVE_THEME.button_rgb

        first_glow = QRadialGradient(QPointF(width * 0.2, height * 0.18), width * 0.55)
        first_glow.setColorAt(
            0.0,
            QColor(button_red, button_green, button_blue, 54),
        )
        first_glow.setColorAt(
            1.0,
            QColor(button_red, button_green, button_blue, 0),
        )
        painter.fillRect(self.rect(), first_glow)

        second_glow = QRadialGradient(QPointF(width * 0.8, height * 0.16), width * 0.45)
        second_glow.setColorAt(
            0.0,
            QColor(button_red, button_green, button_blue, 38),
        )
        second_glow.setColorAt(
            1.0,
            QColor(button_red, button_green, button_blue, 0),
        )
        painter.fillRect(self.rect(), second_glow)

        corner_glow = QRadialGradient(QPointF(-80, -80), 360)
        corner_glow.setColorAt(
            0.0,
            QColor(accent_red, accent_green, accent_blue, 24),
        )
        corner_glow.setColorAt(
            1.0,
            QColor(accent_red, accent_green, accent_blue, 0),
        )
        painter.fillRect(self.rect(), corner_glow)

        bottom_fade = QLinearGradient(0, height * 0.42, 0, height)
        bottom_fade.setColorAt(0.0, QColor(3, 4, 5, 0))
        bottom_fade.setColorAt(1.0, QColor(3, 4, 5, 235))
        painter.fillRect(self.rect(), bottom_fade)

        painter.setPen(QColor(accent_red, accent_green, accent_blue, 16))
        for index in range(260):
            digest = hashlib.blake2b(str(index).encode(), digest_size=4).digest()
            x = int.from_bytes(digest[:2], "big") % width
            y = int.from_bytes(digest[2:], "big") % height
            painter.drawPoint(x, y)

        super().paintEvent(event)


class HoverFadeFrame(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self._hover_strength = 0.0
        self._hover_animation = QPropertyAnimation(self, b"hoverStrength", self)
        self._hover_animation.setDuration(HOVER_FADE_MS)
        self._hover_animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def get_hover_strength(self) -> float:
        return self._hover_strength

    def set_hover_strength(self, value: float) -> None:
        self._hover_strength = max(0.0, min(1.0, value))
        self.update()

    hoverStrength = Property(float, get_hover_strength, set_hover_strength)

    def enterEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_hover_state(True)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802 - Qt override name
        self._set_hover_state(False)
        super().leaveEvent(event)

    def _set_hover_state(self, hovered: bool) -> None:
        self._hover_animation.stop()
        self._hover_animation.setStartValue(self._hover_strength)
        self._hover_animation.setEndValue(1.0 if hovered else 0.0)
        self._hover_animation.start()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override name
        super().paintEvent(event)

        if self._hover_strength <= 0:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = QColor(ACTIVE_THEME.accent)
        color.setAlpha(int(190 * self._hover_strength))
        pen = QPen(color)
        pen.setWidthF(1.2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        painter.drawRoundedRect(rect, 8, 8)


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
        preview_width, preview_height = self._preview_size(image)
        if preview_width and preview_height:
            image_format.setWidth(preview_width)
            image_format.setHeight(preview_height)

        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.insertImage(image_format)
        cursor.insertBlock()
        cursor.endEditBlock()
        self.setTextCursor(cursor)

    def _preview_size(self, image: QImage) -> tuple[int, int]:
        width = image.width()
        height = image.height()
        if width <= 0 or height <= 0:
            return 0, 0

        scale = min(
            IMAGE_PREVIEW_MAX_WIDTH / width,
            IMAGE_PREVIEW_MAX_HEIGHT / height,
            1,
        )
        return max(1, int(width * scale)), max(1, int(height * scale))

    def _coerce_image(self, value) -> QImage:
        if isinstance(value, QImage):
            return value
        if hasattr(value, "toImage"):
            return value.toImage()
        return QImage()


class LoadAssigneesWorker(QObject):
    finished = Signal(list)
    failed = Signal(str)

    def __init__(self, token: str) -> None:
        super().__init__()
        self.token = token

    def run(self) -> None:
        try:
            client = ClickUpClient(lambda: self.token)
            self.finished.emit(extract_workspace_members(client.get_workspaces()))
        except (ClickUpError, KeyError, ValueError) as error:
            self.failed.emit(str(error))


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
        assignee_id: int | None = None,
        claude_api_key: str = "",
        title_source: str = "",
        page_url: str = "",
    ) -> None:
        super().__init__()
        self.token = token
        self.target_id = target_id
        self.title = title
        self.description = description
        self.mode = mode
        self.target_workspace_id = target_workspace_id
        self.assignee_id = assignee_id
        self.claude_api_key = claude_api_key
        self.title_source = title_source
        self.page_url = page_url

    def run(self) -> None:
        try:
            client = ClickUpClient(lambda: self.token)
            claude_client = ClaudeClient(self.claude_api_key)
            page_context = {"page_url": normalise_page_url(self.page_url)} if self.page_url else {}

            self.status_changed.emit("Organising description with Claude...")
            description_source, screenshots = extract_screenshot_placeholders(
                self.description
            )
            description = claude_client.format_bug_description(description_source, **page_context)
            if not description.strip():
                raise ClaudeError(
                    "Add a clear description of the issue before submitting."
                )
            title = self.title.strip()
            if not title:
                self.status_changed.emit("Generating title with Claude...")
                title_source = self.title_source.strip() or description_source
                title = claude_client.generate_bug_title(title_source, **page_context)
                if not title.strip():
                    title = title_from_formatted_description(
                        description, title_source, **page_context
                    )
            if not title.strip():
                raise ClaudeError(
                    "Add a clear description so a task title can be generated."
                )
            description = restore_screenshot_markdown(description, screenshots)

            self.status_changed.emit("Creating subtask..." if self.mode == "subtask" else "Creating task...")
            task_dates = task_dates_for_today()

            if self.mode == "subtask":
                parent_task = resolve_parent_task(
                    client,
                    self.target_id,
                    workspace_id=self.target_workspace_id,
                )
                list_id = extract_task_list_id(parent_task)
                parent_task_id = str(parent_task.get("id") or self.target_id)
                payload = {
                    "name": title,
                    "markdown_content": description,
                    "tags": ["bug"],
                    "status": DEFAULT_TASK_STATUS,
                    "parent": parent_task_id,
                    **task_dates,
                }
            else:
                list_id = resolve_board_list_id(client, self.target_id)
                payload = {
                    "name": title,
                    "markdown_content": description,
                    "tags": ["bug"],
                    "status": DEFAULT_TASK_STATUS,
                    **task_dates,
                }

            if self.assignee_id is not None:
                payload["assignees"] = [self.assignee_id]

            payload["custom_fields"] = resolve_default_custom_fields(
                client.get_list_custom_fields(list_id)
            )
            task = create_task_with_status_fallback(client, list_id, payload)
            self.finished.emit(task.get("url", ""))
        except (ClaudeError, ClickUpError, KeyError, ValueError) as error:
            self.failed.emit(str(error))


class BugReporterWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("ClickUp Bug Reporter")
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(1024, 760)
        self.resize(1280, 900)

        self.token = os.getenv("CLICKUP_API_TOKEN", "").strip()
        self.claude_api_key = (
            os.getenv("ANTHROPIC_API_KEY", "").strip()
            or os.getenv("CLAUDE_API_KEY", "").strip()
        )
        self.attachments: list[Path] = []
        self.status_message = "Ready"
        self.mode = "board"
        self.worker_thread: QThread | None = None
        self.worker: CreateTaskWorker | None = None
        self.assignee_thread: QThread | None = None
        self.assignee_worker: LoadAssigneesWorker | None = None
        self.assignee_ids_by_label: dict[str, int] = {}
        self.show_assignee_load_errors = False
        self.toast: QFrame | None = None

        self.setCentralWidget(self._build_ui())
        self._set_status("Ready" if self.token else "CLICKUP_API_TOKEN missing")
        QTimer.singleShot(0, self._load_assignees)

    def _build_ui(self) -> QWidget:
        root = AtmosphericRoot()
        root.setObjectName("root")

        layout = QVBoxLayout(root)
        layout.setContentsMargins(40, 16, 40, 24)
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
        content_layout.setSpacing(24)

        content_layout.addLayout(self._build_header())
        content_layout.addWidget(self._build_task_panel())

        layout.addStretch(2)
        layout.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(2)

        self.toast = self._build_toast(root)
        self.toast.hide()

        return root

    def _build_header(self) -> QVBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(0)
        title_row.addStretch(1)

        title_block = QVBoxLayout()
        title_block.setSpacing(2)

        self.page_title = QLabel("Create New Task")
        self.page_title.setObjectName("title")
        self.page_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_block.addWidget(self.page_title)

        title_row.addLayout(title_block, 1)
        title_row.addStretch(1)
        header.addLayout(title_row)

        return header

    def _build_task_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("formPanel")

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(40, 24, 40, 32)
        layout.setSpacing(18)

        layout.addLayout(self._build_tabs())

        self.target_input = FocusLineEdit()
        self.target_field_label = "BOARD / LIST URL"
        target_field = self._build_input_field(
            self.target_field_label,
            "",
            "ClickUp List URL or List ID",
            self.target_input,
        )
        layout.addWidget(target_field)

        layout.addWidget(self._build_assignee_field())

        self.title_input = FocusLineEdit()
        self.title_field = self._build_input_field(
            "TASK TITLE (OPTIONAL)",
            "",
            "Leave blank to generate: Page name | brief issue",
            self.title_input,
        )
        self.title_field.hide()
        layout.addWidget(self.title_field)

        self.page_url_input = FocusLineEdit()
        self.page_url_field = self._build_input_field(
            "PAGE URL (OPTIONAL)",
            "",
            "https://example.com/about-us",
            self.page_url_input,
        )
        layout.addWidget(self.page_url_field)

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
        if mode == self.mode:
            return

        self.mode = mode
        if hasattr(self, "target_input"):
            self._apply_mode_copy(mode)
        self._refresh_tabs()

    def _apply_mode_copy(self, mode: str) -> None:
        if mode == "subtask":
            self.page_title.setText("Create New Task")
            self.target_label.setText("PARENT TASK URL")
            self.target_input.setPlaceholderText("https://app.clickup.com/t/...")
        else:
            self.page_title.setText("Create New Task")
            self.target_label.setText("BOARD / LIST URL")
            self.target_input.setPlaceholderText("ClickUp List URL or List ID")

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
            self.target_field = field
        layout.addWidget(label)

        frame = HoverFadeFrame()
        frame.setObjectName("inputFrame")
        frame.setFixedHeight(40)
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(18, 5, 18, 5)
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
            "Provide steps to reproduce, environment details, and expected vs actual behaviour..."
        )
        self.description_input.setMinimumHeight(152)
        self.description_input.setFrameShape(QFrame.Shape.NoFrame)
        self.description_input.textChanged.connect(self._sync_screenshots_from_editor)

        editor_frame = HoverFadeFrame()
        editor_frame.setObjectName("editorFrame")
        editor_frame.setMinimumHeight(188)
        self.description_input.set_focus_frame(editor_frame)
        editor_layout = QHBoxLayout(editor_frame)
        editor_layout.setContentsMargins(18, 14, 18, 14)
        editor_layout.setSpacing(10)

        editor_layout.addWidget(self.description_input, 1)

        layout.addWidget(editor_frame)
        return field

    def _build_assignee_field(self) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        label = QLabel("ASSIGNEE")
        label.setObjectName("fieldLabel")
        layout.addWidget(label)

        frame = HoverFadeFrame()
        frame.setObjectName("inputFrame")
        frame.setFixedHeight(40)
        frame_layout = QHBoxLayout(frame)
        frame_layout.setContentsMargins(18, 4, 10, 4)
        frame_layout.setSpacing(10)

        self.assignee_input = FocusLineEdit()
        self.assignee_input.setPlaceholderText("Loading assignees...")
        self.assignee_input.setEnabled(False)
        self.assignee_input.set_focus_frame(frame)

        self.assignee_model = QStringListModel([], self)
        self.assignee_completer = QCompleter(self.assignee_model, self)
        self.assignee_completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.assignee_completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.assignee_completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.assignee_completer.setMaxVisibleItems(12)
        self.assignee_input.setCompleter(self.assignee_completer)
        frame_layout.addWidget(self.assignee_input, 1)

        self.reload_assignees_button = QPushButton()
        self.reload_assignees_button.setObjectName("compactGhostButton")
        self.reload_assignees_button.setAccessibleName("Refresh assignees")
        self.reload_assignees_button.setToolTip("Refresh assignees")
        self.reload_assignees_button.setIcon(
            tint_icon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload),
                QColor(ACTIVE_THEME.muted_icon),
            )
        )
        self.reload_assignees_button.setFixedSize(28, 28)
        self.reload_assignees_button.clicked.connect(
            lambda: self._load_assignees(show_errors=True)
        )
        frame_layout.addWidget(self.reload_assignees_button)

        layout.addWidget(frame)
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

        self.create_button = QPushButton("Submit")
        self.create_button.setObjectName("primaryButton")
        self.create_button.clicked.connect(self.submit)
        controls.addWidget(self.create_button)

        return controls

    def _build_toast(self, parent: QWidget) -> QFrame:
        toast = QFrame(parent)
        toast.setObjectName("toast")
        toast.setFixedHeight(86)

        layout = QHBoxLayout(toast)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        self.toast_title = QLabel("Task Submitted")
        self.toast_title.setObjectName("accent")
        text_layout.addWidget(self.toast_title)

        self.toast_detail = QLabel("Task has been initialised.")
        self.toast_detail.setObjectName("muted")
        text_layout.addWidget(self.toast_detail)

        layout.addLayout(text_layout, 1)
        self._resize_toast_to_content(toast)

        return toast

    def _resize_toast_to_content(self, toast: QFrame | None = None) -> None:
        target = toast or self.toast
        if not target:
            return

        content_width = max(
            self.toast_title.sizeHint().width(),
            self.toast_detail.sizeHint().width(),
        )
        target.setFixedWidth(content_width + 32)

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
        self._resize_toast_to_content()
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

    def _load_assignees(self, *, show_errors: bool = False) -> None:
        if self.assignee_thread is not None:
            return

        if not self.token:
            self.assignee_ids_by_label.clear()
            self.assignee_model.setStringList([])
            self.assignee_input.clear()
            self.assignee_input.setPlaceholderText("Configure CLICKUP_API_TOKEN first")
            self.assignee_input.setEnabled(False)
            return

        self.show_assignee_load_errors = show_errors
        self.reload_assignees_button.setEnabled(False)
        self.assignee_input.setEnabled(False)
        self.assignee_input.clear()
        self.assignee_input.setPlaceholderText("Loading assignees...")

        self.assignee_thread = QThread()
        self.assignee_worker = LoadAssigneesWorker(self.token)
        self.assignee_worker.moveToThread(self.assignee_thread)

        self.assignee_thread.started.connect(self.assignee_worker.run)
        self.assignee_worker.finished.connect(self._assignees_loaded)
        self.assignee_worker.failed.connect(self._assignees_failed)
        self.assignee_worker.finished.connect(self.assignee_thread.quit)
        self.assignee_worker.failed.connect(self.assignee_thread.quit)
        self.assignee_thread.finished.connect(self.assignee_worker.deleteLater)
        self.assignee_thread.finished.connect(self.assignee_thread.deleteLater)
        self.assignee_thread.finished.connect(self._clear_assignee_worker)
        self.assignee_thread.start()

    def _assignees_loaded(self, members: list[dict]) -> None:
        self.assignee_ids_by_label = {
            member["label"].casefold(): member["id"] for member in members
        }
        self.assignee_model.setStringList([member["label"] for member in members])
        self.assignee_input.clear()
        self.assignee_input.setPlaceholderText("Type to search assignees (optional)")
        self.assignee_input.setEnabled(True)
        self.reload_assignees_button.setEnabled(True)
        self._set_status(f"Loaded {len(members)} assignee(s).")

    def _assignees_failed(self, message: str) -> None:
        self.assignee_ids_by_label.clear()
        self.assignee_model.setStringList([])
        self.assignee_input.clear()
        self.assignee_input.setPlaceholderText("Unable to load assignees")
        self.assignee_input.setEnabled(False)
        self.reload_assignees_button.setEnabled(True)
        self._set_status("Assignee loading failed.")

        if self.show_assignee_load_errors:
            self._show_error("ClickUp error", message)

    def _clear_assignee_worker(self) -> None:
        self.assignee_worker = None
        self.assignee_thread = None

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
            page_url = normalise_page_url(self.page_url_input.text())
            description = self._description_markdown()
            title_source = (
                self.description_input.toPlainText()
                .replace("\ufffc", "[Screenshot]")
                .strip()
            )
            assignee_id = selected_assignee_id(
                self.assignee_input.text(),
                self.assignee_ids_by_label,
            )

            if not self.token:
                raise ValueError("Add CLICKUP_API_TOKEN to .env and restart the app.")
            if not target_id:
                raise ValueError(target_error)
            if not description:
                raise ValueError("Task description or a pasted screenshot is required.")
            if not self.claude_api_key:
                raise ValueError(
                    "Add ANTHROPIC_API_KEY to .env for AI description formatting."
                )
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
            assignee_id=assignee_id,
            claude_api_key=self.claude_api_key,
            title_source=title_source,
            page_url=page_url,
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
        self.create_button.setText("Submit")
        self.title_input.clear()
        self.description_input.clear()
        self.attachments = []
        self._set_status("Task created.")
        self._show_toast("ClickUp task has been initialised.")

    def _task_failed(self, message: str) -> None:
        self.create_button.setEnabled(True)
        self.create_button.setText("Submit")
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


def task_dates_for_today(now: datetime | None = None) -> dict[str, int | bool]:
    local_now = now or datetime.now()
    today_at_4am = local_now.replace(hour=4, minute=0, second=0, microsecond=0)
    timestamp_ms = int(today_at_4am.timestamp() * 1000)

    return {
        "start_date": timestamp_ms,
        "start_date_time": False,
        "due_date": timestamp_ms,
        "due_date_time": False,
    }


def extract_workspace_members(workspaces: list[dict]) -> list[dict]:
    members_by_id: dict[int, dict] = {}
    multiple_workspaces = len(workspaces) > 1

    for workspace in workspaces:
        workspace_name = str(workspace.get("name") or "Workspace")

        for membership in workspace.get("members") or []:
            user = membership.get("user", membership)

            try:
                user_id = int(user["id"])
            except (KeyError, TypeError, ValueError):
                continue

            name = str(user.get("username") or user.get("email") or f"User {user_id}")
            email = str(user.get("email") or "")
            label = name if not email or email.casefold() == name.casefold() else f"{name} ({email})"
            if multiple_workspaces:
                label = f"{label} — {workspace_name}"

            members_by_id.setdefault(user_id, {"id": user_id, "label": label})

    return sorted(members_by_id.values(), key=lambda member: member["label"].casefold())


def extract_screenshot_placeholders(description: str) -> tuple[str, dict[str, str]]:
    screenshot_pattern = re.compile(
        r"!\[Screenshot \d+\]\(data:[^)]+\)",
        re.IGNORECASE,
    )
    screenshots: dict[str, str] = {}

    def replace_screenshot(match: re.Match) -> str:
        token = f"[[SCREENSHOT_{len(screenshots) + 1}]]"
        screenshots[token] = match.group(0)
        return token

    return screenshot_pattern.sub(replace_screenshot, description), screenshots


def restore_screenshot_markdown(
    description: str,
    screenshots: dict[str, str],
) -> str:
    restored = description
    for token, screenshot_markdown in screenshots.items():
        if token not in restored:
            restored = f"{restored.rstrip()}\n\n{screenshot_markdown}"
        else:
            restored = restored.replace(token, screenshot_markdown, 1)
            restored = restored.replace(token, "")

    # Any remaining placeholder has no corresponding pasted screenshot.
    restored = re.sub(r"\[\[SCREENSHOT_\d+\]\]", "", restored, flags=re.IGNORECASE)
    return restored.strip()


def selected_assignee_id(text: str, assignee_ids_by_label: dict[str, int]) -> int | None:
    label = text.strip()
    if not label:
        return None

    assignee_id = assignee_ids_by_label.get(label.casefold())
    if assignee_id is None:
        raise ValueError(
            "Select an assignee from the search suggestions, or leave the assignee blank."
        )

    return assignee_id


def resolve_default_custom_fields(fields: list[dict]) -> list[dict]:
    """Set available defaults; leave missing or ambiguous fields unset."""
    def normalise_name(value: str) -> str:
        # Ignore decorative emoji, capitalisation and whitespace in field labels.
        return " ".join("".join(
            char for char in value.casefold() if char.isalnum() or char.isspace()
        ).split())

    values = []
    for name, option_name in DEFAULT_CUSTOM_FIELDS:
        matches = [
            field for field in fields
            if normalise_name(str(field.get("name", ""))) == normalise_name(name)
        ]
        if len(matches) != 1:
            continue
        field = matches[0]
        scoped_types = [
            item.get("object_id") for item in field.get("applied_objects", [])
            if str(item.get("object_type")) == "19"
        ]
        if scoped_types and not any(str(value) == "0" for value in scoped_types):
            continue
        if field.get("type") != "drop_down" or not field.get("id"):
            continue
        options = field.get("type_config", {}).get("options", [])
        selected = [
            option for option in options
            if normalise_name(str(option.get("name", ""))) == normalise_name(option_name)
        ]
        if len(selected) != 1 or not selected[0].get("id"):
            continue
        values.append({"id": field["id"], "value": selected[0]["id"]})
    return values


def create_task_with_status_fallback(
    client: ClickUpClient,
    list_id: str,
    payload: dict,
) -> dict:
    try:
        return client.create_task(list_id, payload)
    except ClickUpError as error:
        if "status" not in payload or "status not found" not in str(error).casefold():
            raise

        fallback_payload = dict(payload)
        fallback_payload.pop("status")
        return client.create_task(list_id, fallback_payload)


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
    configure_windows_app_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("ClickUp Bug Reporter")
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    app.setStyle("Fusion")
    app.setStyleSheet(APP_STYLE)

    font = QFont("Segoe UI", 10)
    app.setFont(font)

    window = BugReporterWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
