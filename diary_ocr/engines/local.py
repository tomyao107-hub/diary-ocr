"""Local OCR engines: PP-OCRv5/v6 (primary, CPU) and optional Tesseract fallback."""

from __future__ import annotations

import io
import os
import shutil
import threading
import time
from typing import Any

# Skip slow hoster connectivity checks when offline / CI.
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

from .base import (
    CancelToken,
    EngineCapabilities,
    OCREngine,
    OCROptions,
    OCRResult,
)

# Primary local engine id used by HybridRouter / UI.
LOCAL_ENGINE_ID = "ppocr-local"


class PPOCRLocalEngine(OCREngine):
    """
    PaddleOCR PP-OCRv5 / PP-OCRv6 on CPU.

    Defaults to PP-OCRv5 mobile models for CPU-friendly diary pages.
    Set ocr_version to ``PP-OCRv6`` for the newer default series when available.
    """

    ENGINE_ID = LOCAL_ENGINE_ID
    ENGINE_VERSION = "2.0"

    # Prefer mobile/small on CPU; medium/server are slower but more accurate.
    CPU_PRESETS = {
        "PP-OCRv6": {
            "tiny": ("PP-OCRv6_tiny_det", "PP-OCRv6_tiny_rec"),
            "small": ("PP-OCRv6_small_det", "PP-OCRv6_small_rec"),
            "medium": ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"),
        },
        "PP-OCRv5": {
            "mobile": ("PP-OCRv5_mobile_det", "PP-OCRv5_mobile_rec"),
            "server": ("PP-OCRv5_server_det", "PP-OCRv5_server_rec"),
        },
    }

    def __init__(
        self,
        *,
        ocr_version: str = "PP-OCRv5",
        model_size: str = "mobile",
        device: str = "cpu",
        lang: str = "ch",
        enable_mkldnn: bool = True,
        cpu_threads: int | None = None,
    ):
        self.ocr_version = (ocr_version or "PP-OCRv5").strip()
        self.model_size = (model_size or "mobile").strip().lower()
        self.device = device or "cpu"
        self.lang = lang or "ch"
        self.enable_mkldnn = bool(enable_mkldnn)
        self.cpu_threads = cpu_threads
        self._pipeline: Any = None
        self._lock = threading.Lock()
        self._init_error: str | None = None

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name=self.ENGINE_ID,
            display_name=f"本地 {self.ocr_version} (CPU)",
            requires_network=False,  # inference offline; first run may download models
            supports_handwriting=True,
            supports_print=True,
            supports_cpu=True,
            supports_gpu=self.device.startswith("gpu"),
            languages=("zh", "en", "ja"),
            notes=(
                f"{self.ocr_version}/{self.model_size}，device={self.device}。"
                "首次运行会下载模型；之后完全离线。不可用时不会静默切云端。"
            ),
        )

    def is_available(self) -> bool:
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False
        # paddlepaddle is required for default paddle_static engine
        try:
            import paddle  # noqa: F401
        except ImportError:
            # onnxruntime backend may still work without paddle in some builds
            try:
                import onnxruntime  # noqa: F401
            except ImportError:
                return False
        return True

    def _resolve_model_names(self) -> tuple[str | None, str | None]:
        series = self.CPU_PRESETS.get(self.ocr_version)
        if not series:
            return None, None
        # Map aliases: mobile→small for v6, tiny/small/medium for v6
        key = self.model_size
        if self.ocr_version == "PP-OCRv6":
            if key in {"mobile", "default"}:
                key = "small"
            if key not in series:
                key = "small"
        else:
            if key in {"small", "tiny", "default"}:
                key = "mobile"
            if key not in series:
                key = "mobile"
        return series[key]

    def _build_pipeline(self):
        from paddleocr import PaddleOCR

        det_name, rec_name = self._resolve_model_names()
        kwargs: dict[str, Any] = {
            "device": self.device,
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            # Keep off by default so CPU path only loads det+rec (fewer downloads).
            "use_textline_orientation": False,
        }
        # Prefer explicit model names for CPU mobile/small; ocr_version alone
        # is ignored when model names are set (paddleocr 3.x warning).
        if det_name and rec_name:
            kwargs["text_detection_model_name"] = det_name
            kwargs["text_recognition_model_name"] = rec_name
        else:
            kwargs["ocr_version"] = self.ocr_version
            kwargs["lang"] = self.lang

        # CPU tuning — ignore unknown kwargs for older/newer paddleocr.
        if self.device == "cpu":
            if self.enable_mkldnn:
                kwargs["enable_mkldnn"] = True
            if self.cpu_threads:
                kwargs["cpu_threads"] = int(self.cpu_threads)

        try:
            return PaddleOCR(**kwargs)
        except TypeError:
            # Drop optional kwargs not supported by this paddleocr build.
            for optional in (
                "enable_mkldnn",
                "cpu_threads",
                "ocr_version",
                "text_detection_model_name",
                "text_recognition_model_name",
                "use_doc_orientation_classify",
                "use_doc_unwarping",
                "use_textline_orientation",
            ):
                kwargs.pop(optional, None)
            # Retry with minimal CPU config; try legacy 2.x API if needed.
            try:
                return PaddleOCR(
                    use_angle_cls=True,
                    lang=self.lang,
                    use_gpu=False,
                    show_log=False,
                )
            except TypeError:
                return PaddleOCR(lang=self.lang, device=self.device)

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline
            if not self.is_available():
                raise RuntimeError(
                    "本地 PP-OCR 不可用：请安装 paddlepaddle（CPU）与 paddleocr。\n"
                    "  pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/\n"
                    "  pip install paddleocr\n"
                    "不会自动切换云端。"
                )
            try:
                self._pipeline = self._build_pipeline()
                self._init_error = None
            except Exception as exc:
                self._init_error = f"{type(exc).__name__}: {exc}"
                raise RuntimeError(
                    f"初始化 PP-OCR 失败：{self._init_error}\n"
                    "请检查网络（首次需下载模型）与 paddleocr 版本。"
                ) from exc
            return self._pipeline

    @staticmethod
    def _extract_texts(result: Any) -> list[str]:
        """Normalize paddleocr 2.x / 3.x predict outputs to plain text lines."""
        lines: list[str] = []

        def from_mapping(data: dict) -> None:
            for key in ("rec_texts", "texts", "text"):
                value = data.get(key)
                if isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            lines.append(item.strip())
                    return
                if isinstance(value, str) and value.strip():
                    lines.append(value.strip())
                    return
            nested = data.get("res")
            if isinstance(nested, dict):
                from_mapping(nested)

        def from_detection(item: Any) -> bool:
            """Parse one legacy detection: [box, (text, score)] or [box, text]."""
            try:
                if not isinstance(item, (list, tuple)) or len(item) < 2:
                    return False
                payload = item[1]
                if isinstance(payload, (list, tuple)) and payload:
                    text = payload[0]
                elif isinstance(payload, str):
                    text = payload
                else:
                    return False
                if isinstance(text, str) and text.strip():
                    lines.append(text.strip())
                    return True
            except Exception:
                return False
            return False

        def from_detection_list(detections: list) -> None:
            for item in detections:
                from_detection(item)

        if result is None:
            return lines

        pages = result
        if not isinstance(pages, (list, tuple)):
            pages = [pages]

        for page in pages:
            if page is None:
                continue
            if hasattr(page, "rec_texts"):
                texts = getattr(page, "rec_texts") or []
                for item in texts:
                    if isinstance(item, str) and item.strip():
                        lines.append(item.strip())
                continue
            if hasattr(page, "json"):
                try:
                    payload = page.json
                    if callable(payload):
                        payload = payload()
                    if isinstance(payload, dict):
                        from_mapping(payload.get("res", payload))
                        continue
                except Exception:
                    pass
            if isinstance(page, dict):
                from_mapping(page)
                continue
            # Legacy 2.x page: list of detections
            if isinstance(page, list):
                if page and from_detection(page):
                    # page itself is a single detection [box, (text, score)]
                    continue
                from_detection_list(page)
        return lines

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        if cancel_token:
            cancel_token.raise_if_cancelled()
        pipeline = self._ensure_pipeline()
        if cancel_token:
            cancel_token.raise_if_cancelled()

        started = time.perf_counter()
        # Prefer ndarray path to avoid temp files.
        import numpy as np
        from PIL import Image, UnidentifiedImageError

        try:
            pil = Image.open(io.BytesIO(image))
            if pil.mode not in ("RGB", "L"):
                pil = pil.convert("RGB")
            elif pil.mode == "L":
                pil = pil.convert("RGB")
            array = np.array(pil)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise RuntimeError(f"无法解码输入图像：{exc}") from exc

        if cancel_token:
            cancel_token.raise_if_cancelled()

        raw = None
        with self._lock:
            if hasattr(pipeline, "predict"):
                raw = pipeline.predict(array)
            elif hasattr(pipeline, "ocr"):
                # Legacy 2.x
                try:
                    raw = pipeline.ocr(array, cls=True)
                except TypeError:
                    raw = pipeline.ocr(array)
            else:
                raise RuntimeError("PaddleOCR 实例缺少 predict/ocr 方法")

        if cancel_token:
            cancel_token.raise_if_cancelled()

        texts = self._extract_texts(raw)
        # Diary pages: join lines top-to-bottom. PP-OCR already sorts by position.
        text = "\n".join(texts).strip()
        duration_ms = int((time.perf_counter() - started) * 1000)
        det_name, rec_name = self._resolve_model_names()
        warnings: list[str] = []
        if not text:
            warnings.append("本地 PP-OCR 未识别到文本")

        return OCRResult(
            text=text,
            engine=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            model=f"{self.ocr_version}/{self.model_size}",
            duration_ms=duration_ms,
            warnings=warnings,
            parameters={
                "device": self.device,
                "lang": self.lang,
                "det": det_name,
                "rec": rec_name,
            },
        )


class TesseractLocalEngine(OCREngine):
    """Optional Tesseract fallback (weaker on Chinese handwriting)."""

    ENGINE_ID = "tesseract-local"
    ENGINE_VERSION = "1.0"

    def __init__(self, languages: str = "chi_sim+eng"):
        self.languages = languages or "chi_sim+eng"

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name=self.ENGINE_ID,
            display_name="本地 Tesseract（备用）",
            requires_network=False,
            supports_handwriting=False,
            supports_print=True,
            supports_cpu=True,
            supports_gpu=False,
            languages=("zh", "en"),
            notes="备用引擎；中文手写弱于 PP-OCR",
        )

    def is_available(self) -> bool:
        if shutil.which("tesseract") is None:
            return False
        try:
            import pytesseract  # noqa: F401
        except ImportError:
            return False
        return True

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        if not self.is_available():
            raise RuntimeError(
                "Tesseract 不可用：请安装 tesseract 与 pytesseract。"
            )
        if cancel_token:
            cancel_token.raise_if_cancelled()
        import pytesseract
        from PIL import Image

        started = time.perf_counter()
        pil = Image.open(io.BytesIO(image))
        text = pytesseract.image_to_string(pil, lang=self.languages)
        duration_ms = int((time.perf_counter() - started) * 1000)
        warnings = []
        if not text.strip():
            warnings.append("Tesseract 未识别到文本")
        return OCRResult(
            text=text.strip(),
            engine=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            model=f"tesseract:{self.languages}",
            duration_ms=duration_ms,
            warnings=warnings,
            parameters={"languages": self.languages},
        )


class LocalOCREngine(PPOCRLocalEngine):
    """
    Back-compat alias: local mode now means PP-OCR on CPU.
    ENGINE_ID stays ``ppocr-local`` for routing.
    """

    pass
