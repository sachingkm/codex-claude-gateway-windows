"""Encode Codex compaction summaries with current-user Windows DPAPI."""
from __future__ import annotations

import base64
import ctypes
import json
from ctypes import wintypes

PREFIX = "gwcmp1."


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
crypt32.CryptProtectData.argtypes = [
    ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
]
crypt32.CryptProtectData.restype = wintypes.BOOL
crypt32.CryptUnprotectData.argtypes = [
    ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DataBlob),
]
crypt32.CryptUnprotectData.restype = wintypes.BOOL
kernel32.LocalFree.argtypes = [ctypes.c_void_p]
kernel32.LocalFree.restype = ctypes.c_void_p


def _blob(data: bytes) -> tuple[DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _protect(data: bytes) -> bytes:
    source, buffer = _blob(data)
    result = DataBlob()
    if not crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def _unprotect(data: bytes) -> bytes:
    source, buffer = _blob(data)
    result = DataBlob()
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)


def encode_summary(summary: str) -> str:
    if not summary.strip():
        raise ValueError("Cannot compact an empty summary")
    payload = json.dumps({"v": 1, "summary": summary}, ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(_protect(payload)).decode("ascii").rstrip("=")


def decode_summary(value: str) -> str:
    if not value.startswith(PREFIX) or len(value) > 200_000:
        raise ValueError("Compaction content is not owned by this gateway")
    encoded = value[len(PREFIX):]
    data = base64.urlsafe_b64decode(encoded + "=" * ((-len(encoded)) % 4))
    payload = json.loads(_unprotect(data).decode("utf-8"))
    if payload.get("v") != 1 or not isinstance(payload.get("summary"), str):
        raise ValueError("Unsupported compaction format")
    return payload["summary"]
