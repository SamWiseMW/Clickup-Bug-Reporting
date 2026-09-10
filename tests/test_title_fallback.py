import unittest
from unittest.mock import MagicMock, patch

from app import CreateTaskWorker
from claude_client import ClaudeClient, extract_explicit_page_name, title_from_formatted_description


SOURCE = (
    "Whats the point of having an individual component just for a picure when we can add "
    "images to the rich text editor itself? We can potentially remove it\n\n"
    "This is for the Stories and Events pages"
)
REQUIREMENT = "Consider removing the separate picture component; the rich text editor supports images."
FORMATTED = (
    "**URLs / Location:**\n\n- Stories And Events\n\n"
    f"**Requirements:**\n\n1. {REQUIREMENT}\n\n[[SCREENSHOT_1]]"
)


class TitleFallbackTests(unittest.TestCase):
    def test_plural_pages_preserve_named_locations(self):
        self.assertEqual(extract_explicit_page_name(SOURCE), "Stories And Events")
        client = ClaudeClient("test-key")
        with patch.object(client, "_request_text", return_value="Consider removing picture component"):
            self.assertEqual(
                client.generate_bug_title(SOURCE),
                "Stories And Events | Consider removing picture component",
            )
        with patch.object(client, "_request_text", return_value=FORMATTED):
            self.assertIn("- Stories And Events", client.format_bug_description(SOURCE))

    def test_blank_ai_title_uses_requirement_and_preserves_screenshot(self):
        screenshot = "![Screenshot 1](data:image/png;base64,example)"
        for mode in ("board", "subtask"):
            with self.subTest(mode=mode):
                clickup = MagicMock()
                clickup.get_task.return_value = {"id": "parent", "list": {"id": "456"}}
                clickup.get_list_custom_fields.return_value = []
                clickup.create_task.return_value = {"url": "task-url"}
                worker = CreateTaskWorker(
                    "token", "456", "", f"{screenshot}\n{SOURCE}", mode=mode,
                    claude_api_key="test-key", title_source=SOURCE,
                )
                failures = []
                worker.failed.connect(failures.append)
                with (
                    patch("app.ClickUpClient", return_value=clickup),
                    patch.object(ClaudeClient, "_request_text", side_effect=[FORMATTED, "[[BLANK]]"]),
                ):
                    worker.run()
                self.assertEqual(failures, [])
                clickup.create_task.assert_called_once()
                payload = clickup.create_task.call_args.args[1]
                self.assertEqual(payload["name"], f"Stories And Events | {REQUIREMENT}")
                self.assertEqual(payload["markdown_content"].count(screenshot), 1)
                self.assertNotIn("[[SCREENSHOT_1]]", payload["markdown_content"])

    def test_page_url_and_global_scope_are_preserved_in_fallback(self):
        self.assertEqual(
            title_from_formatted_description(FORMATTED, SOURCE, page_url="https://example.com/stories"),
            f"Stories | {REQUIREMENT}",
        )
        self.assertEqual(
            title_from_formatted_description(FORMATTED, "Global: " + SOURCE, page_url="https://example.com/stories"),
            f"Global | {REQUIREMENT}",
        )

    def test_missing_requirements_do_not_generate_a_title(self):
        for formatted in (
            "", "**URLs / Location:**\n\n- Stories\n\n**Requirements:**",
            "**Requirements:**\n\n1. [[SCREENSHOT_1]]",
        ):
            with self.subTest(formatted=formatted):
                self.assertEqual(title_from_formatted_description(formatted, SOURCE), "")

    def test_fallback_cleans_markdown_and_limits_long_titles(self):
        formatted = "**Requirements:**\n\n1. Consider **removing** the [picture component](https://example.com/) [[SCREENSHOT_1]]"
        self.assertEqual(
            title_from_formatted_description(formatted, ""),
            "Consider removing the picture component",
        )
        title = title_from_formatted_description("**Requirements:**\n\n1. " + "Long requirement " * 30, "")
        self.assertLessEqual(len(title), 160)
        self.assertTrue(title.endswith("..."))


if __name__ == "__main__":
    unittest.main()
