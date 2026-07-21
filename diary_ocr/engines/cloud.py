"""Cloud OCR engine wrapping the OpenAI-compatible client."""

from __future__ import annotations

import base64
import time
from typing import Any

from .base import (
    CancelToken,
    EngineCapabilities,
    OCREngine,
    OCROptions,
    OCRResult,
)

MAX_PIXELS = 8_000_000


class CloudOCREngine(OCREngine):
    ENGINE_ID = "qwen-cloud"
    ENGINE_VERSION = "1.0"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-vl-ocr-latest",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: float = 60.0,
        user_prompt: str = "",
        system_prompt: str = "",
    ):
        self.api_key = api_key
        self.base_url = (base_url or "").rstrip("/") or (
            "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        self.model = (model or "").strip() or "qwen-vl-ocr-latest"
        self.temperature = max(0.0, min(2.0, float(temperature)))
        self.max_tokens = max(1, int(max_tokens))
        self.timeout = max(1.0, float(timeout))
        self.default_user_prompt = user_prompt
        self.default_system_prompt = system_prompt

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name=self.ENGINE_ID,
            display_name="云端 Qwen-VL-OCR",
            requires_network=True,
            supports_handwriting=True,
            supports_print=True,
            supports_cpu=True,
            supports_gpu=False,
            languages=("zh", "en"),
            notes="OpenAI 兼容接口（默认阿里百炼）",
        )

    def is_available(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        options = options or OCROptions()
        if cancel_token:
            cancel_token.raise_if_cancelled()
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("请安装 openai 库：pip install openai") from exc

        started = time.perf_counter()
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=2,
        )
        b64_data = base64.b64encode(image).decode("utf-8")
        image_url = f"data:image/jpeg;base64,{b64_data}"
        user_prompt = options.user_prompt or self.default_user_prompt
        system_prompt = (options.system_prompt or self.default_system_prompt).strip()
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url,
                            "min_pixels": 3072,
                            "max_pixels": MAX_PIXELS,
                        },
                    },
                    {"type": "text", "text": user_prompt},
                ],
            }
        )
        if cancel_token:
            cancel_token.raise_if_cancelled()
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=(
                options.temperature
                if options.temperature is not None
                else self.temperature
            ),
            max_tokens=options.max_tokens or self.max_tokens,
        )
        if cancel_token:
            cancel_token.raise_if_cancelled()
        if not response.choices:
            raise RuntimeError("OCR API 返回空 choices，请检查模型与配额")
        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("OCR API 返回空内容，请检查模型与提示词设置")
        duration_ms = int((time.perf_counter() - started) * 1000)
        return OCRResult(
            text=content.strip(),
            engine=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            model=self.model,
            duration_ms=duration_ms,
            parameters={
                "base_url": self.base_url,
                "temperature": options.temperature,
                "max_tokens": options.max_tokens,
            },
        )
