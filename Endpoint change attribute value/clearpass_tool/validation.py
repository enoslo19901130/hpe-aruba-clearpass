"""提供 GUI 輸入值的正規化與白名單驗證。"""

from __future__ import annotations

import re
from collections.abc import Iterable


class ValidationError(ValueError):
    """表示使用者輸入無法安全地送往 ClearPass API。"""

    pass


def normalize_mac(raw_value: object) -> str:
    """接受常見 MAC 格式並輸出 ClearPass 使用的 AA:BB:CC:DD:EE:FF。"""
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ValidationError("請輸入 MAC Address")

    compact = re.sub(r"[.\-:\s]", "", raw_value)
    if not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        raise ValidationError("MAC Address 格式不正確，例如 AA:BB:CC:DD:EE:FF")

    compact = compact.upper()
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2))


def validate_attribute_name(raw_value: object) -> str:
    """驗證可編輯 attribute 名稱，拒絕空值、控制字元及危險保留字。"""
    if not isinstance(raw_value, str):
        raise ValidationError("請選擇或輸入 attribute 名稱")

    value = raw_value.strip()
    if not value:
        raise ValidationError("請選擇或輸入 attribute 名稱")
    if len(value) > 128:
        raise ValidationError("attribute 名稱不可超過 128 個字元")
    if any(ord(character) < 32 for character in value):
        raise ValidationError("attribute 名稱不可包含控制字元")
    if value in {"__proto__", "prototype", "constructor"}:
        raise ValidationError("不允許此 attribute 名稱")
    return value


def validate_attribute_value(raw_value: object, allowed_values: Iterable[str]) -> str:
    """只允許送出設定中下拉選單列出的 attribute 值。"""
    if not isinstance(raw_value, str):
        raise ValidationError("請選擇 attribute 值")

    allowed = tuple(allowed_values)
    if raw_value not in allowed:
        raise ValidationError(f"attribute 值只允許：{', '.join(allowed)}")
    return raw_value
