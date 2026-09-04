from __future__ import annotations

import re
from typing import Any

import requests


CLAUDE_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_API_VERSION = "2023-06-01"
CLAUDE_MODEL = "claude-haiku-4-5-20251001"
BLANK_OUTPUT_MARKER = "[[BLANK]]"
WEB_LINK_PATTERN = re.compile(r"https?://[^\s)]+|www\.[^\s)]+", re.IGNORECASE)


class ClaudeError(Exception):
    pass


class ClaudeClient:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key.strip()

    def generate_bug_title(self, description: str) -> str:
        has_link = contains_web_link(description)
        explicit_page_name = extract_explicit_page_name(description)
        has_page_context = has_link or bool(explicit_page_name)
        required_format = (
            "Page name | brief description of the issue"
            if has_page_context
            else "brief description of the issue"
        )
        link_rule = (
            "Use only the explicitly named location before the pipe. Omit the type "
            "word itself (for example, use 'About Us', not 'About Us Page')."
            if explicit_page_name
            else (
                "Identify the page name from the supplied web link."
                if has_link
                else "Return only the issue description, with no page name or pipe separator."
            )
        )
        prompt = f"""Create a concise bug-report title from the description below.

Return exactly one line in this format:
{required_format}

Rules:
- {link_rule}
- Keep the issue description to a few words.
- Use British English spelling and wording throughout.
- Do not include quotes, brackets, labels, bullets, or extra explanation.
- If the input does not contain a clear issue, return exactly {BLANK_OUTPUT_MARKER}.
- Never ask the user for more information or explain what is missing.

Bug description:
<description>
{description}
</description>"""

        generated = self._request_text(prompt, max_tokens=100)
        if should_leave_blank(generated):
            return ""

        if explicit_page_name:
            # The source description is authoritative. Claude may add the location
            # type (for example, "About Us Page"), so only use its issue text.
            issue = normalize_generated_issue(generated)
            return f"{explicit_page_name} | {issue}"

        if has_link:
            if "|" in generated:
                return normalize_generated_title(generated)

            raise ClaudeError(
                "Claude could not generate a title in the required 'Page name | issue' format."
            )

        return normalize_generated_issue(generated)

    def format_bug_description(self, description: str) -> str:
        prompt = f"""Reorganise the bug report below into exactly this Markdown structure:

**URLs / Location:**

- <page URL, page name, or location>

**Requirements:**

1. <clear, concise requirement or issue>

Rules:
- Return only the formatted Markdown.
- Use British English spelling and wording throughout.
- Preserve every factual detail supplied by the user.
- Do not invent URLs, behaviour, steps, or technical details.
- If a web link is supplied, list it under URLs / Location.
- If no link is supplied but a page, screen, modal, dialog, or feature is explicitly
  named, list that name under URLs / Location without inventing a URL.
- If neither a link nor a named location is supplied, leave URLs / Location empty.
  Do not write a placeholder such as "Not provided".
- Split distinct issues or requested changes into numbered requirements.
- Rewrite rough notes as clear requirements without changing their meaning.
- Preserve tokens such as [[SCREENSHOT_1]] exactly once and place each immediately
  below the requirement it supports.
- If the input does not contain a clear issue or requirement, return exactly
  {BLANK_OUTPUT_MARKER}.
- Never ask the user for more information or explain what is missing.

Raw bug report:
<description>
{description}
</description>"""

        generated = self._request_text(prompt, max_tokens=700)
        if should_leave_blank(generated):
            return ""

        formatted = normalize_structured_description(generated)
        return normalize_url_location_section(formatted, description)

    def _request_text(self, prompt: str, *, max_tokens: int) -> str:
        if not self.api_key:
            raise ClaudeError("Claude API key is not configured.")

        try:
            response = requests.post(
                CLAUDE_MESSAGES_URL,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": CLAUDE_API_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": CLAUDE_MODEL,
                    "max_tokens": max_tokens,
                    "thinking": {"type": "disabled"},
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise ClaudeError("Could not reach the Claude API.") from exc

        data = _parse_json(response)
        if response.status_code >= 400:
            error = data.get("error")
            message = error.get("message") if isinstance(error, dict) else None
            raise ClaudeError(
                message or f"Claude request failed with status {response.status_code}."
            )

        content = data.get("content")
        if not isinstance(content, list):
            raise ClaudeError("Claude returned an unexpected response.")

        text = "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        if not text.strip():
            raise ClaudeError("Claude returned an empty response.")

        return text.strip()


def normalize_generated_title(
    value: str,
    *,
    allow_empty_page: bool = False,
) -> str:
    candidates = [line.strip() for line in value.splitlines() if "|" in line]
    if not candidates:
        raise ClaudeError(
            "Claude could not generate a title in the required 'Page name | issue' format."
        )

    candidate = candidates[0].strip(" `\"'")
    if candidate.casefold().startswith("title:"):
        candidate = candidate[6:].strip()

    parts = candidate.split("|")
    if len(parts) != 2:
        raise ClaudeError(
            "Claude could not generate a title in the required 'Page name | issue' format."
        )

    page_name = clean_page_name(parts[0].strip(" []`\"'"))
    issue = parts[1].strip(" []`\"'")
    if not issue or (not page_name and not allow_empty_page):
        raise ClaudeError(
            "Claude could not generate a title in the required 'Page name | issue' format."
        )

    return f"{page_name} | {issue}"


def should_leave_blank(value: str) -> bool:
    text = value.strip()
    lowered = text.casefold()
    if not text or lowered == BLANK_OUTPUT_MARKER.casefold():
        return True

    refusal_patterns = (
        r"\bi (?:do not|don't|cannot|can't) (?:see|find|identify|create)",
        r"\b(?:no|missing) bug description\b",
        r"\bplease provide (?:the |a |more )?(?:actual )?bug",
        r"\b(?:not enough|insufficient) (?:detail|information)",
        r"\bready to help (?:reorganize|reorganise|organize|organise|create)",
        r"<description>",
    )
    return any(re.search(pattern, lowered) for pattern in refusal_patterns)


def clean_page_name(value: str) -> str:
    return re.sub(
        r"\s+(?:page|screen|modal|dialog|feature)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()


def normalize_generated_issue(value: str) -> str:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        raise ClaudeError("Claude could not generate a bug title.")

    issue = lines[0].strip(" `\"'[]-")
    if issue.casefold().startswith("title:"):
        issue = issue[6:].strip()
    if "|" in issue:
        issue = issue.split("|", 1)[1].strip(" `\"'[]-")
    if not issue:
        raise ClaudeError("Claude could not generate a bug title.")

    return issue


def normalize_structured_description(value: str) -> str:
    text = value.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines.pop()
        text = "\n".join(lines).strip()

    lowered = text.casefold()
    if "urls / location:" not in lowered or "requirements:" not in lowered:
        raise ClaudeError(
            "Claude could not organise the description into the required structure."
        )

    return text


def normalize_url_location_section(formatted: str, source: str) -> str:
    if contains_web_link(source):
        return formatted

    location_heading = re.search(
        r"\*{0,2}URLs / Location:\*{0,2}",
        formatted,
        re.IGNORECASE,
    )
    requirements_heading = re.search(
        r"\*{0,2}Requirements:\*{0,2}",
        formatted,
        re.IGNORECASE,
    )
    if not location_heading or not requirements_heading:
        return formatted
    if location_heading.end() >= requirements_heading.start():
        return formatted

    page_name = extract_explicit_page_name(source)
    location = f"- {page_name}\n\n" if page_name else ""
    return (
        f"{formatted[:location_heading.end()].rstrip()}\n\n{location}"
        f"{formatted[requirements_heading.start():].lstrip()}"
    )


def empty_url_section_without_link(formatted: str, source: str) -> str:
    return normalize_url_location_section(formatted, source)


def contains_web_link(value: str) -> bool:
    return WEB_LINK_PATTERN.search(value) is not None


def extract_explicit_page_name(value: str) -> str:
    page_type = r"(?:page|screen|modal|dialog|feature)"
    patterns = (
        rf"\b(?:on|in|at|from|for)\s+(?:the\s+)?"
        rf"(?P<name>[A-Za-z0-9][A-Za-z0-9 &'/-]{{0,50}}?)\s+{page_type}\b",
        rf"^\s*(?:the\s+)?"
        rf"(?P<name>[A-Za-z0-9][A-Za-z0-9 &'/-]{{0,50}}?)\s+{page_type}\b",
    )

    for pattern in patterns:
        match = re.search(pattern, value, re.IGNORECASE)
        if match:
            return match.group("name").strip().title()

    return ""


def _parse_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ClaudeError("Claude returned an unreadable response.") from exc

    return data if isinstance(data, dict) else {}
