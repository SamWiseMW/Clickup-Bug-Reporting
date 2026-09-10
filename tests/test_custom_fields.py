import unittest
from unittest.mock import MagicMock, patch

from app import CreateTaskWorker, resolve_default_custom_fields
from clickup_client import ClickUpClient, ClickUpError


def field_definitions():
    return [
        {"id": field_id, "name": name, "type": "drop_down",
         "type_config": {"options": [
             {"id": "other", "name": "Other", "orderindex": 0},
             {"id": option_id, "name": option, "orderindex": 1},
         ]}}
        for field_id, name, option_id, option in (
            ("owner", "🧠 Process Owner", "developer-id", "Developer"),
            ("hours", "Type of hours (ACCESS)", "tech-id", "ACCESS Tech"),
            ("category", "Dev category", "development-id", "Development"),
        )
    ]


EXPECTED = [
    {"id": "owner", "value": "developer-id"},
    {"id": "hours", "value": "tech-id"},
    {"id": "category", "value": "development-id"},
]


class CustomFieldTests(unittest.TestCase):
    def test_fields_use_option_ids_and_accept_decorative_emoji(self):
        self.assertEqual(resolve_default_custom_fields(field_definitions()), EXPECTED)

    def test_missing_ambiguous_or_inapplicable_fields_are_skipped(self):
        for problem in ("missing_field", "missing_option", "duplicate", "scoped", "type"):
            fields = field_definitions()
            if problem == "missing_field":
                fields.pop()
            elif problem == "missing_option":
                fields[0]["type_config"]["options"] = []
            elif problem == "duplicate":
                fields.append(fields[0])
            elif problem == "scoped":
                fields[0]["applied_objects"] = [{"object_type": 19, "object_id": 99}]
            else:
                fields[0]["type"] = "text"
            with self.subTest(problem=problem):
                expected = EXPECTED[:2] if problem == "missing_field" else EXPECTED[1:]
                self.assertEqual(resolve_default_custom_fields(fields), expected)

    def test_tasks_and_subtasks_include_fields_even_after_status_fallback(self):
        for mode in ("board", "subtask"):
            with self.subTest(mode=mode):
                client = MagicMock()
                client.get_task.return_value = {"id": "parent", "list": {"id": "456"}}
                client.get_list_custom_fields.return_value = field_definitions()
                client.create_task.side_effect = [
                    ClickUpError("Status not found", 400), {"url": "task-url"}
                ]
                claude = MagicMock()
                claude.format_bug_description.return_value = "Fix padding"
                worker = CreateTaskWorker("token", "456", "Fix padding", "Fix padding", mode=mode)
                with patch("app.ClickUpClient", return_value=client), patch("app.ClaudeClient", return_value=claude):
                    worker.run()
                client.get_list_custom_fields.assert_called_once_with("456")
                self.assertEqual(client.create_task.call_count, 2)
                for request in client.create_task.call_args_list:
                    self.assertEqual(request.args[1]["custom_fields"], EXPECTED)

    def test_missing_fields_allow_task_creation(self):
        client = MagicMock()
        client.create_task.return_value = {"url": "task-url"}
        client.get_list_custom_fields.return_value = []
        claude = MagicMock()
        claude.format_bug_description.return_value = "Fix padding"
        worker = CreateTaskWorker("token", "456", "Fix padding", "Fix padding", mode="board")
        errors = []
        worker.failed.connect(errors.append)
        with patch("app.ClickUpClient", return_value=client), patch("app.ClaudeClient", return_value=claude):
            worker.run()
        client.create_task.assert_called_once()
        self.assertEqual(client.create_task.call_args.args[1]["custom_fields"], [])
        self.assertEqual(errors, [])

    def test_list_field_request_includes_task_type_scoping(self):
        client = ClickUpClient(lambda: "token")
        with patch.object(client, "_request", return_value={"fields": field_definitions()}) as request:
            self.assertEqual(client.get_list_custom_fields("456"), field_definitions())
        request.assert_called_once_with(
            "GET", "/list/456/field", params={"include_applied_objects": "true"}
        )
