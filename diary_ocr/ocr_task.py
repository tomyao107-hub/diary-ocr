"""Persistent OCR task statuses and retry helpers (v1.1+)."""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Iterable


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)
ACTIVE_STATUSES = frozenset({TaskStatus.PENDING, TaskStatus.RUNNING})


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class PageTask:
    path: str
    status: str = TaskStatus.PENDING.value
    attempts: int = 0
    last_error: str | None = None
    completed_at: str | None = None
    output: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict, *, default_path: str = "") -> "PageTask":
        if not isinstance(data, dict):
            return cls(path=default_path)
        path = str(data.get("path") or default_path or "")
        status = str(data.get("status") or TaskStatus.PENDING.value)
        if status not in {item.value for item in TaskStatus}:
            status = TaskStatus.PENDING.value
        try:
            attempts = max(0, int(data.get("attempts", 0)))
        except (TypeError, ValueError):
            attempts = 0
        last_error = data.get("last_error")
        if last_error is not None:
            last_error = str(last_error)
        completed_at = data.get("completed_at")
        if completed_at is not None:
            completed_at = str(completed_at)
        output = data.get("output")
        if output is not None:
            output = str(output)
        return cls(
            path=path,
            status=status,
            attempts=attempts,
            last_error=last_error,
            completed_at=completed_at,
            output=output,
        )

    def mark_running(self) -> None:
        self.status = TaskStatus.RUNNING.value
        self.last_error = None

    def mark_succeeded(self, output: str | None = None) -> None:
        self.status = TaskStatus.SUCCEEDED.value
        self.completed_at = _now()
        self.last_error = None
        if output is not None:
            self.output = output

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED.value
        self.last_error = error
        self.completed_at = _now()

    def mark_cancelled(self) -> None:
        if self.status in {
            TaskStatus.PENDING.value,
            TaskStatus.RUNNING.value,
        }:
            self.status = TaskStatus.CANCELLED.value

    def mark_pending(self) -> None:
        self.status = TaskStatus.PENDING.value
        self.last_error = None
        self.completed_at = None


def recover_running_tasks(tasks: Iterable[PageTask]) -> list[PageTask]:
    """After a crash, re-queue pages stuck in ``running``."""
    recovered = []
    for task in tasks:
        if task.status == TaskStatus.RUNNING.value:
            task.mark_pending()
            recovered.append(task)
    return recovered


def select_jobs(
    paths: list[str],
    tasks_by_path: dict[str, PageTask],
    mode: str,
    *,
    key_fn,
) -> list[tuple[int, str]]:
    """
    mode:
      all       — every page
      unfinished — pending / running / cancelled (not succeeded)
      failed    — failed only
    """
    jobs: list[tuple[int, str]] = []
    for index, path in enumerate(paths):
        task = tasks_by_path.get(key_fn(path))
        status = task.status if task else TaskStatus.PENDING.value
        if mode == "all":
            jobs.append((index, path))
        elif mode == "unfinished":
            if status != TaskStatus.SUCCEEDED.value:
                jobs.append((index, path))
        elif mode == "failed":
            if status == TaskStatus.FAILED.value:
                jobs.append((index, path))
        else:
            raise ValueError(f"未知批量模式: {mode}")
    return jobs


_RETRYABLE_PATTERNS = (
    re.compile(r"\b429\b"),
    re.compile(r"rate.?limit", re.I),
    re.compile(r"timeout", re.I),
    re.compile(r"timed?\s*out", re.I),
    re.compile(r"\b5\d{2}\b"),
    re.compile(r"service\s*unavailable", re.I),
    re.compile(r"temporarily\s*unavailable", re.I),
    re.compile(r"connection\s*(reset|aborted|error)", re.I),
    re.compile(r"APIConnectionError", re.I),
    re.compile(r"APITimeoutError", re.I),
    re.compile(r"InternalServerError", re.I),
    re.compile(r"RateLimitError", re.I),
)


def is_retryable_error(exc: BaseException | str) -> bool:
    text = str(exc)
    name = type(exc).__name__ if isinstance(exc, BaseException) else ""
    haystack = f"{name}: {text}"
    return any(pattern.search(haystack) for pattern in _RETRYABLE_PATTERNS)


def backoff_seconds(attempt: int, *, base: float = 1.0, cap: float = 30.0) -> float:
    """Exponential backoff with a hard ceiling. ``attempt`` is 1-based."""
    delay = base * (2 ** max(0, attempt - 1))
    return min(cap, delay)


def sleep_backoff(attempt: int, *, stop_event=None) -> bool:
    """
    Sleep for the backoff of ``attempt``.
    Returns False if interrupted by stop_event.
    """
    remaining = backoff_seconds(attempt)
    if stop_event is None:
        time.sleep(remaining)
        return True
    # Interruptible sleep in small chunks.
    while remaining > 0:
        if stop_event.is_set():
            return False
        step = min(0.25, remaining)
        time.sleep(step)
        remaining -= step
    return not stop_event.is_set()


@dataclass
class BatchSummary:
    total: int = 0
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0
    stopped: bool = False
    errors: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def format_report(self) -> str:
        lines = [
            "批量 OCR 处理报告",
            f"  计划处理：{self.total}",
            f"  已完成：{self.completed}",
            f"  成功：{self.succeeded}",
            f"  失败：{self.failed}",
            f"  跳过：{self.skipped}",
            f"  取消：{self.cancelled}",
            f"  用户停止：{'是' if self.stopped else '否'}",
        ]
        if self.errors:
            lines.append("  失败明细：")
            for item in self.errors[:50]:
                lines.append(
                    f"    - {Path(item.get('path', '')).name}: {item.get('error', '')}"
                )
            if len(self.errors) > 50:
                lines.append(f"    … 另有 {len(self.errors) - 50} 条")
        return "\n".join(lines)


def infer_status_from_output(
    image_path: str,
    output_dir: Path,
    image_paths: list[str],
    output_path_fn,
) -> str:
    """If a markdown result already exists, treat the page as succeeded."""
    preferred = output_path_fn(image_path, str(output_dir), image_paths)
    if Path(preferred).exists():
        return TaskStatus.SUCCEEDED.value
    legacy = output_dir / f"{Path(image_path).stem}.md"
    if legacy.exists():
        return TaskStatus.SUCCEEDED.value
    return TaskStatus.PENDING.value
