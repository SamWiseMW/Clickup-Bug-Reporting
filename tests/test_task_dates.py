from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import MagicMock, call, patch
from types import SimpleNamespace

from app import (
    BugReporterWindow,
    ClickUpError,
    CreateTaskWorker,
    build_app_style,
    create_accent_theme,
    create_task_with_status_fallback,
    extract_screenshot_placeholders,
    extract_workspace_members,
    restore_screenshot_markdown,
    selected_assignee_id,
    task_dates_for_today,
)


class TaskDatesForTodayTests(unittest.TestCase):
    def test_generated_theme_changes_accent_but_keeps_background_dark(self) -> None:
        red_theme = create_accent_theme(0)
        blue_theme = create_accent_theme(220)
        style = build_app_style(red_theme)

        self.assertNotEqual(red_theme.accent, blue_theme.accent)
        self.assertEqual(red_theme.button, red_theme.accent)
        self.assertNotEqual(red_theme.muted_icon, red_theme.accent)
        self.assertIn("background: #070809", style)
        self.assertIn(f"background: {red_theme.button}", style)
        self.assertIn(f"color: {red_theme.accent}", style)
        self.assertNotIn("__ACCENT", style)
        self.assertEqual(create_accent_theme(60).button_text, "#111315")
        self.assertEqual(create_accent_theme(240).button_text, "#ffffff")

    def test_sets_start_and_due_date_to_same_date_without_times(self) -> None:
        local_timezone = timezone(timedelta(hours=10))
        now = datetime(2026, 9, 4, 18, 37, tzinfo=local_timezone)

        dates = task_dates_for_today(now)

        expected_timestamp = int(
            datetime(2026, 9, 4, 4, 0, tzinfo=local_timezone).timestamp() * 1000
        )
        self.assertEqual(
            dates,
            {
                "start_date": expected_timestamp,
                "start_date_time": False,
                "due_date": expected_timestamp,
                "due_date_time": False,
            },
        )

    def test_board_task_payload_includes_today_dates(self) -> None:
        expected_dates = {
            "start_date": 123,
            "start_date_time": False,
            "due_date": 123,
            "due_date_time": False,
        }
        client = MagicMock()
        client.create_task.return_value = {"url": "https://app.clickup.com/t/task"}
        claude_client = MagicMock()
        claude_client.format_bug_description.return_value = "Details"
        worker = CreateTaskWorker(
            "token",
            "456",
            "Bug",
            "Details",
            mode="board",
            assignee_id=42,
            claude_api_key="claude-key",
        )

        with (
            patch("app.ClickUpClient", return_value=client),
            patch("app.ClaudeClient", return_value=claude_client),
            patch("app.task_dates_for_today", return_value=expected_dates),
        ):
            worker.run()

        client.create_task.assert_called_once_with(
            "456",
            {
                "name": "Bug",
                "markdown_content": "Details",
                "tags": ["bug"],
                "status": "to do",
                "assignees": [42],
                **expected_dates,
            },
        )

    def test_subtask_payload_includes_today_dates(self) -> None:
        expected_dates = {
            "start_date": 123,
            "start_date_time": False,
            "due_date": 123,
            "due_date_time": False,
        }
        client = MagicMock()
        client.get_task.return_value = {"id": "parent", "list": {"id": "789"}}
        client.create_task.return_value = {"url": "https://app.clickup.com/t/task"}
        claude_client = MagicMock()
        claude_client.format_bug_description.return_value = "Details"
        worker = CreateTaskWorker(
            "token",
            "parent",
            "Bug",
            "Details",
            mode="subtask",
            claude_api_key="claude-key",
        )

        with (
            patch("app.ClickUpClient", return_value=client),
            patch("app.ClaudeClient", return_value=claude_client),
            patch("app.task_dates_for_today", return_value=expected_dates),
        ):
            worker.run()

        client.create_task.assert_called_once_with(
            "789",
            {
                "name": "Bug",
                "markdown_content": "Details",
                "tags": ["bug"],
                "status": "to do",
                "parent": "parent",
                **expected_dates,
            },
        )

    def test_success_clears_title_description_and_attachments(self) -> None:
        window = SimpleNamespace(
            create_button=MagicMock(),
            title_input=MagicMock(),
            description_input=MagicMock(),
            attachments=["screenshot.png"],
            _set_status=MagicMock(),
            _show_toast=MagicMock(),
        )

        BugReporterWindow._task_created(window, "https://app.clickup.com/t/task")

        window.title_input.clear.assert_called_once_with()
        window.description_input.clear.assert_called_once_with()
        self.assertEqual(window.attachments, [])

    def test_missing_todo_status_retries_with_list_default(self) -> None:
        client = MagicMock()
        client.create_task.side_effect = [
            ClickUpError("Status not found", 400),
            {"id": "task"},
        ]
        payload = {
            "name": "Bug",
            "status": "to do",
            "due_date": 123,
        }

        task = create_task_with_status_fallback(client, "456", payload)

        self.assertEqual(task, {"id": "task"})
        self.assertEqual(
            client.create_task.call_args_list,
            [
                call("456", payload),
                call("456", {"name": "Bug", "due_date": 123}),
            ],
        )
        self.assertEqual(payload["status"], "to do")

    def test_workspace_members_are_normalized_for_the_picker(self) -> None:
        members = extract_workspace_members(
            [
                {
                    "name": "Engineering",
                    "members": [
                        {
                            "user": {
                                "id": 42,
                                "username": "Ada",
                                "email": "ada@example.com",
                            }
                        },
                        {"user": {"id": "7", "email": "grace@example.com"}},
                    ],
                }
            ]
        )

        self.assertEqual(
            members,
            [
                {"id": 42, "label": "Ada (ada@example.com)"},
                {"id": 7, "label": "grace@example.com"},
            ],
        )

    def test_assignee_search_requires_an_exact_suggestion(self) -> None:
        assignees = {"ada (ada@example.com)": 42}

        self.assertEqual(
            selected_assignee_id("Ada (ada@example.com)", assignees),
            42,
        )
        self.assertIsNone(selected_assignee_id("  ", assignees))

        with self.assertRaisesRegex(ValueError, "search suggestions"):
            selected_assignee_id("Ada", assignees)

    def test_screenshots_survive_ai_description_formatting(self) -> None:
        source, screenshots = extract_screenshot_placeholders(
            "Broken button\n\n![Screenshot 1](data:image/png;base64,abc123)"
        )

        self.assertEqual(source, "Broken button\n\n[[SCREENSHOT_1]]")
        restored = restore_screenshot_markdown(
            "**URLs / Location:**\n\n- Checkout\n\n"
            "**Requirements:**\n\n1. Fix button\n\n[[SCREENSHOT_1]]",
            screenshots,
        )
        self.assertIn("![Screenshot 1](data:image/png;base64,abc123)", restored)
        self.assertNotIn("[[SCREENSHOT_1]]", restored)


if __name__ == "__main__":
    unittest.main()
