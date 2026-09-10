from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlsplit

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

    def generate_bug_title(self, description: str, *, page_url: str = "") -> str:
        page_url = normalise_page_url(page_url)
        has_link = bool(page_url) or contains_web_link(description)
        global_scope = has_global_scope(description)
        explicit_page_name = extract_explicit_page_name(description)
        known_page_name = (
            "Global" if global_scope
            else page_name_from_url(page_url) if page_url
            else explicit_page_name or home_page_name_from_links(description)
        )
        has_page_context = has_link or bool(known_page_name)
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
                "Identify the page name from the URL path, not the website or company name. "
                "A root URL (no path or just '/') means 'Home'."
                if has_link
                else "Return only the issue description, with no page name or pipe separator."
            )
        )
        if global_scope:
            link_rule = "Use exactly 'Global' before the pipe: the issue applies across pages."
        elif page_url:
            link_rule = (
                f"Use '{known_page_name}' before the pipe, as identified by the "
                "dedicated Page URL. Other links in the report are supporting evidence."
            )
        prompt = f"""Create a concise task title from the report below.

Return exactly one line in this format:
{required_format}

Rules:
- {link_rule}
- Keep the issue description to a few words.
- 'Home mobile', 'mobile home', 'homepage', and 'home page' identify the Home
  page. Keep mobile/desktop/tablet as device context in the issue, not the page name.
- Use British English spelling and wording throughout.
- Do not include quotes, brackets, labels, bullets, or extra explanation.
- Accept bugs, requested changes, suggestions, and questions proposing an improvement.
  A report does not need to describe broken behaviour or include reproduction steps.
- Preserve uncertainty: 'potentially remove' means 'Consider removing', not 'Remove'.
- If there is no identifiable issue, request, or suggestion, return exactly {BLANK_OUTPUT_MARKER}.
- Never ask the user for more information or explain what is missing.

Bug description:
<description>
{description}
</description>"""
        if page_url:
            prompt += f"\n\nDedicated Page URL:\n{page_url}"

        generated = self._request_text(prompt, max_tokens=100)
        if should_leave_blank(generated):
            return ""

        if known_page_name:
            # The source description is authoritative. Claude may add the location
            # type (for example, "About Us Page"), so only use its issue text.
            issue = normalize_generated_issue(generated)
            return f"{known_page_name} | {issue}"

        if has_link:
            if "|" in generated:
                return normalize_generated_title(generated)

            raise ClaudeError(
                "Claude could not generate a title in the required 'Page name | issue' format."
            )

        return normalize_generated_issue(generated)

    def format_bug_description(self, description: str, *, page_url: str = "") -> str:
        page_url = normalise_page_url(page_url)
        prompt = f"""Reorganise the bug report below into exactly this Markdown structure:

**URLs / Location:**

- <page URL, page name, or location>

**Requirements:**

1. <clear, concise requirement or issue>

Rules:
- Return only the formatted Markdown.
- Use British English spelling and wording throughout.
- Preserve every factual detail supplied by the user.
- Treat 'home mobile', 'mobile home', 'homepage', and 'home page' as the Home
  location. Preserve any mobile/desktop/tablet context in the requirements.
- Preserve every supplied URL exactly, including Vimeo/video links and their query
  parameters. Video links are evidence, not screenshots; never replace them with
  screenshot placeholders or image Markdown.
- Do not invent URLs, behaviour, steps, or technical details.
- If the user says 'global' or 'all pages', use 'Global' under URLs / Location.
  If links are supplied, retain their destinations with 'Global' as the link text.
- Otherwise, if a web link is supplied, list it under URLs / Location.
- If no link is supplied but a page, screen, modal, dialog, or feature is explicitly
  named, list that name under URLs / Location without inventing a URL.
- If neither a link nor a named location is supplied, leave URLs / Location empty.
  Do not write a placeholder such as "Not provided".
- Split distinct issues or requested changes into numbered requirements.
- Rewrite rough notes as clear requirements without changing their meaning.
- Accept bugs, requested changes, suggestions, and questions proposing an improvement.
  A report does not need to describe broken behaviour or include reproduction steps.
- Preserve uncertainty: 'potentially remove' means 'Consider removing', not 'Remove'.
- Preserve tokens such as [[SCREENSHOT_1]] exactly once and place each immediately
  below the requirement it supports.
- Only include screenshot tokens that occur in the raw report. Never invent them.
- If there is no identifiable issue, request, or suggestion, return exactly
  {BLANK_OUTPUT_MARKER}.
- Never ask the user for more information or explain what is missing.

Raw bug report:
<description>
{description}
</description>"""
        if page_url:
            prompt += (
                f"\n\nDedicated Page URL:\n{page_url}\n"
                "Use this URL under URLs / Location. Retain other supplied links "
                "as evidence with the relevant requirements."
            )

        generated = self._request_text(prompt, max_tokens=700)
        if should_leave_blank(generated):
            return ""

        formatted = normalize_structured_description(generated)
        formatted = normalize_url_location_section(formatted, description, page_url=page_url)
        return preserve_source_links(formatted, description)

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


def title_from_formatted_description(
    formatted: str, source: str, *, page_url: str = ""
) -> str:
    """Reuse an accepted requirement when the title model returns blank."""
    heading = re.search(r"\*{0,2}Requirements:\*{0,2}", formatted, re.IGNORECASE)
    if not heading:
        return ""
    for line in formatted[heading.end():].splitlines():
        requirement = re.match(r"\s*(?:\d+[.)]|[-*])\s+(.+)", line)
        if not requirement:
            continue
        issue = re.sub(r"\[\[SCREENSHOT_\d+\]\]", "", requirement[1], flags=re.IGNORECASE)
        issue = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", issue)
        issue = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", issue)
        issue = " ".join(issue.replace("**", "").replace("`", "").split())
        if not issue or should_leave_blank(issue):
            continue
        page_url = normalise_page_url(page_url)
        page_name = (
            "Global" if has_global_scope(source)
            else page_name_from_url(page_url) if page_url
            else extract_explicit_page_name(source) or home_page_name_from_links(source)
        )
        title = f"{page_name} | {issue}" if page_name else issue
        if len(title) > 160:
            title = title[:157].rsplit(" ", 1)[0].rstrip(".,;:") + "..."
        return title
    return ""


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


def normalize_url_location_section(
    formatted: str, source: str, *, page_url: str = ""
) -> str:
    global_scope = has_global_scope(source)
    if contains_web_link(source) and not global_scope and not page_url:
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
    if global_scope:
        links = list(dict.fromkeys(WEB_LINK_PATTERN.findall(source)))
        location = (
            "\n".join(f"- [Global]({link})" for link in links) + "\n\n"
            if links else "- Global\n\n"
        )
    if page_url:
        location = (
            f"- [Global]({page_url})\n\n" if global_scope
            else f"- {page_url}\n\n"
        )
    return (
        f"{formatted[:location_heading.end()].rstrip()}\n\n{location}"
        f"{formatted[requirements_heading.start():].lstrip()}"
    )


def empty_url_section_without_link(formatted: str, source: str) -> str:
    return normalize_url_location_section(formatted, source)


def preserve_source_links(formatted: str, source: str) -> str:
    """Restore source URLs omitted or rewritten by the formatter."""
    def links(value: str) -> list[str]:
        return list(dict.fromkeys(
            match.rstrip(".,;!]>\"'") for match in WEB_LINK_PATTERN.findall(value)
        ))

    present = set(links(formatted))
    missing = [link for link in links(source) if link not in present]
    if missing:
        formatted = formatted.rstrip() + "\n\n" + "\n".join(
            f"- {link}" for link in missing
        )
    return formatted


def normalise_page_url(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value.lower().startswith("www."):
        value = "https://" + value
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter a valid Page URL starting with https://, or leave it blank.")
    return value


def page_name_from_url(value: str) -> str:
    parsed = urlsplit(value)
    path = parsed.path
    if parsed.fragment.startswith(("/", "!/")):
        path = parsed.fragment.lstrip("!").split("?", 1)[0]
    slug = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    if not slug:
        return "Home"
    slug = re.sub(r"\.(?:html?|php|aspx?)$", "", slug, flags=re.IGNORECASE)
    return clean_page_name(re.sub(r"[-_]+", " ", slug).strip().title()) or "Home"


def contains_web_link(value: str) -> bool:
    return WEB_LINK_PATTERN.search(value) is not None


def has_global_scope(value: str) -> bool:
    # A domain or URL path containing 'global' does not imply site-wide scope.
    prose = WEB_LINK_PATTERN.sub(" ", value)
    return re.search(r"\b(?:global|all\s+pages)\b", prose, re.IGNORECASE) is not None


def home_page_name_from_links(value: str) -> str:
    links = WEB_LINK_PATTERN.findall(value)
    if not links:
        return ""

    # Only force Home when every supplied link identifies the same root page.
    # Mixed pages and hash-based routes need the model's contextual judgement.
    hosts = set()
    for link in links:
        link = link.rstrip(".,;:!?]>\"'")
        if link.lower().startswith("www."):
            link = "https://" + link
        try:
            parsed = urlsplit(link)
        except ValueError:
            return ""
        if not parsed.hostname or parsed.path not in ("", "/"):
            return ""
        if parsed.fragment.startswith(("/", "!")):
            return ""
        hosts.add(parsed.hostname)

    return "Home" if len(hosts) == 1 else ""


def extract_explicit_page_name(value: str) -> str:
    prose = WEB_LINK_PATTERN.sub(" ", value)
    if re.search(
        r"\b(?:home\s*page|home[\s-]+(?:mobile|desktop|tablet)|"
        r"(?:mobile|desktop|tablet)[\s-]+home)\b",
        prose,
        re.IGNORECASE,
    ):
        return "Home"

    page_type = r"(?:pages?|screen|modal|dialog|feature)"
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
