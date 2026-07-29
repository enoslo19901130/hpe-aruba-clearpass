"""讀取、正規化並驗證 ClearPass 連線與工具預設設定。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


# UI 可選業務值與環境預設值集中於此，後續需求變更只需調整一處。
ATTRIBUTE_VALUES = ("[ UNKNOWN ]", "[ HIGH RISK ]")
DEFAULT_ATTRIBUTE_NAME = "[ FS STATUS ]"
DEFAULT_ATTRIBUTE_VALUE = "[ HIGH RISK ]"
DEFAULT_CLEARPASS_URL = "https://172.17.20.181"
DEFAULT_CLIENT_ID = "Enos-TEST-API"


class ConfigurationError(RuntimeError):
    """表示環境變數或 GUI 連線設定不完整或不安全。"""

    pass


def load_env_file(path: str | Path = ".env") -> None:
    """載入簡易 .env，且不覆蓋作業系統已設定的同名環境變數。"""
    env_path = Path(path)
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        # 正式環境注入的環境變數優先於開發機 .env。
        os.environ.setdefault(key, value)


def _boolean(name: str, default: bool) -> bool:
    """解析常見布林環境變數文字，格式不符時明確拒絕。"""
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} 必須是 true 或 false")


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    """解析具有上下限的整數環境變數。"""
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ConfigurationError(f"{name} 必須是整數") from exc

    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} 必須介於 {minimum} 和 {maximum} 之間")
    return value


def _base_url(raw_value: str) -> str:
    """正規化 ClearPass URL，並禁止向遠端純 HTTP 傳送 OAuth 憑證。"""
    value = raw_value.strip().rstrip("/")
    if "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError("CLEARPASS_BASE_URL 必須是有效的 HTTP(S) URL")
    try:
        parsed.port
    except ValueError as exc:
        raise ConfigurationError("CLEARPASS_BASE_URL 包含無效的 port") from exc
    if (
        parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ConfigurationError("CLEARPASS_BASE_URL 只能包含 scheme、host 與可選的 port")
    if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("ClearPass OAuth 憑證不可透過未加密 HTTP 傳送，請使用 HTTPS")
    return value


def _ca_bundle_path(raw_value: str) -> str:
    """將選填 CA bundle 路徑展開成絕對路徑，空值則維持停用。"""
    value = raw_value.strip()
    if not value:
        return ""
    return str(Path(value).expanduser().resolve(strict=False))


@dataclass(frozen=True, slots=True)
class Settings:
    """集中保存 API、TLS、逾時及 attribute 下拉選單設定。"""

    clearpass_base_url: str = DEFAULT_CLEARPASS_URL
    clearpass_client_id: str = DEFAULT_CLIENT_ID
    clearpass_client_secret: str = ""
    clearpass_verify_tls: bool = True
    clearpass_ca_bundle: str = ""
    clearpass_timeout_seconds: int = 15
    default_attribute_name: str = DEFAULT_ATTRIBUTE_NAME
    attribute_values: tuple[str, ...] = ATTRIBUTE_VALUES
    default_attribute_value: str = DEFAULT_ATTRIBUTE_VALUE

    @classmethod
    def from_environment(cls) -> "Settings":
        """由 .env 與環境變數建立已正規化的啟動設定。"""
        load_env_file()
        return cls(
            clearpass_base_url=_base_url(
                os.getenv("CLEARPASS_BASE_URL", DEFAULT_CLEARPASS_URL)
            ),
            clearpass_client_id=os.getenv("CLEARPASS_CLIENT_ID", DEFAULT_CLIENT_ID).strip(),
            clearpass_client_secret=os.getenv("CLEARPASS_CLIENT_SECRET", ""),
            clearpass_verify_tls=_boolean("CLEARPASS_VERIFY_TLS", True),
            clearpass_ca_bundle=_ca_bundle_path(
                os.getenv("CLEARPASS_CA_BUNDLE", "")
            ),
            clearpass_timeout_seconds=_integer(
                "CLEARPASS_TIMEOUT_SECONDS", 15, minimum=1, maximum=120
            ),
            default_attribute_name=(
                os.getenv("CLEARPASS_ATTRIBUTE_NAME", "").strip()
                or DEFAULT_ATTRIBUTE_NAME
            ),
        )

    @classmethod
    def from_values(
        cls,
        *,
        base_url: str,
        client_id: str,
        client_secret: str,
        verify_tls: bool,
        ca_bundle: str = "",
        timeout_seconds: int = 15,
        default_attribute_name: str = DEFAULT_ATTRIBUTE_NAME,
    ) -> "Settings":
        """由 GUI 欄位建立設定，並套用與環境載入相同的正規化規則。"""
        if not 1 <= timeout_seconds <= 120:
            raise ConfigurationError("連線逾時秒數必須介於 1 和 120 之間")
        return cls(
            clearpass_base_url=_base_url(base_url),
            clearpass_client_id=client_id.strip(),
            clearpass_client_secret=client_secret,
            clearpass_verify_tls=bool(verify_tls),
            clearpass_ca_bundle=_ca_bundle_path(ca_bundle),
            clearpass_timeout_seconds=timeout_seconds,
            default_attribute_name=(
                default_attribute_name.strip() or DEFAULT_ATTRIBUTE_NAME
            ),
        )

    @property
    def is_clearpass_configured(self) -> bool:
        """指出 OAuth Client ID 與 Secret 是否皆已提供。"""
        return bool(self.clearpass_client_id and self.clearpass_client_secret)

    @property
    def clearpass_verify(self) -> bool | str:
        """轉換成 requests 的 verify 參數：布林值或 CA bundle 路徑。"""
        if not self.clearpass_verify_tls:
            return False
        return self.clearpass_ca_bundle or True

    @property
    def tls_description(self) -> str:
        """產生不含敏感資訊、可顯示於操作紀錄的 TLS 說明。"""
        if not self.clearpass_verify_tls:
            return "TLS 憑證驗證已關閉"
        if self.clearpass_ca_bundle:
            return f"使用 CA bundle：{self.clearpass_ca_bundle}"
        return "使用作業系統/Python 信任的 CA"

    def ensure_clearpass_configured(self) -> None:
        """在發送請求前確認 OAuth 欄位及選填 CA bundle 可用。"""
        if not self.clearpass_client_id:
            raise ConfigurationError("尚未設定 CLEARPASS_CLIENT_ID")
        if not self.clearpass_client_secret:
            raise ConfigurationError("尚未輸入 CLEARPASS_CLIENT_SECRET")
        if self.clearpass_verify_tls and self.clearpass_ca_bundle:
            bundle = Path(self.clearpass_ca_bundle)
            if not bundle.is_file():
                raise ConfigurationError(f"找不到 CA bundle：{bundle}")
