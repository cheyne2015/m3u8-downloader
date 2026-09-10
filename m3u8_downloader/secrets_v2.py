"""使用 Windows 当前用户的数据保护接口保存敏感配置。"""

import base64
import ctypes
from ctypes import wintypes


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return value, buffer


def protect_secret(value: str) -> str:
    if not value:
        return ""
    source, source_buffer = _blob(value.encode("utf-8"))
    output = _DataBlob()
    result = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "m3u8-downloader-v2", None, None, None, 0x1,
        ctypes.byref(output),
    )
    _ = source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return "dpapi:" + base64.b64encode(encrypted).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def unprotect_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith("dpapi:"):
        raise ValueError("敏感信息格式无效")
    encrypted = base64.b64decode(value[6:], validate=True)
    source, source_buffer = _blob(encrypted)
    output = _DataBlob()
    result = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    )
    _ = source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
