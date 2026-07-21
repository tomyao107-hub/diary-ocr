"""Offline mock engine for tests and fault injection."""

from __future__ import annotations

import time

from .base import (
    CancelToken,
    EngineCapabilities,
    OCREngine,
    OCROptions,
    OCRResult,
)


class MockOCREngine(OCREngine):
    ENGINE_ID = "mock-offline"
    ENGINE_VERSION = "1.0"

    def __init__(
        self,
        text_template: str = "[mock-ocr] bytes={n}",
        delay_ms: int = 0,
        fail_times: int = 0,
        fail_message: str = "MockOCR forced failure",
    ):
        self.text_template = text_template
        self.delay_ms = max(0, int(delay_ms))
        self.fail_times = max(0, int(fail_times))
        self.fail_message = fail_message
        self._calls = 0

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name=self.ENGINE_ID,
            display_name="离线模拟引擎",
            requires_network=False,
            supports_handwriting=False,
            supports_print=True,
            notes="用于无网络测试与故障注入",
        )

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        self._calls += 1
        if cancel_token:
            cancel_token.raise_if_cancelled()
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000.0)
        if cancel_token:
            cancel_token.raise_if_cancelled()
        if self._calls <= self.fail_times:
            raise RuntimeError(self.fail_message)
        text = self.text_template.format(n=len(image))
        return OCRResult(
            text=text,
            engine=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            model="mock",
            duration_ms=self.delay_ms,
            parameters={"calls": self._calls},
        )
