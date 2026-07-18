"""
历史日记 OCR 数字化助手（1.0 校对台模块）
Historical Diary OCR Digitization Assistant

架构设计：
  - ImageCompressor   : 核心图像压缩逻辑（纯业务，无 GUI 依赖）
  - OCRAPIClient      : 封装阿里百炼 Qwen-VL-OCR API 调用
  - OCRWorker         : 单张 OCR 后台线程（QThread）
  - BatchOCRWorker    : 批量 OCR 后台线程（QThread）
  - ZoomableImageViewer : 可缩放/拖拽的图片查看器（QGraphicsView）
  - LogConsole        : 可折叠日志面板
  - SettingsDialog    : API Key / Prompt 持久化设置
  - MainWindow        : 主窗口（工具栏 + 双栏校对区 + 状态栏）

依赖安装：
  pip install PyQt6 Pillow openai python-dotenv
"""

import sys
import os
import io
import json
import base64
import hashlib
import importlib.util
import subprocess
import threading
import traceback
from pathlib import Path
from concurrent.futures import (
    FIRST_COMPLETED, CancelledError, ThreadPoolExecutor, wait,
)


def _restart_with_project_venv_if_needed() -> None:
    """Use the bundled virtual environment when the system Python lacks PyQt6."""
    if importlib.util.find_spec("PyQt6") is not None:
        return

    project_dir = Path(__file__).resolve().parents[1]
    venv_python = project_dir / ".venv" / "Scripts" / "python.exe"
    if __name__ == "__main__" and venv_python.exists():
        try:
            if Path(sys.executable).resolve() != venv_python.resolve():
                completed = subprocess.run(
                    [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
                    cwd=project_dir,
                    check=False,
                )
                raise SystemExit(completed.returncode)
        except OSError as exc:
            raise RuntimeError(f"无法启动项目虚拟环境：{venv_python}") from exc

    raise ModuleNotFoundError(
        "未找到 PyQt6。请运行项目根目录下的 run_diary_ocr.cmd，"
        "或执行 .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
    )


_restart_with_project_venv_if_needed()

if __name__ == "__main__" and "--check-environment" in sys.argv:
    print(f"Python: {sys.executable}")
    print("PyQt6: available")
    sys.exit(0)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter, QTextEdit,
    QToolBar, QStatusBar, QFileDialog, QMessageBox, QDialog,
    QFormLayout, QLineEdit, QDialogButtonBox, QLabel, QPushButton,
    QVBoxLayout, QHBoxLayout, QScrollArea, QFrame, QProgressBar,
    QSizePolicy, QGraphicsView, QGraphicsScene, QGraphicsPixmapItem,
    QPlainTextEdit, QToolButton, QSpinBox, QDoubleSpinBox,
    QGroupBox, QCheckBox, QListWidget, QListWidgetItem,
    QTabWidget, QComboBox,
)
from PyQt6.QtGui import (
    QPixmap, QImage, QImageReader, QAction, QIcon, QFont, QKeySequence,
    QTransform, QColor, QPalette, QWheelEvent, QMouseEvent,
    QPainter, QBrush, QPen,
)
from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QObject, QRectF, QPointF,
    QSize, QTimer, QSettings, QPropertyAnimation, QEasingCurve,
    QMimeData, QPoint,
)

from PIL import Image, ImageOps

# ─────────────────────────────────────────────────────────────────────────────
#  配置常量
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_PATH  = Path.home() / ".diary_ocr_config.json"
SESSION_PATH = Path.home() / ".diary_ocr_session.json"
MAX_PIXELS = 8_000_000          # 安全像素上限（API 最大 8,388,608）
MAX_BYTES = 6.5 * 1024 * 1024  # 6.5 MB —— Base64 后约 8.7 MB < 10 MB 限制
INITIAL_QUALITY = 95
QUALITY_STEP = 5
MIN_QUALITY = 30
API_TIMEOUT_SECONDS = 60.0
PREVIEW_MAX_DIMENSION = 2400


def _canonical_path(path: str) -> str:
    """Return a stable path key without requiring the file to exist."""
    return os.path.normcase(os.path.abspath(path))


def _output_path_for_image(
    image_path: str,
    output_dir: str,
    image_paths: list[str] | tuple[str, ...] | None = None,
) -> Path:
    """Build a readable output path and disambiguate duplicate stems."""
    output_root = Path(output_dir)
    stem = Path(image_path).stem
    digest = hashlib.sha1(
        _canonical_path(image_path).encode("utf-8")
    ).hexdigest()[:8]
    hashed_path = output_root / f"{stem}__{digest}.md"

    has_collision = False
    if image_paths is not None:
        stem_lower = stem.lower()
        has_collision = sum(
            Path(path).stem.lower() == stem_lower for path in image_paths
        ) > 1

    # Keep existing hashed outputs stable even if the conflicting page is removed later.
    if stem.lower() == "final_diary_output" or has_collision or hashed_path.exists():
        return hashed_path
    return output_root / f"{stem}.md"


def _existing_output_path(
    image_path: str,
    output_dir: str,
    image_paths: list[str] | tuple[str, ...] | None = None,
) -> Path:
    """Resolve a current output while remaining compatible with legacy stem names."""
    preferred = _output_path_for_image(image_path, output_dir, image_paths)
    if preferred.exists():
        return preferred
    legacy = Path(output_dir) / f"{Path(image_path).stem}.md"
    return legacy if legacy.exists() else preferred


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)

DEFAULT_PROMPT = (
    "这是一份历史手写日记的扫描件，版式为竖排。"
    "请按照从右到左、从上到下的原始阅读顺序，将提取的文字准确转录为现代标准的横排文本。"
    "请尽量识别并保留繁体字、手写连笔字以及历史异体字。"
    "遇到因纸张破损、墨迹晕染导致完全无法辨认的字，请使用单个英文问号 '?' 代替。"
    "只输出提取的文本，不要添加任何多余的解释或格式。"
)

APP_STYLE = """
QMainWindow, QDialog {
    background-color: #1e1e2e;
    color: #cdd6f4;
}
QWidget {
    background-color: #1e1e2e;
    color: #cdd6f4;
    font-family: "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
    font-size: 13px;
}
QToolBar {
    background-color: #181825;
    border-bottom: 1px solid #313244;
    spacing: 4px;
    padding: 4px 8px;
}
QToolButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 5px 12px;
    font-size: 13px;
}
QToolButton:hover { background-color: #45475a; }
QToolButton:pressed { background-color: #585b70; }
QToolButton:disabled { color: #6c7086; background-color: #1e1e2e; }
QPushButton {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 600;
}
QPushButton:hover { background-color: #74c7ec; }
QPushButton:pressed { background-color: #89dceb; }
QPushButton:disabled { background-color: #45475a; color: #6c7086; }
QPushButton#danger {
    background-color: #f38ba8;
    color: #1e1e2e;
}
QPushButton#success {
    background-color: #a6e3a1;
    color: #1e1e2e;
}
QTextEdit, QPlainTextEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 8px;
    selection-background-color: #45475a;
    font-size: 15px;
    line-height: 1.8;
}
QLineEdit {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 6px 10px;
}
QLineEdit:focus { border-color: #89b4fa; }
QSplitter::handle { background-color: #313244; width: 2px; height: 2px; }
QScrollBar:vertical {
    background-color: #181825; width: 8px; border-radius: 4px;
}
QScrollBar::handle:vertical {
    background-color: #45475a; border-radius: 4px; min-height: 20px;
}
QScrollBar::handle:vertical:hover { background-color: #585b70; }
QScrollBar:horizontal {
    background-color: #181825; height: 8px; border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background-color: #45475a; border-radius: 4px; min-width: 20px;
}
QStatusBar {
    background-color: #181825;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
QLabel { color: #cdd6f4; }
QGroupBox {
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 8px;
    padding-top: 8px;
    color: #a6adc8;
    font-size: 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 6px;
    color: #89b4fa;
}
QListWidget {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    color: #cdd6f4;
}
QListWidget::item { padding: 6px 8px; }
QListWidget::item:selected {
    background-color: #313244;
    color: #89b4fa;
    border-left: 3px solid #89b4fa;
}
QListWidget::item:hover { background-color: #252535; }
QProgressBar {
    border: none;
    background-color: #313244;
    border-radius: 4px;
    height: 4px;
    text-align: center;
}
QProgressBar::chunk {
    background-color: #89b4fa;
    border-radius: 4px;
}
"""


# ─────────────────────────────────────────────────────────────────────────────
#  核心业务类：会话管理器
# ─────────────────────────────────────────────────────────────────────────────
class SessionManager:
    """
    将当前工作会话序列化到 ~/.diary_ocr_session.json，实现跨次启动的进度恢复。

    会话数据结构：
    {
      "version"       : 2,
      "saved_at"      : "2024-01-15T10:30:45",   ISO 时间戳
      "output_dir"    : "/abs/path/to/output",
      "image_paths"   : ["/abs/path/img1.jpg", ...],
      "current_path"  : "/abs/path/img4.jpg",
      "done_paths"    : ["/abs/path/img1.jpg"],   已保存为 .md 的页面
      "draft_texts"   : {"/abs/path/img4.jpg": "..."}
    }

    设计原则：
      - 纯静态/实例方法，无 GUI 依赖，可独立单测。
      - 写入使用"先写临时文件再原子 rename"，防止写入中途崩溃损坏文件。
      - 完成状态和草稿以图片路径为键，删除或移动部分图片后不会错位。
    """

    VERSION = 2

    def __init__(self, path: Path = SESSION_PATH):
        self._path = path

    # ── 写入 ─────────────────────────────────────────────────────────────────

    def save(
        self,
        image_paths: list[str],
        current_index: int,
        output_dir: str,
        done_indices: set[int],
        draft_texts: dict[int, str],
    ) -> bool:
        """
        将会话状态序列化并原子写入磁盘。
        draft_texts: 内存中已识别/编辑但尚未点"保存"落盘为 .md 的文本。
        返回是否成功。
        """
        import datetime
        current_path = (
            image_paths[current_index]
            if 0 <= current_index < len(image_paths)
            else None
        )
        done_paths = [
            image_paths[index]
            for index in sorted(done_indices)
            if 0 <= index < len(image_paths)
        ]
        drafts_by_path = {
            image_paths[index]: text
            for index, text in draft_texts.items()
            if 0 <= index < len(image_paths)
        }
        data = {
            "version":       self.VERSION,
            "saved_at":      datetime.datetime.now().isoformat(timespec="seconds"),
            "output_dir":    output_dir,
            "image_paths":   image_paths,
            "current_index": current_index,
            "current_path":  current_path,
            "done_paths":    done_paths,
            "draft_texts":   drafts_by_path,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            tmp.replace(self._path)   # 原子替换
            return True
        except Exception:
            return False

    # ── 读取 ─────────────────────────────────────────────────────────────────

    def load(self) -> dict | None:
        """
        读取并验证会话文件。
        校验：文件存在、JSON 合法、版本匹配、至少一张图片路径仍然存在。
        返回 None 表示无有效会话。
        """
        if not self._path.exists():
            return None
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None

        version = data.get("version")
        if version not in (1, self.VERSION):
            return None
        paths = data.get("image_paths", [])
        if not isinstance(paths, list) or not paths:
            return None
        # 至少一半文件仍然存在才认为会话有效
        existing = sum(1 for p in paths if Path(p).exists())
        if existing == 0:
            return None

        if version == 1:
            # Migrate index-based sessions to stable path-based state.
            current_index = data.get("current_index", 0)
            data["current_path"] = (
                paths[current_index]
                if isinstance(current_index, int) and 0 <= current_index < len(paths)
                else paths[0]
            )
            data["done_paths"] = {
                paths[index]
                for index in data.get("done_indices", [])
                if isinstance(index, int) and 0 <= index < len(paths)
            }
            migrated_drafts = {}
            for raw_index, text in data.get("draft_texts", {}).items():
                try:
                    index = int(raw_index)
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(paths) and isinstance(text, str):
                    migrated_drafts[paths[index]] = text
            data["draft_texts"] = migrated_drafts
        else:
            raw_done_paths = data.get("done_paths", [])
            data["done_paths"] = set(
                raw_done_paths if isinstance(raw_done_paths, list) else []
            )
            raw_drafts = data.get("draft_texts", {})
            data["draft_texts"] = {
                path: text
                for path, text in (
                    raw_drafts.items() if isinstance(raw_drafts, dict) else []
                )
                if isinstance(path, str) and isinstance(text, str)
            }
        return data

    @staticmethod
    def materialize(data: dict) -> tuple[list[str], int, set[int], dict[int, str]]:
        """Map path-keyed session state onto the subset of files still present."""
        paths = [path for path in data.get("image_paths", []) if Path(path).exists()]
        done_paths = {_canonical_path(path) for path in data.get("done_paths", set())}
        drafts_by_path = {
            _canonical_path(path): text
            for path, text in data.get("draft_texts", {}).items()
        }
        done_indices = {
            index
            for index, path in enumerate(paths)
            if _canonical_path(path) in done_paths
        }
        draft_texts = {
            index: drafts_by_path[_canonical_path(path)]
            for index, path in enumerate(paths)
            if _canonical_path(path) in drafts_by_path
        }
        current_key = _canonical_path(data.get("current_path") or "")
        current_index = next(
            (
                index
                for index, path in enumerate(paths)
                if _canonical_path(path) == current_key
            ),
            0,
        )
        return paths, current_index, done_indices, draft_texts

    # ── 清除 ─────────────────────────────────────────────────────────────────

    def clear(self) -> None:
        """会话完成（合并输出后）或用户手动清除时调用。"""
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass

    def exists(self) -> bool:
        return self._path.exists()

    def saved_at(self) -> str:
        """返回上次保存时间的可读字符串，用于提示用户。"""
        data = self.load()
        if data:
            return data.get("saved_at", "未知时间")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
#  核心业务类：图像压缩器
# ─────────────────────────────────────────────────────────────────────────────
class ImageCompressor:
    """
    将任意尺寸/格式的图像压缩至阿里百炼 API 的合规范围内。
    完全无 GUI 依赖，可独立单元测试。

    压缩策略：
      1. 像素控制：若总像素 > MAX_PIXELS，等比缩小。
      2. 体积控制：以 JPEG 格式，从 quality=95 开始，每次降低 QUALITY_STEP，
                   直到内存中 JPEG 体积 <= MAX_BYTES，或 quality 降至 MIN_QUALITY。
    """

    @staticmethod
    def compress(source_path: str) -> tuple[bytes, dict]:
        """
        参数：
          source_path: 原始图像文件路径
        返回：
          (jpeg_bytes, info_dict)
          info_dict 包含：original_size, original_pixels, final_size,
                          final_pixels, final_quality, scale_ratio
        """
        info = {}
        original_stat = os.path.getsize(source_path)
        info["original_size_bytes"] = original_stat
        info["original_size_human"] = ImageCompressor._human_size(original_stat)

        with Image.open(source_path) as source:
            img = ImageOps.exif_transpose(source).convert("RGB")
        orig_w, orig_h = img.size
        orig_pixels = orig_w * orig_h
        info["original_pixels"] = orig_pixels
        info["original_resolution"] = f"{orig_w}×{orig_h}"

        # ── Step 1: 像素控制 ──────────────────────────────────────────────
        if orig_pixels > MAX_PIXELS:
            ratio = (MAX_PIXELS / orig_pixels) ** 0.5
            new_w = int(orig_w * ratio)
            new_h = int(orig_h * ratio)
            img = img.resize((new_w, new_h), Image.LANCZOS)
            info["scale_ratio"] = round(ratio, 4)
            info["scaled_resolution"] = f"{new_w}×{new_h}"
        else:
            info["scale_ratio"] = 1.0
            info["scaled_resolution"] = f"{orig_w}×{orig_h}"

        # ── Step 2: 体积控制（二分搜索最高可用质量）──────────────────────
        def encode(quality_value: int) -> bytes:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality_value, optimize=True)
            return buf.getvalue()

        low, high = MIN_QUALITY, INITIAL_QUALITY
        quality = MIN_QUALITY
        jpeg_bytes = None
        while low <= high:
            candidate_quality = (low + high) // 2
            candidate = encode(candidate_quality)
            if len(candidate) <= MAX_BYTES:
                quality = candidate_quality
                jpeg_bytes = candidate
                low = candidate_quality + 1
            else:
                high = candidate_quality - 1

        if jpeg_bytes is None:
            quality = MIN_QUALITY
            jpeg_bytes = encode(quality)

        # Extremely noisy images may remain oversized at minimum quality.
        while len(jpeg_bytes) > MAX_BYTES and min(img.size) > 1:
            ratio = max(0.5, min(0.95, (MAX_BYTES / len(jpeg_bytes)) ** 0.5 * 0.95))
            new_size = (
                max(1, int(img.size[0] * ratio)),
                max(1, int(img.size[1] * ratio)),
            )
            if new_size == img.size:
                break
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            jpeg_bytes = encode(quality)

        final_ratio = min(img.size[0] / orig_w, img.size[1] / orig_h)
        info["scale_ratio"] = round(final_ratio, 4)
        info["scaled_resolution"] = f"{img.size[0]}×{img.size[1]}"

        info["final_size_bytes"] = len(jpeg_bytes)
        info["final_size_human"] = ImageCompressor._human_size(len(jpeg_bytes))
        info["final_quality"] = quality
        info["final_pixels"] = img.size[0] * img.size[1]
        info["compliant"] = len(jpeg_bytes) <= MAX_BYTES

        return jpeg_bytes, info

    @staticmethod
    def _human_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"


# ─────────────────────────────────────────────────────────────────────────────
#  核心业务类：OCR API 客户端
# ─────────────────────────────────────────────────────────────────────────────
class OCRAPIClient:
    """
    封装阿里百炼 Qwen-VL-OCR API 调用（openai SDK 兼容接口）。

    参数说明：
      api_key       : 阿里百炼 API Key（必填）
      base_url      : API 端点，默认阿里百炼兼容接口
      model         : 模型名称，默认 qwen-vl-ocr-latest
      user_prompt   : 用户侧提示词（核心 OCR 指令）
      system_prompt : 系统提示词（可选，为空则不发送 system role）
      temperature   : 生成温度，0.0~2.0，OCR 场景建议 0~0.2
      max_tokens    : 单次最大输出 Token 数
    """

    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    DEFAULT_MODEL    = "qwen-vl-ocr-latest"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        user_prompt: str = DEFAULT_PROMPT,
        system_prompt: str = "",
        temperature: float = 0.1,
        max_tokens: int = 4096,
        timeout: float = API_TIMEOUT_SECONDS,
    ):
        self.api_key       = api_key
        self.base_url      = base_url.rstrip("/") or self.DEFAULT_BASE_URL
        self.model         = model.strip() or self.DEFAULT_MODEL
        self.user_prompt   = user_prompt
        self.system_prompt = system_prompt.strip()
        self.temperature   = max(0.0, min(2.0, float(temperature)))
        self.max_tokens    = max(1, int(max_tokens))
        self.timeout       = max(1.0, float(timeout))

    def recognize(self, jpeg_bytes: bytes) -> str:
        """
        同步调用 OCR API，返回识别文本字符串。
        必须在后台线程调用（OCRWorker / BatchOCRWorker），严禁阻塞主线程。
        """
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError("请安装 openai 库：pip install openai")

        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=2,
        )

        b64_data  = base64.b64encode(jpeg_bytes).decode("utf-8")
        image_url = f"data:image/jpeg;base64,{b64_data}"

        # 构建消息列表：system prompt 可选
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})

        messages.append({
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
                {"type": "text", "text": self.user_prompt},
            ],
        })

        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        return response.choices[0].message.content.strip()


# ─────────────────────────────────────────────────────────────────────────────
#  后台线程：单张 OCR
# ─────────────────────────────────────────────────────────────────────────────
class OCRWorker(QThread):
    """
    后台执行：图像压缩 → API 识别
    信号：
      compress_done(info_dict)   压缩完成，携带压缩信息
      ocr_done(text)             识别完成，携带文本
      error(msg)                 任意错误
    """
    compress_done = pyqtSignal(dict)
    ocr_done = pyqtSignal(str, str)
    error = pyqtSignal(str)

    def __init__(self, image_path: str, api_client: OCRAPIClient):
        super().__init__()
        self.image_path = image_path
        self.api_client = api_client
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        try:
            jpeg_bytes, info = ImageCompressor.compress(self.image_path)
            if self._stop.is_set():
                return
            self.compress_done.emit(info)
            text = self.api_client.recognize(jpeg_bytes)
            if self._stop.is_set():
                return
            self.ocr_done.emit(self.image_path, text)
        except Exception as e:
            self.error.emit(f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class ConnectionTestWorker(QThread):
    success = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, api_key: str, base_url: str, model: str):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    def run(self):
        try:
            from openai import OpenAI
            client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=15.0,
                max_retries=0,
            )
            client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )
            self.success.emit(self.model)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")


class PreviewLoadWorker(QThread):
    loaded = pyqtSignal(str, QImage)
    error = pyqtSignal(str, str)

    def __init__(self, image_path: str):
        super().__init__()
        self.image_path = image_path

    def run(self):
        reader = QImageReader(self.image_path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > PREVIEW_MAX_DIMENSION:
            ratio = PREVIEW_MAX_DIMENSION / max(size.width(), size.height())
            reader.setScaledSize(QSize(
                max(1, int(size.width() * ratio)),
                max(1, int(size.height() * ratio)),
            ))
        image = reader.read()
        if image.isNull():
            self.error.emit(self.image_path, reader.errorString() or "无法读取图片")
            return
        self.loaded.emit(self.image_path, image)


# ─────────────────────────────────────────────────────────────────────────────
#  后台线程：批量 OCR（支持可配置并发线程数）
# ─────────────────────────────────────────────────────────────────────────────
class BatchOCRWorker(QThread):
    """
    批量静默处理所有图片并保存 .md 文件。

    并发策略：
      使用 ThreadPoolExecutor，线程数由 max_workers 控制（默认 3）。
      每个子任务独立完成"压缩 → API 请求 → 保存"三步，互不阻塞。
      中止信号通过 _stop 标志传递；已提交的任务会完成当前步骤后退出。

    信号：
      progress(current, total, path)   —— 每张完成或失败时发出
      item_done(index, path, text)     —— 每张成功完成时发出
      log(msg)                         —— 日志字符串
      batch_done(summary)              —— 全部完成（含中止情况）
      error(index, path, msg)          —— 单张失败时发出
    """
    progress  = pyqtSignal(int, int, str)
    item_done = pyqtSignal(int, str, str)
    log       = pyqtSignal(str)
    batch_done = pyqtSignal(dict)
    error     = pyqtSignal(int, str, str)

    def __init__(
        self,
        image_paths: list[str],
        api_client: OCRAPIClient,
        output_dir: str,
        max_workers: int = 3,
    ):
        super().__init__()
        self.image_paths = tuple(image_paths)
        self.api_client  = api_client
        self.output_dir  = output_dir
        self.max_workers = max(1, int(max_workers))
        self._stop       = threading.Event()

    def stop(self):
        self._stop.set()

    def _process_one(self, index: int, path: str) -> tuple[int, str, str]:
        """单任务：压缩 → API → 保存，在线程池子线程内执行。"""
        if self._stop.is_set():
            raise CancelledError()
        jpeg_bytes, info = ImageCompressor.compress(path)
        if self._stop.is_set():
            raise CancelledError()
        self.log.emit(
            f"[{index+1}] 🗜️ {Path(path).name}: "
            f"{info['original_size_human']} → {info['final_size_human']} "
            f"q={info['final_quality']} {info['scaled_resolution']}"
        )
        text = self.api_client.recognize(jpeg_bytes)
        if self._stop.is_set():
            raise CancelledError()
        md_path = _output_path_for_image(path, self.output_dir, self.image_paths)
        _write_text_atomic(md_path, text)
        return index, path, text

    def run(self):
        total = len(self.image_paths)
        completed = 0
        succeeded = 0
        failed = 0
        executor = None
        try:
            Path(self.output_dir).mkdir(parents=True, exist_ok=True)
            self.log.emit(
                f"⚡ 批量识别启动：共 {total} 张，并发线程数 = {self.max_workers}"
            )
            executor = ThreadPoolExecutor(max_workers=self.max_workers)
            future_map = {}
            pending = iter(enumerate(self.image_paths))

            def submit_next() -> bool:
                if self._stop.is_set():
                    return False
                try:
                    index, path = next(pending)
                except StopIteration:
                    return False
                future = executor.submit(self._process_one, index, path)
                future_map[future] = (index, path)
                return True

            for _ in range(min(self.max_workers, total)):
                submit_next()

            while future_map:
                done, _ = wait(tuple(future_map), return_when=FIRST_COMPLETED)
                for future in done:
                    index, path = future_map.pop(future)
                    try:
                        result_index, result_path, text = future.result()
                        succeeded += 1
                        self.item_done.emit(result_index, result_path, text)
                        self.log.emit(
                            f"✅ [{succeeded}/{total}] {Path(result_path).name} 识别完成"
                            f"（{len(text)} 字）"
                        )
                    except CancelledError:
                        pass
                    except Exception as exc:
                        failed += 1
                        message = f"{type(exc).__name__}: {exc}"
                        self.log.emit(f"❌ {Path(path).name}: {message}")
                        self.error.emit(index, path, message)
                    completed += 1
                    self.progress.emit(completed, total, path)
                    submit_next()

                if self._stop.is_set():
                    for future in future_map:
                        future.cancel()
                    self.log.emit("⚠️ 正在停止批量识别，等待当前请求结束")
        except Exception as exc:
            failed += 1
            message = f"{type(exc).__name__}: {exc}"
            self.log.emit(f"❌ 批量识别初始化失败：{message}")
            self.error.emit(-1, "", message)
        finally:
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
            self.batch_done.emit({
                "total": total,
                "completed": completed,
                "succeeded": succeeded,
                "failed": failed,
                "stopped": self._stop.is_set(),
            })


# ─────────────────────────────────────────────────────────────────────────────
#  GUI 组件：可缩放 / 可拖拽图片查看器
# ─────────────────────────────────────────────────────────────────────────────
class ZoomableImageViewer(QGraphicsView):
    """
    基于 QGraphicsView 的高性能图片查看器。

    特性：
      - 鼠标滚轮缩放（以鼠标位置为中心）
      - 鼠标左键拖拽平移（ScrollHandDrag 模式）
      - Ctrl+= / Ctrl+- / Ctrl+0 键盘快捷键
      - 支持自适应窗口大小（首次加载 fit）
      - 显示当前缩放比例
    """

    zoom_changed = pyqtSignal(float)   # 缩放变化信号，携带缩放倍数

    ZOOM_MIN = 0.05
    ZOOM_MAX = 16.0
    ZOOM_FACTOR = 1.15  # 每次滚轮缩放倍率

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._pixmap_item: QGraphicsPixmapItem | None = None
        self._zoom_level = 1.0

        # 渲染质量
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # 拖拽模式
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

        # 外观
        self.setBackgroundBrush(QBrush(QColor("#11111b")))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 占位文字
        self._placeholder = self._scene.addText("← 在左侧文件列表选择图片")
        self._placeholder.setDefaultTextColor(QColor("#45475a"))
        font = QFont()
        font.setPointSize(16)
        self._placeholder.setFont(font)

    # ── 公开 API ─────────────────────────────────────────────────────────────

    def load_image_from_bytes(self, data: bytes):
        """从内存字节加载图像（处理后的 JPEG）"""
        qimg = QImage.fromData(data)
        self._load_qimage(qimg)

    def load_qimage(self, image: QImage):
        self._load_qimage(image)

    def load_image_from_path(self, path: str):
        """同步加载限尺寸预览；主窗口通常使用 PreviewLoadWorker。"""
        reader = QImageReader(path)
        reader.setAutoTransform(True)
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > PREVIEW_MAX_DIMENSION:
            ratio = PREVIEW_MAX_DIMENSION / max(size.width(), size.height())
            reader.setScaledSize(QSize(
                max(1, int(size.width() * ratio)),
                max(1, int(size.height() * ratio)),
            ))
        self._load_qimage(reader.read())

    def clear_image(self):
        self._scene.clear()
        self._pixmap_item = None
        self._placeholder = self._scene.addText("← 在左侧文件列表选择图片")
        self._placeholder.setDefaultTextColor(QColor("#45475a"))
        font = QFont()
        font.setPointSize(16)
        self._placeholder.setFont(font)

    def fit_in_view(self):
        """缩放至适应视口"""
        if self._pixmap_item is None:
            return
        self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = self.transform().m11()
        self.zoom_changed.emit(self._zoom_level)

    def zoom_in(self):
        self._apply_zoom(self.ZOOM_FACTOR)

    def zoom_out(self):
        self._apply_zoom(1 / self.ZOOM_FACTOR)

    def zoom_reset(self):
        self.fit_in_view()

    # ── 内部方法 ─────────────────────────────────────────────────────────────

    def _load_qimage(self, qimg: QImage):
        if qimg.isNull():
            return
        self._scene.clear()
        pixmap = QPixmap.fromImage(qimg)
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._pixmap_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.fit_in_view()

    def _apply_zoom(self, factor: float):
        new_zoom = self._zoom_level * factor
        if not (self.ZOOM_MIN <= new_zoom <= self.ZOOM_MAX):
            return
        self.scale(factor, factor)
        self._zoom_level = new_zoom
        self.zoom_changed.emit(self._zoom_level)

    # ── 事件重写 ─────────────────────────────────────────────────────────────

    def wheelEvent(self, event: QWheelEvent):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            # Ctrl + 滚轮：快速大幅缩放
            delta = event.angleDelta().y()
            factor = self.ZOOM_FACTOR ** (delta / 120 * 2)
        else:
            delta = event.angleDelta().y()
            factor = self.ZOOM_FACTOR if delta > 0 else 1 / self.ZOOM_FACTOR
        self._apply_zoom(factor)
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 初次加载自适应
        if self._pixmap_item is not None:
            pass  # 不自动 refit，保持用户当前缩放

    def mouseDoubleClickEvent(self, event: QMouseEvent):
        """双击恢复适应视口"""
        self.fit_in_view()
        super().mouseDoubleClickEvent(event)


# ─────────────────────────────────────────────────────────────────────────────
#  GUI 组件：可折叠日志控制台
# ─────────────────────────────────────────────────────────────────────────────
class LogConsole(QWidget):
    """可折叠的日志输出面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._expanded = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 标题栏（可点击折叠）
        header = QWidget()
        header.setFixedHeight(28)
        header.setStyleSheet(
            "background-color: #181825; border-top: 1px solid #313244; cursor: pointer;"
        )
        header.mousePressEvent = lambda e: self.toggle()

        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(10, 0, 10, 0)

        self._toggle_icon = QLabel("▼")
        self._toggle_icon.setStyleSheet("color: #89b4fa; font-size: 10px;")
        title = QLabel("📋 日志控制台")
        title.setStyleSheet("color: #a6adc8; font-size: 12px;")

        clear_btn = QToolButton()
        clear_btn.setText("清空")
        clear_btn.setStyleSheet(
            "color: #6c7086; font-size: 11px; border: none; background: transparent;"
        )
        clear_btn.clicked.connect(self.clear)

        h_layout.addWidget(self._toggle_icon)
        h_layout.addWidget(title)
        h_layout.addStretch()
        h_layout.addWidget(clear_btn)

        # 日志文本区
        self._log_area = QPlainTextEdit()
        self._log_area.setReadOnly(True)
        self._log_area.setMaximumHeight(180)
        self._log_area.setStyleSheet(
            "background-color: #11111b; color: #a6e3a1; "
            "font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 12px; "
            "border: none; border-top: 1px solid #313244;"
        )

        layout.addWidget(header)
        layout.addWidget(self._log_area)

    def log(self, msg: str):
        self._log_area.appendPlainText(msg)
        # 自动滚到底部
        self._log_area.verticalScrollBar().setValue(
            self._log_area.verticalScrollBar().maximum()
        )

    def clear(self):
        self._log_area.clear()

    def toggle(self):
        self._expanded = not self._expanded
        self._log_area.setVisible(self._expanded)
        self._toggle_icon.setText("▼" if self._expanded else "▶")


# ─────────────────────────────────────────────────────────────────────────────
#  GUI 组件：设置对话框（三标签页：API连接 / 模型参数 / 提示词）
# ─────────────────────────────────────────────────────────────────────────────
class SettingsDialog(QDialog):
    """
    全量模型设置对话框，使用 QTabWidget 分三个标签页：

    ┌─ Tab 1: API 连接 ──────────────────────────────────┐
    │  API Key（掩码 + 眼睛按钮）                         │
    │  Base URL（可修改，含"恢复默认"按钮）               │
    │  模型名称（可编辑 ComboBox，含常用预设）             │
    │  输出目录                                           │
    └─────────────────────────────────────────────────────┘
    ┌─ Tab 2: 模型参数 ──────────────────────────────────┐
    │  Temperature   [0.00 ── slider ── 2.00]            │
    │  Max Tokens    [SpinBox 128~32768]                  │
    │  并发线程数    [SpinBox 1~16]（批量 OCR）           │
    └─────────────────────────────────────────────────────┘
    ┌─ Tab 3: 提示词 ────────────────────────────────────┐
    │  System Prompt（可选，留空则不发送 system role）    │
    │  User Prompt  （核心 OCR 指令，含"恢复默认"按钮）   │
    └─────────────────────────────────────────────────────┘
    """

    _PRESET_MODELS = [
        "qwen-vl-ocr-latest",
        "qwen-vl-ocr",
        "qwen-vl-max-latest",
        "qwen-vl-plus-latest",
        "gpt-4o",
        "gpt-4-vision-preview",
    ]
    _DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️  全局设置")
        self.setMinimumSize(620, 520)
        self.config = dict(config)
        self._connection_worker: ConnectionTestWorker | None = None
        self._setup_ui()

    # ── 界面构建 ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #313244;
                border-radius: 8px;
                background: #181825;
            }
            QTabBar::tab {
                background: #1e1e2e;
                color: #6c7086;
                border: 1px solid #313244;
                border-bottom: none;
                border-radius: 6px 6px 0 0;
                padding: 6px 18px;
                margin-right: 2px;
                font-size: 13px;
            }
            QTabBar::tab:selected {
                background: #181825;
                color: #89b4fa;
                font-weight: 600;
            }
            QTabBar::tab:hover:!selected { background: #252535; color: #cdd6f4; }
        """)

        tabs.addTab(self._build_api_tab(),    "🔑  API 连接")
        tabs.addTab(self._build_params_tab(), "🎛️  模型参数")
        tabs.addTab(self._build_prompt_tab(), "📝  提示词")

        # 底部按钮
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._on_accept)
        btns.rejected.connect(self.reject)

        root.addWidget(tabs, 1)
        root.addWidget(btns)

    # ── Tab 1: API 连接 ──────────────────────────────────────────────────────

    def _build_api_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        # ── API Key ──────────────────────────────────────────────────────────
        key_group = QGroupBox("身份认证")
        key_form = QFormLayout(key_group)
        key_form.setVerticalSpacing(10)
        key_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._api_key_edit = QLineEdit(self.config.get("api_key", ""))
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
        self._api_key_edit.setMinimumWidth(340)

        self._show_key_btn = QToolButton()
        self._show_key_btn.setText("👁  显示")
        self._show_key_btn.setCheckable(True)
        self._show_key_btn.setStyleSheet(
            "QToolButton { background: #313244; border-radius:4px; padding:3px 8px; color:#cdd6f4; }"
            "QToolButton:checked { background: #45475a; }"
        )
        self._show_key_btn.toggled.connect(self._toggle_key_visibility)

        key_row = QHBoxLayout()
        key_row.addWidget(self._api_key_edit, 1)
        key_row.addWidget(self._show_key_btn)
        key_form.addRow("API Key:", key_row)

        # ── Base URL ─────────────────────────────────────────────────────────
        url_row = QHBoxLayout()
        self._base_url_edit = QLineEdit(
            self.config.get("base_url", self._DEFAULT_BASE_URL)
        )
        self._base_url_edit.setPlaceholderText(self._DEFAULT_BASE_URL)
        reset_url_btn = QToolButton()
        reset_url_btn.setText("↺ 默认")
        reset_url_btn.setStyleSheet(
            "QToolButton { background: #313244; border-radius:4px; padding:3px 8px; color:#cdd6f4; }"
        )
        reset_url_btn.clicked.connect(
            lambda: self._base_url_edit.setText(self._DEFAULT_BASE_URL)
        )
        url_row.addWidget(self._base_url_edit, 1)
        url_row.addWidget(reset_url_btn)
        key_form.addRow("Base URL:", url_row)

        # ── 模型名称 ─────────────────────────────────────────────────────────
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._model_combo.addItems(self._PRESET_MODELS)
        saved_model = self.config.get("model", "qwen-vl-ocr-latest")
        if saved_model not in self._PRESET_MODELS:
            self._model_combo.insertItem(0, saved_model)
        self._model_combo.setCurrentText(saved_model)
        self._model_combo.setStyleSheet(
            "QComboBox { background:#181825; color:#cdd6f4; border:1px solid #45475a;"
            "border-radius:6px; padding:4px 8px; }"
            "QComboBox::drop-down { border:none; }"
            "QComboBox QAbstractItemView { background:#181825; color:#cdd6f4;"
            "selection-background-color:#313244; }"
        )
        key_form.addRow("模型名称:", self._model_combo)

        # ── 输出目录 ─────────────────────────────────────────────────────────
        out_group = QGroupBox("输出目录")
        out_layout = QHBoxLayout(out_group)
        self._out_dir_edit = QLineEdit(
            self.config.get("output_dir", str(Path.home() / "diary_ocr_output"))
        )
        browse_btn = QPushButton("浏览…")
        browse_btn.setFixedWidth(70)
        browse_btn.clicked.connect(self._browse_output_dir)
        out_layout.addWidget(self._out_dir_edit, 1)
        out_layout.addWidget(browse_btn)

        # 连接测试按钮
        self._test_btn = QPushButton("🔗  测试连接（ping API）")
        self._test_btn.clicked.connect(self._test_connection)

        layout.addWidget(key_group)
        layout.addWidget(out_group)
        layout.addWidget(self._test_btn)
        layout.addStretch()
        return page

    # ── Tab 2: 模型参数 ──────────────────────────────────────────────────────

    def _build_params_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        param_group = QGroupBox("推理参数")
        form = QFormLayout(param_group)
        form.setVerticalSpacing(14)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # ── Temperature ──────────────────────────────────────────────────────
        temp_row = QHBoxLayout()
        self._temp_spin = QDoubleSpinBox()
        self._temp_spin.setRange(0.0, 2.0)
        self._temp_spin.setSingleStep(0.05)
        self._temp_spin.setDecimals(2)
        self._temp_spin.setValue(float(self.config.get("temperature", 0.1)))
        self._temp_spin.setFixedWidth(90)
        self._temp_spin.setStyleSheet(
            "QDoubleSpinBox { background:#181825; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:6px; padding:4px 8px; }"
        )

        # 滑块联动
        from PyQt6.QtWidgets import QSlider
        self._temp_slider = QSlider(Qt.Orientation.Horizontal)
        self._temp_slider.setRange(0, 200)
        self._temp_slider.setValue(int(float(self.config.get("temperature", 0.1)) * 100))
        self._temp_slider.setStyleSheet(
            "QSlider::groove:horizontal { background:#313244; height:4px; border-radius:2px; }"
            "QSlider::handle:horizontal { background:#89b4fa; width:14px; height:14px;"
            "margin:-5px 0; border-radius:7px; }"
            "QSlider::sub-page:horizontal { background:#89b4fa; border-radius:2px; }"
        )

        # 双向联动
        self._temp_spin.valueChanged.connect(
            lambda v: self._temp_slider.setValue(int(v * 100))
        )
        self._temp_slider.valueChanged.connect(
            lambda v: self._temp_spin.setValue(v / 100.0)
        )

        temp_hint = QLabel("  ← 越低结果越稳定（OCR 建议 0.0~0.2）")
        temp_hint.setStyleSheet("color:#6c7086; font-size:11px;")

        temp_row.addWidget(self._temp_spin)
        temp_row.addWidget(self._temp_slider, 1)
        temp_row.addWidget(temp_hint)
        form.addRow("Temperature:", temp_row)

        # ── Max Tokens ───────────────────────────────────────────────────────
        tokens_row = QHBoxLayout()
        self._max_tokens_spin = QSpinBox()
        self._max_tokens_spin.setRange(128, 32768)
        self._max_tokens_spin.setSingleStep(256)
        self._max_tokens_spin.setValue(int(self.config.get("max_tokens", 4096)))
        self._max_tokens_spin.setFixedWidth(100)
        self._max_tokens_spin.setStyleSheet(
            "QSpinBox { background:#181825; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:6px; padding:4px 8px; }"
        )
        tokens_hint = QLabel("  单页日记建议 2048~8192")
        tokens_hint.setStyleSheet("color:#6c7086; font-size:11px;")
        tokens_row.addWidget(self._max_tokens_spin)
        tokens_row.addWidget(tokens_hint)
        tokens_row.addStretch()
        form.addRow("Max Tokens:", tokens_row)

        # ── 并发线程数 ───────────────────────────────────────────────────────
        workers_row = QHBoxLayout()
        self._workers_spin = QSpinBox()
        self._workers_spin.setRange(1, 16)
        self._workers_spin.setValue(int(self.config.get("max_workers", 3)))
        self._workers_spin.setFixedWidth(80)
        self._workers_spin.setStyleSheet(
            "QSpinBox { background:#181825; color:#cdd6f4; "
            "border:1px solid #45475a; border-radius:6px; padding:4px 8px; }"
        )
        workers_hint = QLabel("  批量识别并发数（受 API QPS 限制，建议 1~5）")
        workers_hint.setStyleSheet("color:#6c7086; font-size:11px;")
        workers_row.addWidget(self._workers_spin)
        workers_row.addWidget(workers_hint)
        workers_row.addStretch()
        form.addRow("并发线程数:", workers_row)

        # 参数说明卡片
        hint_card = QLabel(
            "💡  <b>参数说明</b><br>"
            "• <b>Temperature = 0</b>：完全确定性，最适合 OCR 精确识别<br>"
            "• <b>Max Tokens</b>：超出截断，长页建议调大至 8192<br>"
            "• <b>并发线程</b>：同时处理多张图片，API 有 QPS 限速时请降低"
        )
        hint_card.setWordWrap(True)
        hint_card.setStyleSheet(
            "background:#11111b; color:#a6adc8; font-size:12px; "
            "border:1px solid #313244; border-radius:8px; padding:12px;"
        )

        layout.addWidget(param_group)
        layout.addWidget(hint_card)
        layout.addStretch()
        return page

    # ── Tab 3: 提示词 ────────────────────────────────────────────────────────

    def _build_prompt_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # System Prompt（可选）
        sys_group = QGroupBox("System Prompt（可选）")
        sys_layout = QVBoxLayout(sys_group)
        sys_hint = QLabel("留空则不发送 system role；可用于约束模型输出格式或角色扮演。")
        sys_hint.setStyleSheet("color:#6c7086; font-size:11px;")
        self._system_prompt_edit = QTextEdit()
        self._system_prompt_edit.setPlainText(self.config.get("system_prompt", ""))
        self._system_prompt_edit.setPlaceholderText(
            "例如：你是一个专业的历史文献整理员，擅长识别民国时期的手写文字……"
        )
        self._system_prompt_edit.setFixedHeight(100)
        clear_sys_btn = QToolButton()
        clear_sys_btn.setText("清空")
        clear_sys_btn.setStyleSheet(
            "QToolButton { background:#313244; border-radius:4px; "
            "padding:3px 10px; color:#f38ba8; }"
        )
        clear_sys_btn.clicked.connect(self._system_prompt_edit.clear)

        sys_btn_row = QHBoxLayout()
        sys_btn_row.addWidget(sys_hint)
        sys_btn_row.addStretch()
        sys_btn_row.addWidget(clear_sys_btn)
        sys_layout.addLayout(sys_btn_row)
        sys_layout.addWidget(self._system_prompt_edit)

        # User Prompt（核心指令）
        user_group = QGroupBox("User Prompt（核心 OCR 指令）")
        user_layout = QVBoxLayout(user_group)
        user_hint = QLabel("随图片一起发送的识别指令，{image} 占位符会被实际图像替换。")
        user_hint.setStyleSheet("color:#6c7086; font-size:11px;")

        self._user_prompt_edit = QTextEdit()
        self._user_prompt_edit.setPlainText(
            self.config.get("user_prompt", DEFAULT_PROMPT)
        )
        self._user_prompt_edit.setMinimumHeight(160)

        # 字符计数
        self._prompt_char_label = QLabel("")
        self._prompt_char_label.setStyleSheet("color:#6c7086; font-size:11px;")
        self._user_prompt_edit.textChanged.connect(self._update_prompt_char_count)
        self._update_prompt_char_count()

        reset_user_btn = QPushButton("↺  恢复默认提示词")
        reset_user_btn.clicked.connect(
            lambda: self._user_prompt_edit.setPlainText(DEFAULT_PROMPT)
        )

        user_btn_row = QHBoxLayout()
        user_btn_row.addWidget(user_hint)
        user_btn_row.addStretch()
        user_btn_row.addWidget(self._prompt_char_label)
        user_btn_row.addWidget(reset_user_btn)

        user_layout.addLayout(user_btn_row)
        user_layout.addWidget(self._user_prompt_edit, 1)

        layout.addWidget(sys_group)
        layout.addWidget(user_group, 1)
        return page

    # ── 槽与工具方法 ─────────────────────────────────────────────────────────

    def _toggle_key_visibility(self, checked: bool):
        self._api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password
        )
        self._show_key_btn.setText("🙈  隐藏" if checked else "👁  显示")

    def _browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if d:
            self._out_dir_edit.setText(d)

    def _update_prompt_char_count(self):
        n = len(self._user_prompt_edit.toPlainText())
        self._prompt_char_label.setText(f"{n} 字")

    def _test_connection(self):
        """在后台线程中快速验证 API Key、端点和模型。"""
        key = self._api_key_edit.text().strip()
        url = self._base_url_edit.text().strip() or self._DEFAULT_BASE_URL
        model = self._model_combo.currentText().strip()
        if not key:
            QMessageBox.warning(self, "提示", "请先填写 API Key。")
            return
        if self._connection_worker and self._connection_worker.isRunning():
            return
        self._test_btn.setEnabled(False)
        self._test_btn.setText("正在测试连接…")
        self._connection_worker = ConnectionTestWorker(key, url, model)
        self._connection_worker.success.connect(
            lambda tested_model: QMessageBox.information(
                self, "连接成功 ✅",
                f"API 鉴权通过！\nModel: {tested_model}\nBase URL: {url}"
            )
        )
        self._connection_worker.error.connect(
            lambda message: QMessageBox.critical(
                self, "连接失败 ❌",
                f"请检查 API Key、Base URL、模型或网络连接：\n\n{message}"
            )
        )
        self._connection_worker.finished.connect(self._on_connection_test_finished)
        self._connection_worker.start()

    def _on_connection_test_finished(self):
        self._test_btn.setEnabled(True)
        self._test_btn.setText("🔗  测试连接（ping API）")
        if self._connection_worker:
            self._connection_worker.deleteLater()
            self._connection_worker = None

    def reject(self):
        if self._connection_worker and self._connection_worker.isRunning():
            QMessageBox.information(self, "正在测试连接", "请等待连接测试结束后再关闭设置。")
            return
        super().reject()

    def _on_accept(self):
        if self._connection_worker and self._connection_worker.isRunning():
            QMessageBox.information(self, "正在测试连接", "请等待连接测试结束后再保存设置。")
            return
        # 基本校验
        if not self._api_key_edit.text().strip():
            QMessageBox.warning(self, "提示", "API Key 不能为空。")
            return
        if not self._user_prompt_edit.toPlainText().strip():
            QMessageBox.warning(self, "提示", "User Prompt 不能为空。")
            return
        if not self._out_dir_edit.text().strip():
            QMessageBox.warning(self, "提示", "输出目录不能为空。")
            return
        self.accept()

    def get_config(self) -> dict:
        """返回从对话框中收集的完整配置字典。"""
        return {
            # Tab 1
            "api_key":    self._api_key_edit.text().strip(),
            "base_url":   self._base_url_edit.text().strip() or self._DEFAULT_BASE_URL,
            "model":      self._model_combo.currentText().strip(),
            "output_dir": self._out_dir_edit.text().strip(),
            # Tab 2
            "temperature": round(self._temp_spin.value(), 2),
            "max_tokens":  self._max_tokens_spin.value(),
            "max_workers": self._workers_spin.value(),
            # Tab 3
            "system_prompt": self._system_prompt_edit.toPlainText().strip(),
            "user_prompt":   self._user_prompt_edit.toPlainText().strip(),
        }


# ─────────────────────────────────────────────────────────────────────────────
#  主窗口
# ─────────────────────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):

    SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}

    # ── 自然排序 key（1,2,...,10 而非 1,10,2,...）────────────────────────────
    @staticmethod
    def _natural_key(path: str) -> list:
        """
        将文件名拆分为 [文字段, 数字段, ...] 交替列表，用于自然排序。
        示例: "diary_10.jpg" → ["diary_", 10, ".jpg"]
        """
        import re
        name = Path(path).name
        parts = re.split(r"(\d+)", name)
        return [int(p) if p.isdigit() else p.lower() for p in parts]

    def __init__(self):
        super().__init__()
        self.setWindowTitle("历史日记 OCR 数字化助手")
        self.resize(1400, 860)

        # 运行时状态
        self._image_paths: list[str]   = []
        self._current_index: int       = -1
        self._ocr_texts: dict[int, str] = {}   # index → 内存中最新文本（含未保存草稿）
        self._done_indices: set[int]   = set() # 已写盘为 .md 的页面索引
        self._ocr_worker: OCRWorker | None   = None
        self._batch_worker: BatchOCRWorker | None = None
        self._preview_workers: set[PreviewLoadWorker] = set()
        self._batch_ui_running = False

        # 进度会话
        self._session = SessionManager()

        # 配置
        self._config = self._load_config()

        self._setup_ui()
        self._connect_signals()
        self._update_buttons()

        # 自动保存定时器（每 60 秒静默写一次会话）
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setInterval(60_000)
        self._auto_save_timer.timeout.connect(self._session_autosave)

        # 启动时检测上次会话
        QTimer.singleShot(300, self._offer_restore_session)

    # ── 界面构建 ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        # ── 工具栏 ───────────────────────────────────────────────────────────
        toolbar = QToolBar("主工具栏")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(18, 18))
        self.addToolBar(toolbar)

        def make_action(text: str, shortcut: str = "", tip: str = "") -> QAction:
            act = QAction(text, self)
            if shortcut:
                act.setShortcut(QKeySequence(shortcut))
            if tip:
                act.setToolTip(tip)
            return act

        self._act_open_files  = make_action("📂 导入图片",   "Ctrl+O",       "选择多张日记图片")
        self._act_open_folder = make_action("📁 导入文件夹", "Ctrl+Shift+O", "导入整个文件夹")
        self._act_settings    = make_action("⚙️ 设置",       "",             "API Key、提示词、输出目录")
        self._act_batch_ocr   = make_action("⚡ 批量识别",   "Ctrl+B",       "后台一键识别所有图片")
        self._act_stop_batch  = make_action("⏹ 停止",        "",             "中止批量识别")
        self._act_stop_batch.setEnabled(False)
        self._act_merge       = make_action("📄 合并输出",   "Ctrl+M",       "合并所有 .md 为一个文件")
        self._act_save_session = make_action("💾 保存进度",  "Ctrl+S",       "立即保存当前会话进度")
        self._act_restore_session = make_action("📌 恢复上次", "",            "恢复上次未完成的工作")
        self._act_clear_session   = make_action("🗑 清除进度", "",            "删除已保存的会话文件")

        for act in [
            self._act_open_files, self._act_open_folder, None,
            self._act_settings, None,
            self._act_batch_ocr, self._act_stop_batch, None,
            self._act_merge, None,
            self._act_save_session, self._act_restore_session, self._act_clear_session,
        ]:
            if act is None:
                toolbar.addSeparator()
            else:
                toolbar.addAction(act)

        # ── 中央区域 ─────────────────────────────────────────────────────────
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 进度条（批量时显示）
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setFixedHeight(4)
        self._progress_bar.setTextVisible(False)
        main_layout.addWidget(self._progress_bar)

        # 主水平分割：文件列表 | 图片查看 | 文本编辑
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(self._main_splitter, 1)

        # ── 左侧：文件列表面板 ───────────────────────────────────────────────
        left_panel = QWidget()
        left_panel.setFixedWidth(220)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(4)

        # 标题行：标签 + 自然排序按钮
        list_header = QHBoxLayout()
        list_label = QLabel("📋 文件列表")
        list_label.setStyleSheet("color: #89b4fa; font-size: 12px; font-weight: 600;")
        self._btn_sort_natural = QToolButton()
        self._btn_sort_natural.setText("🔢")
        self._btn_sort_natural.setToolTip("按文件名自然排序（1,2…10）")
        self._btn_sort_natural.setStyleSheet(
            "QToolButton{background:#313244;border-radius:4px;padding:2px 6px;"
            "color:#cdd6f4;font-size:11px;border:1px solid #45475a;}"
            "QToolButton:hover{background:#45475a;}"
        )
        self._btn_sort_natural.clicked.connect(self._sort_list_natural)
        list_header.addWidget(list_label)
        list_header.addStretch()
        list_header.addWidget(self._btn_sort_natural)

        # 文件列表：开启内部拖拽重排
        self._file_list = QListWidget()
        self._file_list.setToolTip("拖拽条目可调整处理顺序；绿色 = 已识别\nDelete 键快速移除选中项")
        self._file_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._file_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._file_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        # 拖拽完成后同步内存数据
        self._file_list.model().rowsMoved.connect(self._on_list_reordered)

        _btn_style_gray = (
            "QToolButton{background:#313244;border-radius:4px;padding:3px 8px;"
            "color:#cdd6f4;font-size:11px;border:1px solid #45475a;}"
            "QToolButton:hover{background:#45475a;}"
        )
        _btn_style_red = (
            "QToolButton{background:#3d1a22;border-radius:4px;padding:3px 8px;"
            "color:#f38ba8;font-size:11px;border:1px solid #5a2030;}"
            "QToolButton:hover{background:#5a2030;}"
        )

        # 移动按钮行（上移 / 下移 / 删除）
        move_layout = QHBoxLayout()
        move_layout.setSpacing(4)
        self._btn_move_up = QToolButton()
        self._btn_move_up.setText("▲ 上移")
        self._btn_move_up.setToolTip("将当前条目上移一位")
        self._btn_move_up.setStyleSheet(_btn_style_gray)
        self._btn_move_up.clicked.connect(self._move_current_up)

        self._btn_move_down = QToolButton()
        self._btn_move_down.setText("▼ 下移")
        self._btn_move_down.setToolTip("将当前条目下移一位")
        self._btn_move_down.setStyleSheet(_btn_style_gray)
        self._btn_move_down.clicked.connect(self._move_current_down)

        self._btn_remove = QToolButton()
        self._btn_remove.setText("✕ 移除")
        self._btn_remove.setToolTip("从队列中移除当前图片（不删除磁盘文件，Del 键）")
        self._btn_remove.setStyleSheet(_btn_style_red)
        self._btn_remove.clicked.connect(self._remove_current)

        move_layout.addWidget(self._btn_move_up, 1)
        move_layout.addWidget(self._btn_move_down, 1)
        move_layout.addWidget(self._btn_remove, 1)

        # 导航按钮
        nav_layout = QHBoxLayout()
        self._btn_prev = QPushButton("◀ 上一张")
        self._btn_next = QPushButton("下一张 ▶")
        nav_layout.addWidget(self._btn_prev)
        nav_layout.addWidget(self._btn_next)

        left_layout.addLayout(list_header)
        left_layout.addWidget(self._file_list, 1)
        left_layout.addLayout(move_layout)
        left_layout.addLayout(nav_layout)

        # ── 中间：图片查看器面板 ─────────────────────────────────────────────
        viewer_panel = QWidget()
        viewer_layout = QVBoxLayout(viewer_panel)
        viewer_layout.setContentsMargins(4, 8, 4, 8)
        viewer_layout.setSpacing(4)

        # 查看器工具栏
        viewer_toolbar = QHBoxLayout()
        self._lbl_filename = QLabel("未选择图片")
        self._lbl_filename.setStyleSheet("color: #89b4fa; font-size: 12px;")
        self._lbl_zoom = QLabel("100%")
        self._lbl_zoom.setStyleSheet("color: #6c7086; font-size: 11px; min-width: 45px;")

        btn_zoom_in = QToolButton()
        btn_zoom_in.setText("🔍+")
        btn_zoom_in.setToolTip("放大 (Ctrl+=)")
        btn_zoom_in.clicked.connect(lambda: self._viewer.zoom_in())

        btn_zoom_out = QToolButton()
        btn_zoom_out.setText("🔍-")
        btn_zoom_out.setToolTip("缩小 (Ctrl+-)")
        btn_zoom_out.clicked.connect(lambda: self._viewer.zoom_out())

        btn_zoom_fit = QToolButton()
        btn_zoom_fit.setText("⊡ 适应")
        btn_zoom_fit.setToolTip("适应窗口 (双击图片)")
        btn_zoom_fit.clicked.connect(lambda: self._viewer.fit_in_view())

        for w in [self._lbl_filename, None, btn_zoom_out, self._lbl_zoom, btn_zoom_in, btn_zoom_fit]:
            if w is None:
                viewer_toolbar.addStretch()
            else:
                viewer_toolbar.addWidget(w)

        self._viewer = ZoomableImageViewer()
        self._viewer.zoom_changed.connect(
            lambda z: self._lbl_zoom.setText(f"{z*100:.0f}%")
        )

        viewer_layout.addLayout(viewer_toolbar)
        viewer_layout.addWidget(self._viewer, 1)

        # ── 右侧：文本编辑面板 ───────────────────────────────────────────────
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 8, 8, 8)
        right_layout.setSpacing(6)

        right_header = QHBoxLayout()
        text_label = QLabel("✏️ OCR 识别文本（可直接编辑校对）")
        text_label.setStyleSheet("color: #89b4fa; font-size: 12px; font-weight: 600;")
        self._btn_ocr = QPushButton("🔍 识别当前图片")
        self._btn_save_next = QPushButton("💾 保存并下一张")
        self._btn_save_next.setObjectName("success")
        right_header.addWidget(text_label)
        right_header.addStretch()
        right_header.addWidget(self._btn_ocr)
        right_header.addWidget(self._btn_save_next)

        self._text_editor = QTextEdit()
        self._text_editor.setPlaceholderText(
            "点击「识别当前图片」后，OCR 结果将显示在此处。\n"
            "您可以在此直接对照左侧图片进行手动修改校对。\n\n"
            "快捷键提示：\n"
            "  Ctrl+= / Ctrl+-  放大 / 缩小图片\n"
            "  双击图片         恢复适应视口\n"
            "  拖动图片         平移查看"
        )
        self._text_editor.setAcceptRichText(False)  # 纯文本模式

        # 字符统计
        self._lbl_char_count = QLabel("字符数：0")
        self._lbl_char_count.setStyleSheet("color: #6c7086; font-size: 11px;")
        self._text_editor.textChanged.connect(
            lambda: self._lbl_char_count.setText(
                f"字符数：{len(self._text_editor.toPlainText())}"
            )
        )

        right_layout.addLayout(right_header)
        right_layout.addWidget(self._text_editor, 1)
        right_layout.addWidget(self._lbl_char_count, 0, Qt.AlignmentFlag.AlignRight)

        # 组装主分割器
        self._main_splitter.addWidget(left_panel)
        self._main_splitter.addWidget(viewer_panel)
        self._main_splitter.addWidget(right_panel)
        self._main_splitter.setSizes([220, 650, 530])
        self._main_splitter.setHandleWidth(3)

        # ── 日志控制台 ───────────────────────────────────────────────────────
        self._log_console = LogConsole()
        main_layout.addWidget(self._log_console)

        # ── 状态栏 ───────────────────────────────────────────────────────────
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._lbl_status = QLabel("就绪  |  请先导入图片")
        self._statusbar.addWidget(self._lbl_status)
        self._lbl_page = QLabel("")
        self._statusbar.addPermanentWidget(self._lbl_page)
        # 自动保存时间戳（右下角）
        self._lbl_autosave = QLabel("")
        self._lbl_autosave.setStyleSheet("color: #45475a; font-size: 11px; margin-right: 8px;")
        self._statusbar.addPermanentWidget(self._lbl_autosave)

    def _connect_signals(self):
        self._act_open_files.triggered.connect(self._open_files)
        self._act_open_folder.triggered.connect(self._open_folder)
        self._act_settings.triggered.connect(self._open_settings)
        self._act_batch_ocr.triggered.connect(self._start_batch_ocr)
        self._act_stop_batch.triggered.connect(self._stop_batch_ocr)
        self._act_merge.triggered.connect(self._merge_outputs)
        self._act_save_session.triggered.connect(self._session_save_manual)
        self._act_restore_session.triggered.connect(self._offer_restore_session)
        self._act_clear_session.triggered.connect(self._clear_session)

        self._file_list.currentRowChanged.connect(self._on_list_selection_changed)
        self._btn_prev.clicked.connect(self._go_prev)
        self._btn_next.clicked.connect(self._go_next)
        self._btn_ocr.clicked.connect(self._start_single_ocr)
        self._btn_save_next.clicked.connect(self._save_and_next)

        # 编辑器内容变化 → 标记当前页为草稿（抑制频繁触发，用 500ms 防抖）
        self._draft_timer = QTimer(self)
        self._draft_timer.setSingleShot(True)
        self._draft_timer.setInterval(500)
        self._draft_timer.timeout.connect(self._flush_draft)
        self._text_editor.textChanged.connect(self._draft_timer.start)

        # Delete / Backspace 键在文件列表中触发移除
        from PyQt6.QtGui import QKeySequence, QShortcut
        for seq in ("Delete", "Backspace"):
            sc = QShortcut(QKeySequence(seq), self._file_list)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(self._remove_current)

    # ── 配置持久化 ───────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        defaults = {
            # API 连接
            "api_key":       "",
            "base_url":      "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model":         "qwen-vl-ocr-latest",
            "output_dir":    str(Path.home() / "diary_ocr_output"),
            # 模型参数
            "temperature":   0.1,
            "max_tokens":    4096,
            "max_workers":   3,
            # 提示词
            "system_prompt": "",
            "user_prompt":   DEFAULT_PROMPT,
        }
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                defaults.update(saved)
            except Exception:
                pass
        return defaults

    def _save_config(self):
        try:
            CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log(f"⚠️ 保存配置失败: {e}")

    # ── 文件加载 ─────────────────────────────────────────────────────────────

    def _open_files(self):
        if self._batch_ui_running:
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择日记照片", "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp)"
        )
        if paths:
            self._load_images(sorted(paths, key=self._natural_key))

    def _open_folder(self):
        if self._batch_ui_running:
            return
        folder = QFileDialog.getExistingDirectory(self, "选择包含日记照片的文件夹")
        if folder:
            paths = sorted([
                str(p) for p in Path(folder).iterdir()
                if p.suffix.lower() in self.SUPPORTED_EXTS
            ], key=self._natural_key)
            if not paths:
                QMessageBox.information(self, "提示", "所选文件夹中没有支持的图片文件。")
                return
            self._load_images(paths)

    def _load_images(self, paths: list[str], start_index: int = 0,
                     restore_done: set | None = None,
                     restore_texts: dict | None = None):
        """
        加载图片列表并初始化工作区。

        参数：
          paths         : 图片路径列表（已排序）
          start_index   : 初始定位到第几张（会话恢复时使用）
          restore_done  : 恢复的已完成索引集合
          restore_texts : 恢复的草稿文本字典
        """
        self._image_paths = paths
        self._current_index = -1
        self._ocr_texts.clear()
        self._done_indices = set(restore_done) if restore_done else set()
        self._file_list.clear()

        for p in paths:
            item = QListWidgetItem(Path(p).name)
            item.setToolTip(p)
            self._file_list.addItem(item)

        # 恢复草稿文本到内存
        if restore_texts:
            self._ocr_texts.update(restore_texts)

        self._log(f"📂 已加载 {len(paths)} 张图片")

        # 恢复已完成状态的列表颜色
        for idx in self._done_indices:
            self._mark_item_done(idx)

        self._set_current(max(0, min(start_index, len(paths) - 1)))
        self._update_buttons()

        # 启动自动保存定时器
        self._auto_save_timer.start()
        # 立即写一次会话（记录本次打开的文件列表）
        self._session_save_silent()

    # ── 图片切换 ─────────────────────────────────────────────────────────────

    def _set_current(self, index: int):
        if not self._image_paths or index < 0 or index >= len(self._image_paths):
            return

        # 保存当前编辑内容到内存
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()

        self._current_index = index
        path = self._image_paths[index]

        # 更新列表选中
        self._file_list.blockSignals(True)
        self._file_list.setCurrentRow(index)
        self._file_list.blockSignals(False)

        # 后台解码限尺寸预览，避免大图切换阻塞主线程。
        self._load_preview(path)
        self._lbl_filename.setText(Path(path).name)

        # 恢复已保存/识别的文本
        self._text_editor.setPlainText(self._ocr_texts.get(index, ""))

        # 尝试从 .md 文件读取
        if index not in self._ocr_texts:
            self._try_load_md(index, path)

        # 更新状态
        total = len(self._image_paths)
        self._lbl_page.setText(f"第 {index+1} / {total} 张")
        self._set_status(f"当前：{Path(path).name}")
        self._update_buttons()

    def _load_preview(self, path: str):
        worker = PreviewLoadWorker(path)
        self._preview_workers.add(worker)
        worker.loaded.connect(self._on_preview_loaded)
        worker.error.connect(
            lambda failed_path, message: self._log(
                f"⚠️ 预览加载失败 {Path(failed_path).name}：{message}"
            )
        )
        worker.finished.connect(
            lambda current_worker=worker: self._preview_workers.discard(current_worker)
        )
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_preview_loaded(self, path: str, image: QImage):
        if (
            0 <= self._current_index < len(self._image_paths)
            and _canonical_path(self._image_paths[self._current_index])
            == _canonical_path(path)
        ):
            self._viewer.load_qimage(image)

    def _try_load_md(self, index: int, path: str):
        """尝试从同名 .md 文件加载已保存文本"""
        output_dir = self._config.get("output_dir", "")
        md_path = _existing_output_path(path, output_dir, self._image_paths)
        if md_path.exists():
            try:
                text = md_path.read_text(encoding="utf-8")
                self._ocr_texts[index] = text
                self._text_editor.setPlainText(text)
                self._done_indices.add(index)
                self._mark_item_done(index)
                self._log(f"📖 已从缓存加载：{md_path.name}")
            except Exception:
                pass

    def _on_list_selection_changed(self, row: int):
        if row != self._current_index:
            self._set_current(row)

    # ── 列表顺序管理 ─────────────────────────────────────────────────────────

    def _rebuild_order_from_list(self):
        """
        拖拽或移动按钮操作后，用列表 widget 当前排列顺序重建内存数据。

        策略：
          1. 从每个 QListWidgetItem.toolTip() 读出绝对路径，重建 _image_paths。
          2. 建立 old_index → new_index 映射，据此重映射 _ocr_texts / _done_indices。
          3. 更新 _current_index 至当前选中行。
          4. 刷新所有条目的颜色（已完成 → 绿色）。
        """
        n = self._file_list.count()
        new_paths = [self._file_list.item(i).toolTip() for i in range(n)]

        # 建立 path → old_index 反查表
        old_index_of = {p: i for i, p in enumerate(self._image_paths)}

        # 重映射 _ocr_texts
        new_ocr_texts: dict[int, str] = {}
        for new_i, path in enumerate(new_paths):
            old_i = old_index_of.get(path)
            if old_i is not None and old_i in self._ocr_texts:
                new_ocr_texts[new_i] = self._ocr_texts[old_i]

        # 重映射 _done_indices
        new_done: set[int] = set()
        for new_i, path in enumerate(new_paths):
            old_i = old_index_of.get(path)
            if old_i is not None and old_i in self._done_indices:
                new_done.add(new_i)

        self._image_paths  = new_paths
        self._ocr_texts    = new_ocr_texts
        self._done_indices = new_done

        # 同步 _current_index 为当前选中行
        row = self._file_list.currentRow()
        self._current_index = row if row >= 0 else (0 if n > 0 else -1)

        # 刷新列表颜色
        for i in range(n):
            item = self._file_list.item(i)
            item.setForeground(
                QColor("#a6e3a1") if i in self._done_indices
                else QColor("#cdd6f4")
            )

        total = len(self._image_paths)
        self._lbl_page.setText(
            f"第 {self._current_index + 1} / {total} 张"
            if self._current_index >= 0 else ""
        )
        self._log(f"🔀 顺序已更新，当前第 {self._current_index + 1} / {total} 张")
        self._session_save_silent()

    def _on_list_reordered(self, _parent, start, end, _dest, dest_row):
        """rowsMoved 信号槽：拖拽完成后同步内存。"""
        if self._batch_ui_running:
            return
        self._rebuild_order_from_list()

    def _move_current_up(self):
        """将当前选中条目上移一位。"""
        if self._batch_ui_running:
            return
        row = self._file_list.currentRow()
        if row <= 0:
            return
        self._file_list.model().rowsMoved.disconnect(self._on_list_reordered)
        item = self._file_list.takeItem(row)
        self._file_list.insertItem(row - 1, item)
        self._file_list.setCurrentRow(row - 1)
        self._file_list.model().rowsMoved.connect(self._on_list_reordered)
        self._rebuild_order_from_list()

    def _move_current_down(self):
        """将当前选中条目下移一位。"""
        if self._batch_ui_running:
            return
        row = self._file_list.currentRow()
        if row < 0 or row >= self._file_list.count() - 1:
            return
        self._file_list.model().rowsMoved.disconnect(self._on_list_reordered)
        item = self._file_list.takeItem(row)
        self._file_list.insertItem(row + 1, item)
        self._file_list.setCurrentRow(row + 1)
        self._file_list.model().rowsMoved.connect(self._on_list_reordered)
        self._rebuild_order_from_list()

    def _sort_list_natural(self):
        """按文件名自然排序重排列表（1,2,…,10 顺序）。"""
        if self._batch_ui_running:
            return
        if not self._image_paths:
            return
        # 先将当前编辑器内容写入内存，防止丢失
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()

        current_path = (
            self._image_paths[self._current_index]
            if self._current_index >= 0 else None
        )

        # 暂断信号，避免排序中途多次触发 rowsMoved
        self._file_list.model().rowsMoved.disconnect(self._on_list_reordered)

        # 取出所有条目，按自然排序重建
        items_data = []
        for i in range(self._file_list.count()):
            item = self._file_list.item(i)
            items_data.append((item.text(), item.toolTip(),
                                item.foreground().color()))
        items_data.sort(key=lambda x: self._natural_key(x[1]))

        self._file_list.clear()
        for name, tip, color in items_data:
            new_item = QListWidgetItem(name)
            new_item.setToolTip(tip)
            new_item.setForeground(color)
            self._file_list.addItem(new_item)

        self._file_list.model().rowsMoved.connect(self._on_list_reordered)
        self._rebuild_order_from_list()

        # 恢复选中当前图片
        if current_path and current_path in self._image_paths:
            new_idx = self._image_paths.index(current_path)
            self._file_list.blockSignals(True)
            self._file_list.setCurrentRow(new_idx)
            self._file_list.blockSignals(False)
            self._current_index = new_idx

        self._log("🔢 已按自然顺序排序（1,2,…,10）")

    def _remove_current(self):
        """
        从处理队列中移除当前选中的图片条目。

        行为说明：
          - 只从内存队列和列表 UI 中删除，不影响磁盘上的图片文件或已生成的 .md 文件。
          - 删除后自动选中相邻条目（优先下一张，末尾时选上一张）。
          - 若队列清空，清空查看器和编辑器。
          - 支持 Delete 键触发（在 _file_list 的 keyPressEvent 中绑定）。
        """
        if self._batch_ui_running:
            return
        row = self._file_list.currentRow()
        if row < 0 or not self._image_paths:
            return

        path = self._image_paths[row]
        name = Path(path).name

        # 无需弹框确认（操作轻量，不删磁盘文件），直接执行
        # 暂断信号防止 rowsMoved 干扰
        self._file_list.model().rowsMoved.disconnect(self._on_list_reordered)
        self._file_list.takeItem(row)
        self._file_list.model().rowsMoved.connect(self._on_list_reordered)

        # 从内存中移除，并将后续 index 全部前移 1
        del self._image_paths[row]

        # 重建 _ocr_texts（key 大于 row 的全部 -1）
        new_ocr: dict[int, str] = {}
        for idx, text in self._ocr_texts.items():
            if idx == row:
                pass            # 丢弃被删除项
            elif idx > row:
                new_ocr[idx - 1] = text
            else:
                new_ocr[idx] = text
        self._ocr_texts = new_ocr

        # 重建 _done_indices
        new_done: set[int] = set()
        for idx in self._done_indices:
            if idx == row:
                pass
            elif idx > row:
                new_done.add(idx - 1)
            else:
                new_done.add(idx)
        self._done_indices = new_done

        total = len(self._image_paths)
        self._log(f"🗑 已从队列移除：{name}（剩余 {total} 张，磁盘文件未删除）")

        if total == 0:
            # 队列已空
            self._current_index = -1
            self._viewer.clear_image()
            self._text_editor.clear()
            self._lbl_filename.setText("未选择图片")
            self._lbl_page.setText("")
            self._set_status("队列已清空，请重新导入图片")
            self._update_buttons()
            self._session.clear()
            self._auto_save_timer.stop()
            self._lbl_autosave.setText("")
            return

        # 选中相邻条目：优先保持 row，超出则退一格
        new_row = min(row, total - 1)
        # 不触发 _on_list_selection_changed，直接调用 _set_current
        self._current_index = -1          # 强制让 _set_current 刷新
        self._set_current(new_row)
        self._update_buttons()
        self._session_save_silent()

    def _go_prev(self):
        if self._current_index > 0:
            self._set_current(self._current_index - 1)

    def _go_next(self):
        if self._current_index < len(self._image_paths) - 1:
            self._set_current(self._current_index + 1)

    # ── 单张 OCR ─────────────────────────────────────────────────────────────

    def _start_single_ocr(self):
        if self._current_index < 0:
            QMessageBox.warning(self, "提示", "请先选择一张图片。")
            return
        if not self._config.get("api_key"):
            QMessageBox.warning(self, "缺少 API Key", "请先在「⚙️ 设置」中配置 API Key。")
            return
        if self._ocr_worker and self._ocr_worker.isRunning():
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return

        self._btn_ocr.setEnabled(False)
        self._btn_ocr.setText("识别中…")
        self._set_status("正在压缩图片并调用 OCR API…")

        client = self._build_api_client()
        path = self._image_paths[self._current_index]
        self._ocr_worker = OCRWorker(path, client)
        self._ocr_worker.compress_done.connect(self._on_compress_done)
        self._ocr_worker.ocr_done.connect(self._on_ocr_done)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._ocr_worker.finished.connect(self._on_ocr_worker_finished)
        self._ocr_worker.start()
        self._update_buttons()

    def _on_compress_done(self, info: dict):
        self._log(
            f"🗜️ 压缩完成：{info['original_size_human']} → {info['final_size_human']} "
            f"| 分辨率 {info['original_resolution']} → {info['scaled_resolution']} "
            f"| quality={info['final_quality']} "
            f"| {'✅ 合规' if info['compliant'] else '⚠️ 超限'}"
        )

    def _on_ocr_done(self, path: str, text: str):
        target_key = _canonical_path(path)
        index = next(
            (
                idx for idx, candidate in enumerate(self._image_paths)
                if _canonical_path(candidate) == target_key
            ),
            None,
        )
        if index is None:
            self._log(f"⚠️ OCR 已完成，但图片已从队列移除：{Path(path).name}")
            return
        self._ocr_texts[index] = text
        self._mark_item_done(index)
        if index == self._current_index:
            self._text_editor.setPlainText(text)
            self._set_status("✅ 识别完成，请在右侧校对文本后点击「保存并下一张」")
        else:
            self._set_status(f"✅ {Path(path).name} 识别完成")
        self._log(f"✅ OCR 成功，识别字符数：{len(text)}")
        # 识别结果立即写入会话，防止崩溃丢失
        self._session_save_silent()

    def _on_ocr_error(self, msg: str):
        self._log(f"❌ OCR 失败：{msg}")
        QMessageBox.critical(self, "OCR 失败", f"调用 API 时出错：\n\n{msg[:500]}")
        self._set_status("❌ OCR 失败")

    def _on_ocr_worker_finished(self):
        self._btn_ocr.setText("🔍 识别当前图片")
        if self._ocr_worker:
            self._ocr_worker.deleteLater()
            self._ocr_worker = None
        self._update_buttons()

    # ── 保存并切换 ───────────────────────────────────────────────────────────

    def _save_and_next(self):
        if self._current_index < 0:
            return
        text = self._text_editor.toPlainText().strip()
        path = self._image_paths[self._current_index]
        if self._save_md(self._current_index, path, text):
            self._go_next()
        else:
            self._set_status("❌ 保存失败，已停留在当前页面")
            QMessageBox.warning(self, "保存失败", "无法写入输出文件，请检查目录权限或磁盘空间。")

    def _save_md(self, index: int, image_path: str, text: str) -> str | None:
        output_dir = self._config.get("output_dir", str(Path.home()))
        try:
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            md_path = _output_path_for_image(image_path, output_dir, self._image_paths)
            _write_text_atomic(md_path, text)
            self._ocr_texts[index] = text
            self._done_indices.add(index)
            self._mark_item_done(index)
            self._log(f"💾 已保存：{md_path}")
            # 落盘成功后同步会话（从草稿中移除该页，因为已有 .md 文件）
            self._session_save_silent()
            return str(md_path)
        except Exception as e:
            self._log(f"❌ 保存失败：{e}")
            return None

    # ── 批量 OCR ─────────────────────────────────────────────────────────────

    def _start_batch_ocr(self):
        if not self._image_paths:
            QMessageBox.information(self, "提示", "请先导入图片。")
            return
        if not self._config.get("api_key"):
            QMessageBox.warning(self, "缺少 API Key", "请先在「⚙️ 设置」中配置 API Key。")
            return
        if self._batch_worker and self._batch_worker.isRunning():
            return
        if self._ocr_worker and self._ocr_worker.isRunning():
            QMessageBox.information(self, "请稍候", "当前图片识别完成后再启动批量识别。")
            return

        reply = QMessageBox.question(
            self, "批量识别确认",
            f"将对全部 {len(self._image_paths)} 张图片进行 OCR 识别，\n"
            f"结果自动保存至：\n{self._config.get('output_dir')}\n\n"
            "确认开始？",
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._set_batch_ui_running(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setRange(0, len(self._image_paths))
        self._progress_bar.setValue(0)

        self._batch_worker = BatchOCRWorker(
            list(self._image_paths),
            self._build_api_client(),
            self._config.get("output_dir", ""),
            max_workers=int(self._config.get("max_workers", 3)),
        )
        self._batch_worker.progress.connect(self._on_batch_progress)
        self._batch_worker.item_done.connect(self._on_batch_item_done)
        self._batch_worker.log.connect(self._log)
        self._batch_worker.batch_done.connect(self._on_batch_finished)
        self._batch_worker.finished.connect(self._on_batch_thread_finished)
        self._batch_worker.start()

    def _on_batch_progress(self, current: int, total: int, path: str):
        self._progress_bar.setValue(current)
        self._set_status(f"批量识别：{current}/{total} — {Path(path).name}")

    def _on_batch_item_done(self, index: int, path: str, text: str):
        target_key = _canonical_path(path)
        current_index = next(
            (
                idx for idx, candidate in enumerate(self._image_paths)
                if _canonical_path(candidate) == target_key
            ),
            None,
        )
        if current_index is None:
            return
        self._ocr_texts[current_index] = text
        self._done_indices.add(current_index)
        self._mark_item_done(current_index)
        if current_index == self._current_index:
            self._text_editor.setPlainText(text)
        self._session_save_silent()

    def _on_batch_finished(self, summary: dict):
        self._set_batch_ui_running(False)
        self._progress_bar.setVisible(False)
        succeeded = summary.get("succeeded", 0)
        failed = summary.get("failed", 0)
        total = summary.get("total", 0)
        if summary.get("stopped"):
            self._set_status(f"⚠️ 批量识别已停止：成功 {succeeded}，失败 {failed}")
            self._log(f"⚠️ 批量识别已停止：成功 {succeeded}/{total}，失败 {failed}")
        elif failed:
            self._set_status(f"⚠️ 批量识别完成：成功 {succeeded}，失败 {failed}")
            self._log(f"⚠️ 批量识别完成：成功 {succeeded}/{total}，失败 {failed}")
        else:
            self._set_status(f"✅ 批量识别完成：{succeeded}/{total}")
            self._log(f"✅ 批量识别全部完成：{succeeded}/{total}")
        self._session_save_silent()

    def _stop_batch_ocr(self):
        if self._batch_worker:
            self._batch_worker.stop()
        self._act_stop_batch.setEnabled(False)
        self._set_status("正在停止批量识别，等待当前请求结束…")

    def _on_batch_thread_finished(self):
        if self._batch_worker:
            self._batch_worker.deleteLater()
            self._batch_worker = None

    def _set_batch_ui_running(self, running: bool):
        self._batch_ui_running = running
        self._act_stop_batch.setEnabled(running)
        self._act_open_files.setEnabled(not running)
        self._act_open_folder.setEnabled(not running)
        self._act_settings.setEnabled(not running)
        self._file_list.setDragDropMode(
            QListWidget.DragDropMode.NoDragDrop
            if running else QListWidget.DragDropMode.InternalMove
        )
        self._btn_sort_natural.setEnabled(not running)
        self._btn_move_up.setEnabled(not running)
        self._btn_move_down.setEnabled(not running)
        self._btn_remove.setEnabled(not running and self._current_index >= 0)
        self._update_buttons()

    # ── 合并输出 ─────────────────────────────────────────────────────────────

    def _merge_outputs(self):
        output_dir = self._config.get("output_dir", "")
        if not output_dir or not Path(output_dir).exists():
            QMessageBox.warning(self, "提示", "输出目录不存在，请先保存一些识别结果。")
            return

        entries = []
        missing = []
        for image_path in self._image_paths:
            md_path = _existing_output_path(image_path, output_dir, self._image_paths)
            if md_path.exists():
                entries.append((image_path, md_path))
            else:
                missing.append(Path(image_path).name)

        if not entries:
            QMessageBox.information(self, "提示", "当前队列还没有可合并的识别结果。")
            return

        if missing:
            reply = QMessageBox.question(
                self,
                "部分页面尚未保存",
                f"当前队列有 {len(missing)} 张图片没有对应的 .md 文件。\n\n"
                "是否只合并已有结果？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        final_path = Path(output_dir) / "final_diary_output.md"
        parts = []
        for image_path, md_path in entries:
            try:
                content = md_path.read_text(encoding="utf-8").strip()
            except Exception as e:
                self._log(f"⚠️ 读取 {md_path.name} 失败：{e}")
                continue
            number = len(parts) + 1
            parts.append(f"### 第 {number} 篇  —  {Path(image_path).stem}\n\n{content}")

        merged = "\n\n---\n\n".join(parts)
        try:
            _write_text_atomic(final_path, merged)
            self._log(f"📄 合并完成，共 {len(parts)} 篇 → {final_path}")
            if not missing and len(parts) == len(self._image_paths):
                self._session.clear()
                self._auto_save_timer.stop()
                self._lbl_autosave.setText("")
                self._log("🗑 会话进度已清除（合并完成）")
            QMessageBox.information(
                self, "合并成功",
                f"已将 {len(parts)} 篇日记合并为：\n{final_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "合并失败", str(e))

    # ── 进度会话管理 ─────────────────────────────────────────────────────────

    def _flush_draft(self):
        """防抖回调：将当前编辑器内容同步到 _ocr_texts 内存缓存。"""
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()

    def _session_save_silent(self) -> bool:
        """
        静默保存会话（不弹框、不改变状态栏主文本）。
        draft_texts 只存储"已识别但尚未写盘为 .md"的草稿，减少冗余。
        """
        if not self._image_paths:
            return False
        # 将当前编辑器最新内容计入内存
        self._flush_draft()
        # 草稿 = 内存中有内容 且 尚未写盘（不在 done_indices 中）的页面
        draft_texts = {
            idx: text
            for idx, text in self._ocr_texts.items()
            if idx not in self._done_indices and text.strip()
        }
        ok = self._session.save(
            image_paths=self._image_paths,
            current_index=self._current_index,
            output_dir=self._config.get("output_dir", ""),
            done_indices=self._done_indices,
            draft_texts=draft_texts,
        )
        if ok:
            import datetime
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self._lbl_autosave.setText(f"进度已保存 {ts}")
        return ok

    def _session_autosave(self):
        """定时器槽：静默保存 + 在日志中打点（不打扰用户）。"""
        if not self._image_paths:
            return
        ok = self._session_save_silent()
        if ok:
            done  = len(self._done_indices)
            total = len(self._image_paths)
            self._log(f"🕒 自动保存进度：{done}/{total} 张已完成")

    def _session_save_manual(self):
        """Ctrl+S / 工具栏"💾 保存进度"：用户主动触发，给出明确反馈。"""
        if not self._image_paths:
            self._set_status("⚠️ 尚未加载图片，无进度可保存")
            return
        ok = self._session_save_silent()
        if ok:
            done  = len(self._done_indices)
            total = len(self._image_paths)
            msg = (
                f"进度已保存！\n\n"
                f"  • 已完成（写盘）：{done} / {total} 张\n"
                f"  • 草稿（仅在会话中）：{len(self._ocr_texts) - done} 张\n"
                f"  • 当前位置：第 {self._current_index + 1} 张\n\n"
                f"会话文件：{SESSION_PATH}"
            )
            QMessageBox.information(self, "进度已保存 ✅", msg)
        else:
            QMessageBox.warning(self, "保存失败", f"写入会话文件失败：\n{SESSION_PATH}")

    def _offer_restore_session(self):
        """
        启动时检测上次会话，若有效则询问用户是否恢复。
        使用 QTimer.singleShot 延迟调用，确保主窗口已完全显示。
        """
        data = self._session.load()
        if not data:
            return

        saved_at   = data.get("saved_at", "未知")
        paths      = data.get("image_paths", [])
        current_path = data.get("current_path")
        cur_idx = next(
            (index for index, path in enumerate(paths) if path == current_path),
            0,
        )
        done_set   = data.get("done_paths", set())
        draft_texts = data.get("draft_texts", {})
        output_dir = data.get("output_dir", "")
        done_count = len(done_set)
        draft_count = len(draft_texts)
        total      = len(paths)

        # 计算仍存在的文件数
        existing = sum(1 for p in paths if Path(p).exists())
        missing  = total - existing

        detail = (
            f"上次保存时间：{saved_at}\n\n"
            f"  • 图片总数：{total} 张（{missing} 张文件已移动或删除）\n"
            f"  • 已完成（.md 已写盘）：{done_count} 张\n"
            f"  • 草稿（已识别未保存）：{draft_count} 张\n"
            f"  • 上次位置：第 {cur_idx + 1} 张\n"
            f"  • 输出目录：{output_dir}\n\n"
            "是否从上次中断处继续？"
        )
        reply = QMessageBox.question(
            self,
            "🔄 检测到未完成的进度",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._restore_session(data)

    def _restore_session(self, data: dict):
        """将会话数据恢复到内存并重建 UI 状态。"""
        paths, cur_idx, valid_done, draft_texts = self._session.materialize(data)

        if not paths:
            QMessageBox.warning(self, "恢复失败", "会话中的图片文件均已不存在。")
            return

        cur_idx = min(cur_idx, len(paths) - 1)

        # Restore the output directory before _load_images performs its first save.
        session_output = data.get("output_dir", "")
        if session_output and session_output != self._config.get("output_dir"):
            self._config["output_dir"] = session_output
            self._log(f"📁 输出目录已恢复为：{session_output}")

        self._log(f"📌 正在恢复上次会话（{len(paths)} 张图片）…")
        self._load_images(
            paths,
            start_index=cur_idx,
            restore_done=valid_done,
            restore_texts=draft_texts,
        )
        self._log(
            f"✅ 会话恢复完成：{len(valid_done)} 张已完成，"
            f"{len(draft_texts)} 张草稿已载入，当前第 {cur_idx + 1} 张"
        )

    def _clear_session(self):
        """用户手动清除会话文件。"""
        if not self._session.exists():
            QMessageBox.information(self, "提示", "当前没有保存的进度文件。")
            return
        reply = QMessageBox.question(
            self,
            "确认清除进度",
            f"这将删除会话文件：\n{SESSION_PATH}\n\n"
            "注意：已写盘的 .md 文件不受影响，仅清除中间进度记录。\n\n"
            "确认清除？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._session.clear()
            self._auto_save_timer.stop()
            self._lbl_autosave.setText("")
            self._log("🗑 会话进度已手动清除")

    # ── 设置 ─────────────────────────────────────────────────────────────────

    def _build_api_client(self) -> OCRAPIClient:
        """从当前配置构造 OCRAPIClient，单一工厂，避免到处散落参数。"""
        return OCRAPIClient(
            api_key=self._config["api_key"],
            base_url=self._config.get("base_url", OCRAPIClient.DEFAULT_BASE_URL),
            model=self._config.get("model", OCRAPIClient.DEFAULT_MODEL),
            user_prompt=self._config.get("user_prompt", DEFAULT_PROMPT),
            system_prompt=self._config.get("system_prompt", ""),
            temperature=float(self._config.get("temperature", 0.1)),
            max_tokens=int(self._config.get("max_tokens", 4096)),
        )

    def _open_settings(self):
        if self._batch_ui_running:
            return
        dlg = SettingsDialog(self._config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._config.update(dlg.get_config())
            self._save_config()
            self._log("⚙️ 设置已保存")

    # ── 工具方法 ─────────────────────────────────────────────────────────────

    def _mark_item_done(self, index: int):
        """将文件列表中对应条目标记为绿色（已识别/已保存）"""
        item = self._file_list.item(index)
        if item:
            item.setForeground(QColor("#a6e3a1"))

    def _update_buttons(self):
        has_images  = bool(self._image_paths)
        has_current = self._current_index >= 0
        single_running = bool(self._ocr_worker and self._ocr_worker.isRunning())
        self._btn_ocr.setEnabled(has_current and not single_running and not self._batch_ui_running)
        self._btn_save_next.setEnabled(has_current and not self._batch_ui_running)
        self._btn_prev.setEnabled(has_current and self._current_index > 0)
        self._btn_next.setEnabled(
            has_current and self._current_index < len(self._image_paths) - 1
        )
        self._btn_remove.setEnabled(has_current and not self._batch_ui_running)
        self._btn_move_up.setEnabled(has_current and not self._batch_ui_running)
        self._btn_move_down.setEnabled(has_current and not self._batch_ui_running)
        self._btn_sort_natural.setEnabled(has_images and not self._batch_ui_running)
        self._act_batch_ocr.setEnabled(has_images and not self._batch_ui_running and not single_running)
        self._act_merge.setEnabled(has_images and not self._batch_ui_running)

    def _set_status(self, msg: str):
        self._lbl_status.setText(msg)

    def _log(self, msg: str):
        self._log_console.log(msg)

    # ── 关闭事件 ─────────────────────────────────────────────────────────────

    def closeEvent(self, event):
        for worker in tuple(self._preview_workers):
            if worker.isRunning():
                worker.wait(2000)
        if any(worker.isRunning() for worker in self._preview_workers):
            self._set_status("图片预览仍在加载，请稍后再次关闭窗口…")
            event.ignore()
            return

        running_workers = []
        if self._ocr_worker and self._ocr_worker.isRunning():
            self._ocr_worker.stop()
            running_workers.append("单张 OCR")
        if self._batch_worker and self._batch_worker.isRunning():
            self._batch_worker.stop()
            running_workers.append("批量 OCR")
        if running_workers:
            self._set_status("正在停止后台任务，请稍后再次关闭窗口…")
            QMessageBox.information(
                self,
                "正在停止后台任务",
                f"已请求停止：{'、'.join(running_workers)}。\n"
                "当前网络请求结束后即可安全退出。",
            )
            event.ignore()
            return

        # 将当前编辑内容同步到内存
        if self._current_index >= 0:
            self._ocr_texts[self._current_index] = self._text_editor.toPlainText()

        # 计算未落盘页面数（已识别但未点"保存并下一张"）
        recognized = set(self._ocr_texts.keys())
        unsaved = recognized - self._done_indices
        unsaved_nonempty = [i for i in unsaved if self._ocr_texts.get(i, "").strip()]

        if unsaved_nonempty and self._image_paths:
            reply = QMessageBox.question(
                self,
                "有未保存的页面",
                f"共有 {len(unsaved_nonempty)} 张图片已识别但尚未保存为 .md 文件。\n\n"
                "• 点击「保存进度并退出」：将草稿写入会话文件，下次启动可继续。\n"
                "• 点击「直接退出」：草稿仍在会话中（自动保存），但本次编辑可能丢失。\n"
                "• 点击「取消」：返回程序继续工作。",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Cancel:
                event.ignore()
                return
            if reply == QMessageBox.StandardButton.Save:
                self._session_save_silent()

        # 最终保存会话 + 配置
        self._session_save_silent()
        self._save_config()
        self._auto_save_timer.stop()
        event.accept()


# ─────────────────────────────────────────────────────────────────────────────
#  程序入口
# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = QApplication(sys.argv)
    app.setApplicationName("历史日记 OCR 数字化助手")
    app.setOrganizationName("DiaryOCR")
    app.setStyleSheet(APP_STYLE)

    # 全局字体
    font = QFont()
    font.setFamily("PingFang SC")
    font.setPointSize(13)
    app.setFont(font)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
