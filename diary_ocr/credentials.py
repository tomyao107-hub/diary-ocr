"""API key storage via Windows Credential Manager when available (v1.4)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

SERVICE_NAME = "DiaryOCR"
TARGET_NAME = "DiaryOCR/api_key"


def _config_path() -> Path:
    return Path.home() / ".diary_ocr_config.json"


def _windows_cred_write(secret: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", wintypes.LPVOID),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    CredWriteW = advapi32.CredWriteW
    CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    CredWriteW.restype = wintypes.BOOL

    blob = secret.encode("utf-16-le")
    blob_buf = ctypes.create_string_buffer(blob)
    cred = CREDENTIAL()
    cred.Type = 1  # CRED_TYPE_GENERIC
    cred.TargetName = TARGET_NAME
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buf, ctypes.POINTER(ctypes.c_char))
    cred.Persist = 2  # CRED_PERSIST_LOCAL_MACHINE → use ENTERPRISE(3) or LOCAL_MACHINE(2)
    # CRED_PERSIST_LOCAL_MACHINE = 2, LOCAL_MACHINE may need admin; use ENTERPRISE=3 or SESSION.
    # CRED_PERSIST_ENTERPRISE = 3 is commonly used for roaming; LOCAL_MACHINE=2 for machine.
    # Prefer CRED_PERSIST_LOCAL_MACHINE=2; if fails try ENTERPRISE=3.
    cred.UserName = SERVICE_NAME
    if not CredWriteW(ctypes.byref(cred), 0):
        cred.Persist = 3
        if not CredWriteW(ctypes.byref(cred), 0):
            return False
    return True


def _windows_cred_read() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return None

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", wintypes.LPVOID),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    CredReadW = advapi32.CredReadW
    CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
    ]
    CredReadW.restype = wintypes.BOOL
    CredFree = advapi32.CredFree
    CredFree.argtypes = [wintypes.LPVOID]

    ptr = ctypes.POINTER(CREDENTIAL)()
    if not CredReadW(TARGET_NAME, 1, 0, ctypes.byref(ptr)):
        return None
    try:
        size = ptr.contents.CredentialBlobSize
        raw = ctypes.string_at(ptr.contents.CredentialBlob, size)
        return raw.decode("utf-16-le")
    finally:
        CredFree(ptr)


def _windows_cred_delete() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    CredDeleteW = advapi32.CredDeleteW
    CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    CredDeleteW.restype = wintypes.BOOL
    return bool(CredDeleteW(TARGET_NAME, 1, 0))


def save_api_key(api_key: str) -> str:
    """
    Store API key securely when possible.
    Returns storage backend: 'windows-credential-manager' | 'config-file'.
    """
    api_key = api_key or ""
    if api_key and _windows_cred_write(api_key):
        # Remove plaintext key from config if present.
        path = _config_path()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "api_key" in data:
                    data["api_key"] = ""
                    data["api_key_storage"] = "windows-credential-manager"
                    temporary = path.with_suffix(path.suffix + ".tmp")
                    temporary.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    temporary.replace(path)
            except (OSError, json.JSONDecodeError):
                pass
        return "windows-credential-manager"
    return "config-file"


def load_api_key(config: dict | None = None) -> str:
    """Load API key from credential manager first, then config fallback."""
    secret = _windows_cred_read()
    if secret:
        return secret
    if config is not None:
        return str(config.get("api_key") or "")
    path = _config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return str(data.get("api_key") or "")
        except (OSError, json.JSONDecodeError):
            pass
    return ""


def delete_stored_api_key() -> None:
    _windows_cred_delete()


def merge_api_key_into_config(config: dict) -> dict:
    """Ensure config dict has runtime api_key from secure storage if empty."""
    merged = dict(config)
    if not merged.get("api_key"):
        key = load_api_key(merged)
        if key:
            merged["api_key"] = key
            merged["api_key_storage"] = "windows-credential-manager"
    return merged
