import unittest
from unittest.mock import MagicMock, patch

from app import CreateTaskWorker
from claude_client import (
    CLAUDE_MODEL,
    ClaudeClient,
    ClaudeError,
    empty_url_section_without_link,
    extract_explicit_page_name,
    normalize_generated_issue,
    normalize_generated_title,
    normalize_url_location_section,
    normalize_structured_description,
)


class ClaudeTitleTests(unittest.TestCase):
    def test_title_generation_uses_the_low_cost_haiku_model(self) -> None:
        self.assertEqual(CLAUDE_MODEL, "claude-haiku-4-5-20251001")

    def test_claude_requests_explicitly_disable_thinking(self) -> None:
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "content": [{"type": "text", "text": "response"}]
        }
        client = ClaudeClient("key")

        with patch("claude_client.requests.post", return_value=response) as post:
            client._request_text("prompt", max_tokens=100)

        request_payload = post.call_args.kwargs["json"]
        self.assertEqual(request_payload["thinking"], {"type": "disabled"})

    def test_claude_prompts_require_british_english(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value="Checkout button fails",
        ) as request:
            client.generate_bug_title("Checkout button fails")

        self.assertIn("Use British English", request.call_args.args[0])

        with patch.object(
            client,
            "_request_text",
            return_value=(
                "**URLs / Location:**\n\n"
                "**Requirements:**\n\n1. Correct the button behaviour"
            ),
        ) as request:
            client.format_bug_description("Correct the button behaviour")

        self.assertIn("Use British English", request.call_args.args[0])

    def test_generated_title_is_normalized_to_required_structure(self) -> None:
        self.assertEqual(
            normalize_generated_title('Title: "[Checkout] | [Submit button fails]"'),
            "Checkout | Submit button fails",
        )

    def test_generated_title_without_separator_is_rejected(self) -> None:
        with self.assertRaisesRegex(ClaudeError, "required"):
            normalize_generated_title("Checkout submit button fails")

    def test_generated_title_allows_empty_page_when_no_link_exists(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value="Unspecified Component | Padding needs adjustment",
        ):
            title = client.generate_bug_title("Padding needs to be 20px")

        self.assertEqual(title, "Padding needs adjustment")

    def test_no_link_title_has_no_empty_separator(self) -> None:
        self.assertEqual(
            normalize_generated_issue(" | Padding needs adjustment to 20px"),
            "Padding needs adjustment to 20px",
        )

    def test_unclear_title_response_becomes_blank(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value=(
                "I don't see a bug description provided. Please provide the actual "
                "bug description between the <description> tags."
            ),
        ):
            title = client.generate_bug_title("xxx")

        self.assertEqual(title, "")

    def test_unclear_description_response_becomes_blank(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value=(
                "I'm ready to help reorganize the bug report, but I don't see the "
                "actual bug content. Please provide more information.\n\n"
                "**URLs / Location:**\n\n**Requirements:**"
            ),
        ):
            description = client.format_bug_description("xxx")

        self.assertEqual(description, "")

    def test_generated_title_keeps_page_when_link_exists(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value="Settings | Padding needs adjustment",
        ):
            title = client.generate_bug_title(
                "Padding needs adjustment at https://example.com/settings"
            )

        self.assertEqual(title, "Settings | Padding needs adjustment")

    def test_explicit_page_name_is_used_without_a_link(self) -> None:
        client = ClaudeClient("key")

        with patch.object(
            client,
            "_request_text",
            return_value="About Us Page | Padding needs to be 20px",
        ):
            title = client.generate_bug_title(
                "Padding needs to be 20px on about us page"
            )

        self.assertEqual(title, "About Us | Padding needs to be 20px")
        self.assertEqual(
            extract_explicit_page_name("Button is broken in the checkout screen"),
            "Checkout",
        )

    def test_page_type_is_removed_from_generated_link_title(self) -> None:
        self.assertEqual(
            normalize_generated_title("About Us Page | Padding is incorrect"),
            "About Us | Padding is incorrect",
        )

    def test_structured_description_accepts_required_sections(self) -> None:
        description = normalize_structured_description(
            "```markdown\n**URLs / Location:**\n\n- Checkout\n\n"
            "**Requirements:**\n\n1. Fix submit button\n```"
        )

        self.assertTrue(description.startswith("**URLs / Location:**"))
        self.assertIn("**Requirements:**", description)

    def test_url_section_is_empty_when_source_has_no_link(self) -> None:
        formatted = (
            "**URLs / Location:**\n\n- Not provided\n\n"
            "**Requirements:**\n\n1. Padding needs to be 20px"
        )

        cleaned = empty_url_section_without_link(
            formatted,
            "Padding needs to be 20px",
        )

        self.assertEqual(
            cleaned,
            "**URLs / Location:**\n\n"
            "**Requirements:**\n\n1. Padding needs to be 20px",
        )

    def test_url_section_is_preserved_when_source_has_a_link(self) -> None:
        formatted = (
            "**URLs / Location:**\n\n- https://example.com/settings\n\n"
            "**Requirements:**\n\n1. Fix padding"
        )

        self.assertEqual(
            empty_url_section_without_link(
                formatted,
                "Fix padding on https://example.com/settings",
            ),
            formatted,
        )

    def test_named_page_populates_location_without_a_link(self) -> None:
        formatted = (
            "**URLs / Location:**\n\n- Not provided\n\n"
            "**Requirements:**\n\n1. Fix padding"
        )

        cleaned = normalize_url_location_section(
            formatted,
            "Padding needs to be 20px on about us page",
        )

        self.assertEqual(
            cleaned,
            "**URLs / Location:**\n\n- About Us\n\n"
            "**Requirements:**\n\n1. Fix padding",
        )

    def test_blank_title_is_generated_before_creating_task(self) -> None:
        dates = {
            "start_date": 123,
            "start_date_time": False,
            "due_date": 123,
            "due_date_time": False,
        }
        clickup_client = MagicMock()
        clickup_client.create_task.return_value = {"url": "https://app.clickup.com/t/task"}
        claude_client = MagicMock()
        claude_client.format_bug_description.return_value = (
            "**URLs / Location:**\n\n- Checkout\n\n"
            "**Requirements:**\n\n1. Submit button fails"
        )
        claude_client.generate_bug_title.return_value = "Checkout | Submit button fails"
        worker = CreateTaskWorker(
            "clickup-token",
            "456",
            "",
            "Detailed Markdown description",
            mode="board",
            claude_api_key="claude-key",
            title_source="Submit fails on the Checkout page",
        )

        with (
            patch("app.ClickUpClient", return_value=clickup_client),
            patch("app.ClaudeClient", return_value=claude_client),
            patch("app.task_dates_for_today", return_value=dates),
        ):
            worker.run()

        claude_client.generate_bug_title.assert_called_once_with(
            "Submit fails on the Checkout page"
        )
        clickup_client.create_task.assert_called_once_with(
            "456",
            {
                "name": "Checkout | Submit button fails",
                "markdown_content": (
                    "**URLs / Location:**\n\n- Checkout\n\n"
                    "**Requirements:**\n\n1. Submit button fails"
                ),
                "tags": ["bug"],
                "status": "to do",
                **dates,
            },
        )

    def test_unclear_description_is_not_sent_to_clickup(self) -> None:
        clickup_client = MagicMock()
        claude_client = MagicMock()
        claude_client.format_bug_description.return_value = ""
        worker = CreateTaskWorker(
            "clickup-token",
            "456",
            "",
            "xxx",
            mode="board",
            claude_api_key="claude-key",
            title_source="xxx",
        )

        with (
            patch("app.ClickUpClient", return_value=clickup_client),
            patch("app.ClaudeClient", return_value=claude_client),
        ):
            worker.run()

        claude_client.generate_bug_title.assert_not_called()
        clickup_client.create_task.assert_not_called()


if __name__ == "__main__":
    unittest.main()
