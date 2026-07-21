"""Engine registry and hybrid routing (v1.5 / v2.0)."""

from __future__ import annotations

from dataclasses import dataclass

from .base import CancelToken, OCREngine, OCROptions, OCRResult
from .cloud import CloudOCREngine
from .local import LOCAL_ENGINE_ID, LocalOCREngine, TesseractLocalEngine
from .mock import MockOCREngine
from .paddle_json import (
    PADDLE_JSON_ENGINE_ID,
    PaddleOCRJsonEngine,
    get_shared_paddle_json_engine,
)


@dataclass
class HybridDecision:
    engine_id: str
    reason: str
    needs_user_confirm_for_cloud: bool = False


class EngineRegistry:
    def __init__(self):
        self._engines: dict[str, OCREngine] = {}

    def register(self, engine: OCREngine) -> None:
        caps = engine.capabilities()
        self._engines[caps.name] = engine

    def get(self, engine_id: str) -> OCREngine | None:
        return self._engines.get(engine_id)

    def list_engines(self) -> list[OCREngine]:
        return list(self._engines.values())

    def available(self) -> list[OCREngine]:
        return [engine for engine in self._engines.values() if engine.is_available()]

    def pick_local(self) -> OCREngine | None:
        """Prefer portable PaddleOCR-json, then in-process PP-OCR, then Tesseract."""
        for engine_id in (
            PADDLE_JSON_ENGINE_ID,
            LOCAL_ENGINE_ID,
            TesseractLocalEngine.ENGINE_ID,
        ):
            engine = self.get(engine_id)
            if engine and engine.is_available():
                return engine
        return None


def default_registry(
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
    temperature: float = 0.1,
    max_tokens: int = 4096,
    user_prompt: str = "",
    system_prompt: str = "",
    include_mock: bool = False,
    ocr_version: str = "PP-OCRv5",
    ppocr_model_size: str = "mobile",
    local_device: str = "cpu",
    paddleocr_json_path: str = "",
    engines_dir: str = "",
) -> EngineRegistry:
    registry = EngineRegistry()
    registry.register(
        CloudOCREngine(
            api_key=api_key,
            base_url=base_url
            or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            model=model or "qwen-vl-ocr-latest",
            temperature=temperature,
            max_tokens=max_tokens,
            user_prompt=user_prompt,
            system_prompt=system_prompt,
        )
    )
    # Portable default (Umi-style external Paddle process) — shared instance
    # so batch workers reuse one long-lived OCR subprocess.
    registry.register(
        get_shared_paddle_json_engine(
            exe_path=paddleocr_json_path or None,
            engines_dir=engines_dir or None,
        )
    )
    # In-process PP-OCR for developers with paddle installed.
    registry.register(
        LocalOCREngine(
            ocr_version=ocr_version,
            model_size=ppocr_model_size,
            device=local_device,
        )
    )
    registry.register(TesseractLocalEngine())
    if include_mock:
        registry.register(MockOCREngine())
    return registry


class HybridRouter:
    """
    Hybrid strategy:
      1. Prefer local Paddle when available and privacy allows.
      2. Flag empty local results as cloud candidates.
      3. Never send to cloud without user confirmation when hybrid+confirm.
    """

    def __init__(
        self,
        registry: EngineRegistry,
        *,
        mode: str = "local",
        privacy_local_only: bool = False,
        require_cloud_confirm: bool = True,
    ):
        self.registry = registry
        self.mode = mode  # cloud | local | hybrid
        self.privacy_local_only = privacy_local_only
        self.require_cloud_confirm = require_cloud_confirm

    def _local_decision(self, reason_prefix: str) -> HybridDecision:
        local = self.registry.pick_local()
        if local is not None:
            return HybridDecision(
                local.capabilities().name,
                f"{reason_prefix}：{local.capabilities().display_name}",
            )
        return HybridDecision(
            PADDLE_JSON_ENGINE_ID,
            f"{reason_prefix}：需要本地 Paddle 引擎，但当前不可用",
        )

    def decide(self, *, prefer_cloud: bool = False) -> HybridDecision:
        if self.privacy_local_only or self.mode == "local":
            return self._local_decision("本地/隐私模式")
        if self.mode == "cloud" or prefer_cloud:
            return HybridDecision(CloudOCREngine.ENGINE_ID, "云端模式")
        local = self.registry.pick_local()
        if local is not None:
            return HybridDecision(
                local.capabilities().name,
                f"混合模式：先本地（{local.capabilities().display_name}）",
            )
        return HybridDecision(
            CloudOCREngine.ENGINE_ID,
            "混合模式：本地不可用，候选云端",
            needs_user_confirm_for_cloud=self.require_cloud_confirm,
        )

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
        *,
        allow_cloud: bool = True,
        cloud_confirmed: bool = False,
    ) -> OCRResult:
        decision = self.decide()
        engine_id = decision.engine_id
        if engine_id == CloudOCREngine.ENGINE_ID:
            if self.privacy_local_only:
                raise RuntimeError("隐私模式禁止任何网络 OCR 请求")
            if decision.needs_user_confirm_for_cloud and not cloud_confirmed:
                raise PermissionError("混合模式：云端识别需用户确认后才会上传")
            if not allow_cloud:
                raise RuntimeError("当前不允许使用云端引擎")
        engine = self.registry.get(engine_id)
        if engine is None or not engine.is_available():
            raise RuntimeError(f"OCR 引擎不可用：{engine_id}（{decision.reason}）")
        result = engine.recognize(image, options, cancel_token)
        if (
            self.mode == "hybrid"
            and engine_id != CloudOCREngine.ENGINE_ID
            and not result.text.strip()
        ):
            result.warnings.append("本地结果为空，建议加入云端候选队列")
        return result
