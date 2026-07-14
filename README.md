# ClickUp Bug Reporting

A desktop app for creating ClickUp bug report tasks.

## Sprint 1

One-page PySide6 desktop app with:

- ClickUp API token loaded from `.env`.
- Board/List reporting mode.
- Parent task subtask reporting mode.
- Task title field.
- Combined description and screenshot field.
- ClickUp task creation.
- Pasted screenshots embedded into the task description.

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Add `CLICKUP_API_TOKEN` to `.env`. The app does not show a token field.

## Run

```bash
python app.py
```

This opens the desktop app window.

## ClickUp Notes

The app uses ClickUp API v2:

- `POST /list/{list_id}/task` to create the bug task.
- `GET /task/{task_id}` to resolve a parent task before creating a subtask.

The Board/List link must contain the ClickUp List ID. The app can also accept the List ID directly.

The Subtask tab accepts a ClickUp parent task URL like `https://app.clickup.com/t/{workspace_id}/{task_id}` or a task ID directly. The app resolves the parent task's List and creates the new task with ClickUp's `parent` field.

Screenshots can be pasted directly into the description field. They appear inline in the app and are embedded into the created ClickUp task or subtask through the Markdown description, not uploaded as separate task attachments.

