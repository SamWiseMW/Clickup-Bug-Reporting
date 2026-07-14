from __future__ import annotations

from typing import Any, Callable

import requests


CLICKUP_API_BASE = "https://api.clickup.com/api/v2"


class ClickUpError(Exception):
    def __init__(self, message: str, status_code: int = 502, details: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details


class ClickUpClient:
    def __init__(self, token_provider: Callable[[], str]) -> None:
        self._token_provider = token_provider

    def get_workspaces(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/team")
        return data.get("teams", [])

    def get_spaces(self, workspace_id: str) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"/team/{workspace_id}/space",
            params={"archived": "false"},
        )
        return data.get("spaces", [])

    def get_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool = False,
        team_id: str | None = None,
    ) -> dict[str, Any]:
        params = {"include_markdown_description": "true"}

        if custom_task_ids:
            params["custom_task_ids"] = "true"
            if team_id:
                params["team_id"] = str(team_id)

        return self._request("GET", f"/task/{task_id}", params=params)

    def get_lists_for_space(self, space_id: str) -> list[dict[str, Any]]:
        folderless = self._request(
            "GET",
            f"/space/{space_id}/list",
            params={"archived": "false"},
        ).get("lists", [])

        folders = self._request(
            "GET",
            f"/space/{space_id}/folder",
            params={"archived": "false"},
        ).get("folders", [])

        lists: list[dict[str, Any]] = []

        for item in folderless:
            item["folder"] = None
            lists.append(item)

        for folder in folders:
            folder_lists = self._request(
                "GET",
                f"/folder/{folder['id']}/list",
                params={"archived": "false"},
            ).get("lists", [])

            for item in folder_lists:
                item["folder"] = {"id": folder["id"], "name": folder["name"]}
                lists.append(item)

        return lists

    def create_task(self, list_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/list/{list_id}/task", json=payload)

    def get_view(self, view_id: str) -> dict[str, Any]:
        return self._request("GET", f"/view/{view_id}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        token = self._token_provider()

        if not token:
            raise ClickUpError("ClickUp API token is not configured.", 401)

        try:
            response = requests.request(
                method,
                f"{CLICKUP_API_BASE}{path}",
                headers={
                    "Authorization": token,
                    "Content-Type": "application/json",
                },
                params=params,
                json=json,
                timeout=20,
            )
        except requests.RequestException as exc:
            raise ClickUpError("Could not reach ClickUp API.") from exc

        data = _parse_json(response)

        if response.status_code >= 400:
            message = (
                data.get("err")
                or data.get("error")
                or data.get("message")
                or f"ClickUp request failed with status {response.status_code}."
            )
            raise ClickUpError(message, response.status_code, data)

        return data


def _parse_json(response: requests.Response) -> dict[str, Any]:
    if not response.text:
        return {}

    try:
        data = response.json()
    except ValueError:
        return {"raw": response.text}

    return data if isinstance(data, dict) else {"data": data}
