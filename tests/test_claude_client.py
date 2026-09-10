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

    def test_root_url_uses_home_instead_of_company_name(self) -> None:
        client = ClaudeClient("key")
        for link in (
            "https://galambila-stg.mw-aws.com/",
            "https://galambila-stg.mw-aws.com",
            "https://galambila-stg.mw-aws.com/?utm_source=test#hero",
            "www.example.com/",
        ):
            with self.subTest(link=link), patch.object(
                client, "_request_text",
                return_value="Galambila | UI not responsive on smaller screens",
            ):
                self.assertEqual(
                    client.generate_bug_title(
                        f"UI isn't responsive on smaller screens\n{link}"
                    ),
                    "Home | UI not responsive on smaller screens",
                )

    def test_global_scope_overrides_page_prefix(self) -> None:
        client = ClaudeClient("key")
        for source in (
            "Global: carousels cannot be dragged",
            "Carousels cannot be dragged on ALL PAGES",
            "Global carousel issue on about us page https://example.com/",
        ):
            with self.subTest(source=source), patch.object(
                client, "_request_text", return_value="Home | Carousels cannot be dragged"
            ):
                self.assertEqual(
                    client.generate_bug_title(source),
                    "Global | Carousels cannot be dragged",
                )

    def test_global_location_is_labelled_and_links_are_preserved(self) -> None:
        formatted = (
            "**URLs / Location:**\n\n- Home\n\n"
            "**Requirements:**\n\n1. Fix carousels\n\n[[SCREENSHOT_1]]"
        )
        for source, location in (
            ("Global: fix carousels", "- Global"),
            ("Fix carousels on all pages", "- Global"),
            ("Global: fix carousels https://example.com/", "- [Global](https://example.com/)"),
        ):
            with self.subTest(source=source):
                self.assertEqual(
                    normalize_url_location_section(formatted, source),
                    f"**URLs / Location:**\n\n{location}\n\n"
                    "**Requirements:**\n\n1. Fix carousels\n\n[[SCREENSHOT_1]]",
                )

    def test_global_in_url_alone_does_not_imply_global_scope(self) -> None:
        client = ClaudeClient("key")
        with patch.object(client, "_request_text", return_value="Settings | Fix padding"):
            self.assertEqual(
                client.generate_bug_title("Fix padding at https://global.example.com/settings"),
                "Settings | Fix padding",
            )

    def test_root_url_does_not_override_explicit_location(self) -> None:
        client = ClaudeClient("key")
        with patch.object(client, "_request_text", return_value="Fix padding"):
            self.assertEqual(
                client.generate_bug_title(
                    "Fix padding on about us page at https://example.com/"
                ),
                "About Us | Fix padding",
            )

    def test_mixed_urls_and_hash_routes_are_not_forced_to_home(self) -> None:
        client = ClaudeClient("key")
        for links in (
            "https://example.com/ https://example.com/settings",
            "https://example.com/#/settings",
        ):
            with self.subTest(links=links), patch.object(
                client, "_request_text", return_value="Settings | Fix padding"
            ):
                self.assertEqual(
                    client.generate_bug_title(f"Fix padding: {links}"),
                    "Settings | Fix padding",
                )

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

    def test_home_device_shorthand_sets_title_and_location(self) -> None:
        client = ClaudeClient("key")
        for location in ("home mobile", "HOME MOBILE", "home mobile page", "mobile home",
                         "homepage", "home page", "home desktop", "home tablet"):
            source = f"Missing arrow on {location}"
            with self.subTest(location=location), patch.object(
                client, "_request_text", return_value="Missing arrow on mobile"
            ):
                self.assertEqual(
                    client.generate_bug_title(source), "Home | Missing arrow on mobile"
                )
                formatted = normalize_url_location_section(
                    "**URLs / Location:**\n\n**Requirements:**\n\n"
                    f"1. {source}\n\n[[SCREENSHOT_1]]",
                    source,
                )
                self.assertIn("**URLs / Location:**\n\n- Home\n\n", formatted)
                self.assertIn(f"1. {source}", formatted)
                self.assertIn("[[SCREENSHOT_1]]", formatted)

    def test_home_in_unrelated_text_is_not_a_location(self) -> None:
        for source in ("Home button is missing", "Work from home", "https://example.com/home-mobile"):
            with self.subTest(source=source):
                self.assertEqual(extract_explicit_page_name(source), "")

    def test_structured_description_accepts_required_sections(self) -> None:
        description = normalize_structured_description(
            "```markdown\n**URLs / Location:**\n\n- Checkout\n\n"
            "**Requirements:**\n\n1. Fix submit button\n```"
        )

        self.assertTrue(description.startswith("**URLs / Location:**"))
        self.assertIn("**Requirements:**", description)

    def test_omitted_video_link_is_restored_exactly(self) -> None:
        client = ClaudeClient("key")
        url = "https://vimeo.com/123456789/abcdef?h=privatehash&share=copy"
        with patch.object(client, "_request_text", return_value=(
            "**URLs / Location:**\n\n- Home\n\n"
            "**Requirements:**\n\n1. Fix the black pixel.\n\n[[SCREENSHOT_1]]"
        )):
            formatted = client.format_bug_description(
                f"Black pixel on home page image hover. Recording: {url}"
            )
        from app import restore_screenshot_markdown

        result = restore_screenshot_markdown(formatted, {})
        self.assertIn(url, result)
        self.assertNotIn("[[SCREENSHOT_1]]", result)

    def test_retained_video_link_is_not_duplicated(self) -> None:
        client = ClaudeClient("key")
        url = "https://vimeo.com/123456789"
        with patch.object(client, "_request_text", return_value=(
            "**URLs / Location:**\n\n- Home\n\n"
            f"**Requirements:**\n\n1. Fix hover.\n\n[Recording]({url})"
        )):
            formatted = client.format_bug_description(f"Fix hover. {url}")
        self.assertEqual(formatted.count(url), 1)

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
            patch("app.resolve_default_custom_fields", return_value=[]),
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
                "custom_fields": [],
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
