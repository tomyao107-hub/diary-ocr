"""Unified OCR engine interface (v1.5+)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class EngineCapabilities:
    name: str
    display_name: str
    requires_network: bool
    supports_handwriting: bool = True
    supports_print: bool = True
    supports_cpu: bool = True
    supports_gpu: bool = False
    languages: tuple[str, ...] = ("zh", "en")
    notes: str = ""


@dataclass
class OCROptions:
    user_prompt: str = ""
    system_prompt: str = ""
    temperature: float = 0.1
    max_tokens: int = 4096
    language_hint: str = "zh"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class OCRResult:
    text: str
    engine: str
    engine_version: str = ""
    model: str = ""
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    created_at: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    result_schema_version: int = 1
    # Geometry for preview overlay (Paddle det boxes). Coordinates are in the
    # OCR input image pixel space (same frame as image_size).
    boxes: list[dict[str, Any]] = field(default_factory=list)
    # (width, height) of the image bytes passed to recognize(), if known.
    image_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now().astimezone().isoformat(timespec="seconds")
        if self.image_size is not None and not isinstance(self.image_size, tuple):
            try:
                w, h = self.image_size  # type: ignore[misc]
                self.image_size = (int(w), int(h))
            except Exception:
                self.image_size = None

    def to_dict(self) -> dict:
        return asdict(self)


class CancelToken:
    """Simple cooperative cancellation token."""

    def __init__(self):
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def raise_if_cancelled(self) -> None:
        if self._cancelled:
            raise InterruptedError("OCR 已取消")


class OCREngine(ABC):
    @abstractmethod
    def capabilities(self) -> EngineCapabilities:
        ...

    @abstractmethod
    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        ...

    def is_available(self) -> bool:
        return True
