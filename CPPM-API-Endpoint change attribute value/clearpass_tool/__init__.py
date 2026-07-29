"""提供桌面應用程式共用的 ClearPass API、設定及錯誤型別。"""

from .client import ClearPassClient, ClearPassError, ClearPassResponseError
from .config import ConfigurationError, Settings

# 明確限制套件公開介面，GUI 不需依賴各模組的私有實作細節。
__all__ = [
    "ClearPassClient",
    "ClearPassError",
    "ClearPassResponseError",
    "ConfigurationError",
    "Settings",
]
