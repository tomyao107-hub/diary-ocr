"""OCR engine abstractions (v1.5+) and concrete engines."""

from .base import EngineCapabilities, OCREngine, OCROptions, OCRResult
from .cloud import CloudOCREngine
from .local import LOCAL_ENGINE_ID, LocalOCREngine, PPOCRLocalEngine, TesseractLocalEngine
from .mock import MockOCREngine
from .registry import EngineRegistry, default_registry

__all__ = [
    "EngineCapabilities",
    "OCREngine",
    "OCROptions",
    "OCRResult",
    "CloudOCREngine",
    "LocalOCREngine",
    "PPOCRLocalEngine",
    "TesseractLocalEngine",
    "LOCAL_ENGINE_ID",
    "MockOCREngine",
    "EngineRegistry",
    "default_registry",
]
