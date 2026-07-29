"""封裝 ClearPass OAuth、Endpoint 查詢與 attribute 更新的 HTTP client。"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

from .config import Settings


class ClearPassError(RuntimeError):
    """表示 ClearPass 連線或 API 回應失敗，並保留可選 HTTP 狀態碼。"""

    def __init__(self, message: str, status_code: int | None = None):
        """建立不含敏感憑證的使用者可讀錯誤。"""
        super().__init__(message)
        self.status_code = status_code


class ClearPassResponseError(ClearPassError):
    """表示 ClearPass 雖回應成功，但資料格式或驗證結果不符合預期。"""

    pass


@dataclass(slots=True)
class _AccessToken:
    """保存記憶體內的 Bearer token 與可安全使用至的單調時鐘時間。"""

    value: str
    expires_at: float


class ClearPassClient:
    """提供執行緒安全的 OAuth token 快取及 Endpoint API 操作。"""

    def __init__(
        self,
        settings: Settings,
        session: requests.Session | None = None,
        clock=time.monotonic,
    ) -> None:
        """建立 client；可注入 Session 與時鐘以便離線測試。"""
        self.settings = settings
        self.session = session or requests.Session()
        self.clock = clock
        self._token: _AccessToken | None = None
        self._token_lock = threading.Lock()

    def test_connection(self) -> dict[str, Any]:
        """取得或重用 OAuth token，但不把 token 回傳給 GUI。"""
        self._access_token()
        return {
            "connected": True,
            "base_url": self.settings.clearpass_base_url,
            "tls": self.settings.tls_description,
        }

    def close(self) -> None:
        """清除記憶體中的 token 並關閉底層 HTTP session。"""
        self._token = None
        self.session.close()

    def get_endpoint_by_mac(self, mac_address: str) -> dict[str, Any]:
        """使用正規化 MAC Address 查詢單一 ClearPass Endpoint。"""
        encoded_mac = quote(mac_address, safe="")
        payload = self._authorized_request(
            "GET", f"/api/endpoint/mac-address/{encoded_mac}"
        )
        if not isinstance(payload, dict):
            raise ClearPassResponseError("ClearPass 回傳的 Endpoint 格式不正確")
        return payload

    def update_endpoint_attribute(
        self,
        mac_address: str,
        attribute_name: str,
        attribute_value: str,
    ) -> dict[str, Any]:
        """更新單一 attribute，保留其他 attributes，並重新查詢驗證結果。"""
        endpoint = self.get_endpoint_by_mac(mac_address)
        endpoint_id = endpoint.get("id", endpoint.get("endpoint_id"))
        if endpoint_id is None:
            raise ClearPassResponseError("ClearPass Endpoint 回應缺少 id")

        existing_attributes = endpoint.get("attributes") or {}
        if not isinstance(existing_attributes, dict):
            raise ClearPassResponseError("ClearPass Endpoint attributes 格式不正確")

        previous_value = existing_attributes.get(attribute_name)
        if previous_value == attribute_value:
            return {
                "changed": False,
                "endpoint_id": endpoint_id,
                "mac_address": mac_address,
                "attribute_name": attribute_name,
                "previous_value": previous_value,
                "attribute_value": attribute_value,
                "endpoint": endpoint,
            }

        # ClearPass PATCH updates the top-level attributes object. Send a merged copy
        # so changing one key does not discard the endpoint's other custom attributes.
        merged_attributes = dict(existing_attributes)
        merged_attributes[attribute_name] = attribute_value

        self._authorized_request(
            "PATCH",
            f"/api/endpoint/{quote(str(endpoint_id), safe='')}",
            json_body={"attributes": merged_attributes},
        )

        verified_endpoint = self.get_endpoint_by_mac(mac_address)
        verified_attributes = verified_endpoint.get("attributes") or {}
        if not isinstance(verified_attributes, dict) or (
            verified_attributes.get(attribute_name) != attribute_value
        ):
            raise ClearPassResponseError(
                "ClearPass 回應更新成功，但重新查詢後 attribute 值不一致"
            )

        return {
            "changed": True,
            "endpoint_id": endpoint_id,
            "mac_address": mac_address,
            "attribute_name": attribute_name,
            "previous_value": previous_value,
            "attribute_value": attribute_value,
            "endpoint": verified_endpoint,
        }

    def _authorized_request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        retry_auth: bool = True,
    ) -> Any:
        """附加 Bearer token 發送 API 請求，遇到 401 時只重新認證一次。"""
        token = self._access_token()
        try:
            return self._request_json(
                method,
                path,
                json_body=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except ClearPassError as error:
            if error.status_code == 401 and retry_auth:
                # Token 可能被伺服器提早撤銷；清除後重取一次，避免無限重試。
                self._token = None
                return self._authorized_request(
                    method,
                    path,
                    json_body=json_body,
                    retry_auth=False,
                )
            raise

    def _access_token(self) -> str:
        """回傳有效 token；過期或不存在時以 client_credentials 重新取得。"""
        if self._token and self._token.expires_at > self.clock():
            return self._token.value

        # 多個背景操作同時發生時，只允許一個執行緒向 /api/oauth 取 token。
        with self._token_lock:
            # 等鎖期間其他執行緒可能已更新 token，因此進入鎖後必須再次檢查。
            if self._token and self._token.expires_at > self.clock():
                return self._token.value

            payload = self._request_json(
                "POST",
                "/api/oauth",
                json_body={
                    "grant_type": "client_credentials",
                    "client_id": self.settings.clearpass_client_id,
                    "client_secret": self.settings.clearpass_client_secret,
                },
            )
            if not isinstance(payload, dict) or not isinstance(
                payload.get("access_token"), str
            ):
                raise ClearPassResponseError("ClearPass OAuth 回應缺少 access_token")

            try:
                expires_in = max(float(payload.get("expires_in", 300)), 1.0)
            except (TypeError, ValueError):
                expires_in = 300.0

            # 提前最多 60 秒視為過期，降低網路延遲造成 token 臨界失效的風險。
            usable_lifetime = max(expires_in - min(60.0, expires_in * 0.1), 1.0)
            self._token = _AccessToken(
                value=payload["access_token"],
                expires_at=self.clock() + usable_lifetime,
            )
            return self._token.value

    def _request_json(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """統一執行 HTTP、TLS、逾時、狀態碼及 JSON 解析處理。"""
        url = f"{self.settings.clearpass_base_url}{path}"
        request_headers = {"Accept": "application/json", **(headers or {})}

        try:
            response = self.session.request(
                method=method,
                url=url,
                json=json_body,
                headers=request_headers,
                timeout=self.settings.clearpass_timeout_seconds,
                verify=self.settings.clearpass_verify,
            )
        except requests.exceptions.SSLError as exc:
            raise ClearPassError(
                "ClearPass TLS 憑證驗證失敗。請使用與憑證 SAN 相符的 hostname，"
                "或指定簽發該憑證的 CA bundle；正式環境請勿關閉驗證"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise ClearPassError("連線 ClearPass 逾時") from exc
        except requests.exceptions.RequestException as exc:
            raise ClearPassError("無法連線至 ClearPass") from exc

        if not 200 <= response.status_code < 300:
            message = _safe_error_message(response)
            raise ClearPassError(message, status_code=response.status_code)

        if response.status_code == 204 or not response.content:
            return None

        try:
            return response.json()
        except ValueError as exc:
            raise ClearPassResponseError("ClearPass 回傳了無效的 JSON") from exc


def _safe_error_message(response: requests.Response) -> str:
    """從錯誤回應擷取短訊息，且不回顯送出的 client secret 或 token。"""
    prefix = f"ClearPass API 回應 {response.status_code}"
    try:
        payload = response.json()
    except ValueError:
        return prefix

    if isinstance(payload, dict):
        for key in ("error_description", "detail", "message", "error"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return f"{prefix}: {value.strip()[:300]}"
    return prefix
