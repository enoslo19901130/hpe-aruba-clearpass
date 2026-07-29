"""以模擬 HTTP 回應驗證 ClearPassClient，不連線至實際設備。"""

import json
import unittest

import requests

from clearpass_tool.client import ClearPassClient, ClearPassError
from clearpass_tool.config import Settings


class FakeResponse:
    """提供 requests.Response 測試所需的最小介面。"""

    def __init__(self, status_code, payload=None):
        """建立指定 HTTP 狀態碼與選填 JSON payload 的假回應。"""
        self.status_code = status_code
        self._payload = payload
        self.content = b"" if payload is None else json.dumps(payload).encode()

    def json(self):
        """模擬 response.json()，無內容時依 requests 行為拋出 ValueError。"""
        if self._payload is None:
            raise ValueError("empty response")
        return self._payload


class FakeSession:
    """依序回傳預先排定結果，並保存每次 HTTP 呼叫供斷言。"""

    def __init__(self, responses):
        """複製回應序列，避免測試執行時修改呼叫端資料。"""
        self.responses = list(responses)
        self.calls = []

    def request(self, **kwargs):
        """記錄請求並回傳下一個假回應或重新拋出排定例外。"""
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        """符合 requests.Session.close 介面；假 session 無資源需釋放。"""
        pass


class ClearPassClientTests(unittest.TestCase):
    """涵蓋 OAuth、TLS、錯誤清理與安全更新行為。"""

    def setUp(self):
        """為每項測試建立不含真實憑證的共用設定。"""
        self.settings = Settings(
            clearpass_base_url="https://clearpass.test",
            clearpass_client_id="client-id",
            clearpass_client_secret="secret",
        )

    def test_updates_one_attribute_and_preserves_others(self):
        """確認 PATCH 合併其他 attributes，並在寫入後再次 GET 驗證。"""
        session = FakeSession(
            [
                FakeResponse(200, {"access_token": "token", "expires_in": 3600}),
                FakeResponse(
                    200,
                    {
                        "id": 42,
                        "mac_address": "AA:BB:CC:DD:EE:FF",
                        "attributes": {"Owner": "WNC", "Risk": "[ UNKNOWN ]"},
                    },
                ),
                FakeResponse(204),
                FakeResponse(
                    200,
                    {
                        "id": 42,
                        "mac_address": "AA:BB:CC:DD:EE:FF",
                        "attributes": {"Owner": "WNC", "Risk": "[ HIGH RISK ]"},
                    },
                ),
            ]
        )
        client = ClearPassClient(self.settings, session=session, clock=lambda: 100.0)

        result = client.update_endpoint_attribute(
            "AA:BB:CC:DD:EE:FF", "Risk", "[ HIGH RISK ]"
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["previous_value"], "[ UNKNOWN ]")
        patch_call = session.calls[2]
        self.assertEqual(patch_call["method"], "PATCH")
        self.assertEqual(patch_call["url"], "https://clearpass.test/api/endpoint/42")
        self.assertEqual(
            patch_call["json"],
            {"attributes": {"Owner": "WNC", "Risk": "[ HIGH RISK ]"}},
        )
        self.assertEqual(patch_call["headers"]["Authorization"], "Bearer token")

    def test_skips_patch_when_value_is_already_current(self):
        """確認目標值相同時不送出不必要的 PATCH。"""
        session = FakeSession(
            [
                FakeResponse(200, {"access_token": "token", "expires_in": 3600}),
                FakeResponse(
                    200,
                    {"id": 7, "attributes": {"Risk": "[ HIGH RISK ]"}},
                ),
            ]
        )
        client = ClearPassClient(self.settings, session=session, clock=lambda: 100.0)

        result = client.update_endpoint_attribute(
            "AA:BB:CC:DD:EE:FF", "Risk", "[ HIGH RISK ]"
        )

        self.assertFalse(result["changed"])
        self.assertEqual(len(session.calls), 2)

    def test_does_not_include_secret_in_upstream_error(self):
        """確認上游認證錯誤不會將 client secret 帶入例外訊息。"""
        session = FakeSession([FakeResponse(401, {"error": "invalid_client"})])
        client = ClearPassClient(self.settings, session=session)

        with self.assertRaises(ClearPassError) as context:
            client.get_endpoint_by_mac("AA:BB:CC:DD:EE:FF")

        self.assertNotIn("secret", str(context.exception))
        self.assertIn("invalid_client", str(context.exception))

    def test_connection_does_not_return_access_token(self):
        """確認連線測試只回傳狀態資料，不將 Bearer token 交給 GUI。"""
        session = FakeSession(
            [FakeResponse(200, {"access_token": "sensitive-token", "expires_in": 3600})]
        )
        client = ClearPassClient(self.settings, session=session, clock=lambda: 100.0)

        result = client.test_connection()

        self.assertTrue(result["connected"])
        self.assertNotIn("token", result)
        self.assertNotIn("sensitive-token", str(result))

    def test_passes_ca_bundle_to_tls_verification(self):
        """確認 OAuth 與 Endpoint 請求都使用設定的 CA bundle。"""
        settings = Settings(
            clearpass_base_url="https://clearpass.test",
            clearpass_client_id="client-id",
            clearpass_client_secret="secret",
            clearpass_ca_bundle="/trusted/clearpass-ca.pem",
        )
        session = FakeSession(
            [
                FakeResponse(200, {"access_token": "token", "expires_in": 3600}),
                FakeResponse(200, {"id": 1, "attributes": {}}),
            ]
        )
        client = ClearPassClient(settings, session=session, clock=lambda: 100.0)

        client.get_endpoint_by_mac("AA:BB:CC:DD:EE:FF")

        self.assertEqual(session.calls[0]["verify"], "/trusted/clearpass-ca.pem")
        self.assertEqual(session.calls[1]["verify"], "/trusted/clearpass-ca.pem")

    def test_tls_error_explains_ca_and_hostname(self):
        """確認 TLS 失敗訊息提示 CA bundle 與 hostname 排查方向。"""
        session = FakeSession([requests.exceptions.SSLError("certificate verify failed")])
        client = ClearPassClient(self.settings, session=session)

        with self.assertRaises(ClearPassError) as context:
            client.test_connection()

        self.assertIn("CA bundle", str(context.exception))
        self.assertIn("hostname", str(context.exception))


if __name__ == "__main__":
    # 支援直接執行單一測試檔，也可由 unittest discover 統一載入。
    unittest.main()
