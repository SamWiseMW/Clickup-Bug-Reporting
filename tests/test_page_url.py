import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from app import APP_STYLE, BugReporterWindow, CreateTaskWorker
from claude_client import ClaudeClient, normalise_page_url


class PageUrlTests(unittest.TestCase):
    def test_page_url_drives_title_despite_other_locations_in_description(self):
        client = ClaudeClient("key")
        for url, page in (
            ("https://example.com/", "Home"),
            ("https://example.com/about-us/", "About Us"),
            ("https://example.com/services/contact-us.html", "Contact Us"),
            ("https://example.com/#/settings", "Settings"),
        ):
            with self.subTest(url=url), patch.object(
                client, "_request_text", return_value="Vimeo | Missing arrow on mobile"
            ):
                title = client.generate_bug_title(
                    "Missing arrow on home mobile. https://vimeo.com/123456789",
                    page_url=url,
                )
                self.assertEqual(title, f"{page} | Missing arrow on mobile")

    def test_location_uses_page_url_and_retains_video_evidence(self):
        client = ClaudeClient("key")
        with patch.object(client, "_request_text", return_value=(
            "**URLs / Location:**\n\n- https://vimeo.com/123456789\n\n"
            "**Requirements:**\n\n1. Fix padding\n\n[[SCREENSHOT_1]]"
        )):
            result = client.format_bug_description(
                "Fix padding https://vimeo.com/123456789 [[SCREENSHOT_1]]",
                page_url="https://example.com/about-us?preview=1",
            )
        location, requirements = result.split("**Requirements:**", 1)
        self.assertEqual(location, "**URLs / Location:**\n\n- https://example.com/about-us?preview=1\n\n")
        self.assertIn("https://vimeo.com/123456789", requirements)
        self.assertIn("[[SCREENSHOT_1]]", requirements)

    def test_global_scope_is_preserved_with_page_url(self):
        client = ClaudeClient("key")
        with patch.object(client, "_request_text", return_value="Home | Fix padding"):
            self.assertEqual(
                client.generate_bug_title("Fix padding on all pages", page_url="https://example.com/"),
                "Global | Fix padding",
            )
        with patch.object(client, "_request_text", return_value=(
            "**URLs / Location:**\n\n**Requirements:**\n\n1. Fix padding"
        )):
            result = client.format_bug_description("Global: fix padding", page_url="https://example.com/")
        self.assertIn("- [Global](https://example.com/)", result)

    def test_page_url_validation(self):
        self.assertEqual(normalise_page_url("  "), "")
        self.assertEqual(normalise_page_url(" www.example.com/about-us "), "https://www.example.com/about-us")
        for invalid in ("not a url", "https://", "file:///C:/test", "https://example.com/with spaces"):
            with self.subTest(url=invalid), self.assertRaises(ValueError):
                normalise_page_url(invalid)

    def test_worker_passes_page_url_to_both_ai_calls_for_both_tabs(self):
        for mode in ("board", "subtask"):
            with self.subTest(mode=mode):
                clickup = MagicMock()
                clickup.get_task.return_value = {"id": "parent", "list": {"id": "456"}}
                clickup.get_list_custom_fields.return_value = []
                clickup.create_task.return_value = {"url": "task-url"}
                claude = MagicMock()
                claude.format_bug_description.return_value = "Formatted report"
                claude.generate_bug_title.return_value = "Home | Fix padding"
                worker = CreateTaskWorker(
                    "token", "456", "", "Fix padding", mode=mode,
                    title_source="Fix padding", page_url="https://example.com/",
                )
                with patch("app.ClickUpClient", return_value=clickup), patch("app.ClaudeClient", return_value=claude):
                    worker.run()
                claude.format_bug_description.assert_called_once_with("Fix padding", page_url="https://example.com/")
                claude.generate_bug_title.assert_called_once_with("Fix padding", page_url="https://example.com/")
                clickup.create_task.assert_called_once()


class PageUrlUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.qt_app.setStyleSheet(APP_STYLE)

    def test_page_url_survives_submission_and_tab_changes(self):
        with patch.object(BugReporterWindow, "_load_assignees"), patch.object(BugReporterWindow, "_show_toast"):
            window = BugReporterWindow()
            try:
                window.page_url_input.setText("https://example.com/about-us")
                window.description_input.setPlainText("Fix padding")
                window.title_input.setText("About Us | Fix padding")
                window._task_created("task-url")
                self.assertEqual(window.page_url_input.text(), "https://example.com/about-us")
                self.assertEqual(window.description_input.toPlainText(), "")
                self.assertEqual(window.title_input.text(), "")
                window._set_mode("subtask")
                self.assertEqual(window.page_url_input.text(), "https://example.com/about-us")
                window.resize(1024, 760)
                window.show()
                self.qt_app.processEvents()
                self.assertLess(window.page_url_input.mapTo(window, window.page_url_input.rect().bottomLeft()).y(),
                                window.description_input.mapTo(window, window.description_input.rect().topLeft()).y())
                self.assertLessEqual(window.create_button.mapTo(window, window.create_button.rect().bottomRight()).y(), window.height())
            finally:
                window.close()
                window.deleteLater()
                self.qt_app.processEvents()
