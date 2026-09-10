import unittest
from unittest.mock import MagicMock

from app import extract_list_id, resolve_board_list_id


class BoardUrlTests(unittest.TestCase):
    def test_board_url_resolves_to_its_parent_list(self) -> None:
        target = extract_list_id(
            "https://app.clickup.com/9003001220/v/b/8c9xtc4-120016"
        )
        self.assertEqual(target, "8c9xtc4-120016")
        client = MagicMock()
        client.get_view.return_value = {
            "view": {"parent": {"id": "456", "type": 6}}
        }

        self.assertEqual(resolve_board_list_id(client, target), "456")
        client.get_view.assert_called_once_with("8c9xtc4-120016")

    def test_board_and_list_urls_preserve_only_the_view_id(self) -> None:
        for view_type in ("b", "l"):
            with self.subTest(view_type=view_type):
                self.assertEqual(
                    extract_list_id(
                        f"https://app.clickup.com/9003001220/v/{view_type}/"
                        "8c9xtc4-120016?filter=active#tasks"
                    ),
                    "8c9xtc4-120016",
                )
        self.assertEqual(extract_list_id("456"), "456")
        self.assertEqual(extract_list_id("https://app.clickup.com/t/abc123"), "")
