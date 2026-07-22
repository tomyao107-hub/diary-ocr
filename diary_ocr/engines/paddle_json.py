"""PaddleOCR-json subprocess engine — portable default local OCR (Umi-style)."""

from __future__ import annotations

import atexit
import base64
import io
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from ..paths import resolve_paddleocr_json_exe
from .base import (
    CancelToken,
    EngineCapabilities,
    OCREngine,
    OCROptions,
    OCRResult,
)

PADDLE_JSON_ENGINE_ID = "paddleocr-json"


class PaddleOCRJsonEngine(OCREngine):
    """
    Offline Paddle OCR via PaddleOCR-json.exe (pipe protocol).

    Used as the default local engine in portable builds so users need neither
    Python paddle packages nor an API key.
    """

    ENGINE_ID = PADDLE_JSON_ENGINE_ID
    ENGINE_VERSION = "1.0"

    def __init__(
        self,
        *,
        exe_path: str | Path | None = None,
        engines_dir: str | None = None,
        enable_mkldnn: bool = True,
        limit_side_len: int = 960,
        cls: bool = False,
    ):
        self._explicit_exe = str(exe_path) if exe_path else None
        self._engines_dir = engines_dir
        self.enable_mkldnn = bool(enable_mkldnn)
        self.limit_side_len = int(limit_side_len)
        self.cls = bool(cls)
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._init_error: str | None = None
        self._resolved: Path | None = None
        atexit.register(self.stop)

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            name=self.ENGINE_ID,
            display_name="本地 Paddle OCR（便携）",
            requires_network=False,
            supports_handwriting=True,
            supports_print=True,
            supports_cpu=True,
            supports_gpu=False,
            languages=("zh", "en", "ja", "ko"),
            notes=(
                "PaddleOCR-json 独立进程；需 CPU 支持 AVX。"
                "便携包自带 engines/PaddleOCR-json；不可用时不会静默切云端。"
            ),
        )

    def resolve_exe(self) -> Path | None:
        if self._resolved and self._resolved.is_file():
            return self._resolved
        found = resolve_paddleocr_json_exe(
            explicit_path=self._explicit_exe,
            engines_dir=self._engines_dir,
        )
        self._resolved = found
        return found

    def is_available(self) -> bool:
        return self.resolve_exe() is not None

    def stop(self) -> None:
        with self._lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
        finally:
            # Popen does not close PIPE handles merely because the child was
            # killed/waited; close them explicitly when recycling the engine.
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

    def _start_unlocked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        exe = self.resolve_exe()
        if exe is None:
            raise RuntimeError(
                "未找到 PaddleOCR-json 引擎。\n"
                "便携版请确认 engines/PaddleOCR-json/ 目录完整；\n"
                "源码开发可安装进程内 PP-OCR，或下载 PaddleOCR-json 到 engines/。\n"
                "不会自动切换云端。"
            )
        cwd = str(exe.parent)
        cmds = [str(exe)]
        # Startup args (bools must be key=value for this engine).
        cmds.append(f"--ensure_ascii=true")
        cmds.append(f"--enable_mkldnn={'true' if self.enable_mkldnn else 'false'}")
        cmds.append(f"--limit_side_len={self.limit_side_len}")
        if self.cls:
            cmds.append("--cls=true")
            cmds.append("--use_angle_cls=true")

        startupinfo = None
        creationflags = 0
        if sys.platform == "win32":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            self._proc = subprocess.Popen(
                cmds,
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
                creationflags=creationflags,
            )
        except OSError as exc:
            self._init_error = str(exc)
            raise RuntimeError(
                f"无法启动 PaddleOCR-json：{exc}\n"
                "若提示缺少 VCOMP140.DLL，请安装 VC++ 运行库。"
            ) from exc

        # Wait for init line.
        deadline = time.time() + 60.0
        assert self._proc.stdout is not None
        while time.time() < deadline:
            if self._proc.poll() is not None:
                self._proc = None
                raise RuntimeError(
                    "PaddleOCR-json 初始化失败（进程已退出）。"
                    "CPU 可能不支持 AVX，或引擎文件损坏。"
                )
            line = self._proc.stdout.readline().decode("utf-8", errors="ignore")
            if "OCR init completed." in line:
                self._init_error = None
                return
        self._stop_unlocked()
        raise RuntimeError("PaddleOCR-json 初始化超时。")

    def _run_dict_unlocked(self, payload: dict) -> dict:
        self._start_unlocked()
        assert self._proc is not None and self._proc.stdin and self._proc.stdout
        if self._proc.poll() is not None:
            self._proc = None
            self._start_unlocked()
        write_str = json.dumps(payload, ensure_ascii=True) + "\n"
        try:
            self._proc.stdin.write(write_str.encode("utf-8"))
            self._proc.stdin.flush()
        except Exception as exc:
            self._stop_unlocked()
            raise RuntimeError(f"向 PaddleOCR-json 写入失败：{exc}") from exc
        try:
            raw = self._proc.stdout.readline().decode("utf-8", errors="ignore")
        except Exception as exc:
            self._stop_unlocked()
            raise RuntimeError(f"读取 PaddleOCR-json 输出失败：{exc}") from exc
        if not raw.strip():
            self._stop_unlocked()
            raise RuntimeError("PaddleOCR-json 返回空输出，引擎可能已崩溃。")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"PaddleOCR-json 输出不是合法 JSON：{raw[:200]}"
            ) from exc

    @staticmethod
    def parse_result(response: dict) -> tuple[str, list[str], list[dict]]:
        """Return (text, warnings, boxes) from engine JSON."""
        code = int(response.get("code", -1))
        data = response.get("data")
        warnings: list[str] = []
        boxes: list[dict] = []
        if code == 100:
            lines: list[str] = []
            if isinstance(data, list):
                for item in data:
                    if not isinstance(item, dict):
                        continue
                    text = item.get("text")
                    line = text.strip() if isinstance(text, str) else ""
                    if line:
                        lines.append(line)
                    box = item.get("box")
                    if box is not None:
                        try:
                            score = float(item.get("score", 1.0))
                        except (TypeError, ValueError):
                            score = 1.0
                        boxes.append(
                            {
                                "text": line,
                                "score": score,
                                "box": box,
                            }
                        )
            return "\n".join(lines), warnings, boxes
        if code == 101:
            warnings.append("未识别到文字")
            return "", warnings, []
        message = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        raise RuntimeError(f"Paddle OCR 失败（code={code}）：{message}")

    def recognize(
        self,
        image: bytes,
        options: OCROptions | None = None,
        cancel_token: CancelToken | None = None,
    ) -> OCRResult:
        if cancel_token:
            cancel_token.raise_if_cancelled()
        if not image:
            raise RuntimeError("图像数据为空")
        started = time.perf_counter()
        image_size: tuple[int, int] | None = None
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image)) as pil:
                image_size = (int(pil.width), int(pil.height))
        except Exception:
            image_size = None
        b64 = base64.b64encode(image).decode("ascii")
        if cancel_token:
            cancel_token.raise_if_cancelled()
        with self._lock:
            response = self._run_dict_unlocked({"image_base64": b64})
        if cancel_token:
            cancel_token.raise_if_cancelled()
        text, warnings, boxes = self.parse_result(response)
        duration_ms = int((time.perf_counter() - started) * 1000)
        exe = self.resolve_exe()
        return OCRResult(
            text=text,
            engine=self.ENGINE_ID,
            engine_version=self.ENGINE_VERSION,
            model="PaddleOCR-json",
            duration_ms=duration_ms,
            warnings=warnings,
            boxes=boxes,
            image_size=image_size,
            parameters={
                "exe": str(exe) if exe else "",
                "enable_mkldnn": self.enable_mkldnn,
                "limit_side_len": self.limit_side_len,
            },
        )


# Process-wide singleton helpers for app shutdown.
_shared_engine: PaddleOCRJsonEngine | None = None
_shared_engine_config: tuple[str | None, str | None] | None = None
_shared_lock = threading.Lock()


def get_shared_paddle_json_engine(
    *,
    exe_path: str | None = None,
    engines_dir: str | None = None,
) -> PaddleOCRJsonEngine:
    global _shared_engine, _shared_engine_config
    config = (exe_path or None, engines_dir or None)
    with _shared_lock:
        if _shared_engine is None or _shared_engine_config != config:
            if _shared_engine is not None:
                _shared_engine.stop()
            _shared_engine = PaddleOCRJsonEngine(
                exe_path=exe_path,
                engines_dir=engines_dir,
            )
            _shared_engine_config = config
        return _shared_engine


def shutdown_shared_paddle_json_engine() -> None:
    global _shared_engine, _shared_engine_config
    with _shared_lock:
        if _shared_engine is not None:
            _shared_engine.stop()
            _shared_engine = None
        _shared_engine_config = None
