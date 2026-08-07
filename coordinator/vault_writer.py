import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

VAULT_PATH = Path(os.environ.get("VAULT_PATH", "../Vault"))
NOTES_DIR = "1-Notes"
TASKS_DIR = "0-Inbox/Tasks"
FAILED_DIR = "0-Inbox/Failed"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(vault: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=vault,
        capture_output=True,
        text=True,
        timeout=60,
    )


def git_pull(vault: Path = VAULT_PATH):
    result = _git(vault, "pull", "--rebase", "--autostash")
    if result.returncode != 0:
        log.error("git pull failed: %s", result.stderr)


def git_push(vault: Path, message: str):
    _git(vault, "add", "-A")
    result = _git(vault, "commit", "-m", message)
    if result.returncode not in (0, 1):  # 1 = nothing to commit
        log.error("git commit failed: %s", result.stderr)
        return

    push = _git(vault, "push")
    if push.returncode != 0:
        # Try rebase and push again
        _git(vault, "pull", "--rebase", "--autostash")
        _git(vault, "push")


def _format_bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items) if items else ""


def _format_chapters(chapters: list[dict]) -> str:
    if not chapters:
        return ""
    lines = [f"- {ch['time']} — {ch['title']}" for ch in chapters]
    return "\n".join(lines)


def write_note(task: dict, result: dict, vault: Path = VAULT_PATH) -> str:
    """
    Create a Markdown note in vault/1-Notes/ and push to Git.
    Returns relative note path.
    """
    task_id = task["task_id"]
    task_type = task["type"]
    payload = json.loads(task["payload"]) if isinstance(task["payload"], str) else task["payload"]

    title = result.get("title") or payload.get("title") or task_id
    summary = result.get("summary", {})
    brief = summary.get("brief", "")
    ideas = summary.get("ideas", [])
    actions = summary.get("actions", [])
    transcript = result.get("transcript", "")
    chapters = result.get("chapters", [])
    tags = result.get("tags", [])
    metadata = result.get("metadata", {})

    source_url = payload.get("url", "")
    source_file = payload.get("file_path", "")

    note_filename = f"{task_id}.md"
    note_path = vault / NOTES_DIR / note_filename

    note_path.parent.mkdir(parents=True, exist_ok=True)

    tags_yaml = json.dumps(tags)
    chapters_block = ""
    if chapters:
        chapters_block = f"\n## Главы\n{_format_chapters(chapters)}\n"

    transcript_block = ""
    if transcript:
        transcript_block = f"""
<details><summary>Полный транскрипт</summary>

{transcript}

</details>
"""

    content = f"""---
task_id: {task_id}
type: {task_type}
status: done
source: {source_url or source_file}
created: {task.get("created_at", _now_iso())}
processed: {_now_iso()}
worker: {task.get("assigned_to", "")}
method: {metadata.get("method", "")}
language: {metadata.get("language", "")}
duration_seconds: {metadata.get("duration_seconds", "")}
tags: {tags_yaml}
---

# {title}

## Кратко
{brief}

## Ключевые идеи
{_format_bullets(ideas)}
{chapters_block}
## Действия
{_format_bullets(actions)}
{transcript_block}"""

    # Write atomically: temp file then rename
    tmp_path = note_path.with_suffix(".tmp")
    tmp_path.write_text(content.strip(), encoding="utf-8")
    tmp_path.rename(note_path)

    _update_task_card(task_id, vault, status="done")

    git_push(vault, f"processed: {task_id}")

    return f"{NOTES_DIR}/{note_filename}"


def write_failed_note(task: dict, vault: Path = VAULT_PATH):
    """Create a review note in 0-Inbox/Failed/."""
    task_id = task["task_id"]
    payload = json.loads(task["payload"]) if isinstance(task["payload"], str) else task["payload"]

    failed_dir = vault / FAILED_DIR
    failed_dir.mkdir(parents=True, exist_ok=True)

    note_path = failed_dir / f"{task_id}.md"
    content = f"""---
task_id: {task_id}
type: {task["type"]}
status: failed
source: {payload.get("url") or payload.get("file_path") or ""}
created: {task.get("created_at", "")}
failed_at: {_now_iso()}
attempts: {task.get("attempts", 0)}
last_error: {task.get("last_error", "")}
---

# FAILED: {task_id}

**Тип:** {task["type"]}
**Ошибка:** {task.get("last_error", "unknown")}
**Попыток:** {task.get("attempts", 0)}

Задача требует ручного разбора.
"""
    note_path.write_text(content.strip(), encoding="utf-8")
    git_push(vault, f"failed: {task_id}")


def _update_task_card(task_id: str, vault: Path, status: str):
    """Update status field in task card if it exists."""
    tasks_dir = vault / TASKS_DIR
    for md_file in tasks_dir.glob(f"*{task_id}*.md"):
        text = md_file.read_text(encoding="utf-8")
        updated = ""
        for line in text.splitlines():
            if line.strip().startswith("status:"):
                updated += f"status: {status}\n"
            else:
                updated += line + "\n"
        md_file.write_text(updated, encoding="utf-8")
        break


def scan_vault_inbox(vault: Path = VAULT_PATH) -> list[dict]:
    """
    Scan 0-Inbox/Tasks/ for task cards with status: new.
    Used by vault watcher background task.
    """
    tasks_dir = vault / TASKS_DIR
    if not tasks_dir.exists():
        return []

    import re
    found = []
    for md_file in sorted(tasks_dir.glob("*.md")):
        text = md_file.read_text(encoding="utf-8")
        # Only process status: new
        if "status: new" not in text:
            continue

        task_data = _parse_frontmatter(text)
        if not task_data:
            continue

        found.append({
            "file": md_file,
            "task_data": task_data,
        })

    return found


def _parse_frontmatter(text: str) -> dict | None:
    import re
    match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
    if not match:
        return None

    result = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result
