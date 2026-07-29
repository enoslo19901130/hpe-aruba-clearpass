"""以輕量假物件驗證 GUI 流程，不建立實際 Tk 視窗。"""

import unittest
from unittest.mock import patch

from clearpass_tool.config import ConfigurationError, Settings
from clearpass_tool.gui import (
    CLIENT_CREDENTIALS_HELP_URL,
    ClearPassDesktopApp,
    attribute_name_options,
    clearpass_address_for_display,
)


class FakeVar:
    """模擬測試流程所需的 tk.StringVar get/set 介面。"""

    def __init__(self, value=""):
        """以指定初始值建立假 Tk 變數。"""
        self.value = value

    def get(self):
        """回傳目前保存值。"""
        return self.value

    def set(self, value):
        """覆寫目前保存值。"""
        self.value = value


class FakeBadge:
    """記錄 connection_badge.configure 傳入的狀態樣式。"""

    def __init__(self):
        """建立空白元件選項集合。"""
        self.options = {}

    def configure(self, **options):
        """合併元件設定，模擬 Tk Label.configure。"""
        self.options.update(options)


class FakeClient:
    """記錄 OAuth 與 Endpoint 呼叫順序的 GUI 測試 client。"""

    def __init__(self):
        """建立空白呼叫紀錄。"""
        self.calls = []

    def test_connection(self):
        """記錄 OAuth 階段並回傳不含 token 的成功資訊。"""
        self.calls.append("oauth")
        return {
            "connected": True,
            "base_url": "https://clearpass.test",
            "tls": "已驗證",
        }

    def get_endpoint_by_mac(self, mac_address):
        """記錄 Endpoint 階段並回傳最小可用 Endpoint。"""
        self.calls.append(("endpoint", mac_address))
        return {"id": 42, "mac_address": mac_address, "attributes": {}}


class LookupFlowTests(unittest.TestCase):
    """驗證 attribute 選項與 OAuth 優先的查詢流程。"""

    def test_fs_status_is_always_an_attribute_dropdown_option(self):
        """確認固定名稱與 Endpoint 既有名稱會合併進下拉選單。"""
        options = attribute_name_options(
            {"Owner": "WNC", "Risk": "[ UNKNOWN ]"},
            "[ FS STATUS ]",
        )

        self.assertIn("[ FS STATUS ]", options)
        self.assertIn("Owner", options)
        self.assertIn("Risk", options)

    def test_lookup_authenticates_before_endpoint_request(self):
        """確認查詢按鈕先完成 OAuth，之後才允許呼叫 Endpoint API。"""
        app = object.__new__(ClearPassDesktopApp)
        client = FakeClient()
        queued_operations = []

        app.mac_var = FakeVar("aa-bb-cc-dd-ee-ff")
        app.connection_status_var = FakeVar("尚未驗證 OAuth")
        app.connection_badge = FakeBadge()
        app.oauth_authenticated = False
        app._prepare_client = lambda: client
        app._run_async = lambda label, operation, callback: queued_operations.append(
            (label, operation, callback)
        )
        app._log = lambda *_args: None
        app._set_status = lambda *_args, **_kwargs: None

        app._lookup_endpoint()

        self.assertEqual(app.mac_var.get(), "AA:BB:CC:DD:EE:FF")
        self.assertEqual(queued_operations[0][0], "查詢前 OAuth 驗證")
        self.assertEqual(client.calls, [])

        oauth_result = queued_operations[0][1]()
        self.assertEqual(client.calls, ["oauth"])
        queued_operations[0][2](oauth_result)

        self.assertTrue(app.oauth_authenticated)
        self.assertEqual(queued_operations[1][0], "查詢 Endpoint")
        endpoint = queued_operations[1][1]()
        self.assertEqual(
            client.calls,
            ["oauth", ("endpoint", "AA:BB:CC:DD:EE:FF")],
        )
        self.assertEqual(endpoint["id"], 42)


class ConnectionFormTests(unittest.TestCase):
    """驗證 GUI 僅接收 ClearPass 位址並固定使用 HTTPS。"""

    def test_display_address_omits_https_scheme(self):
        """確認完整設定 URL 載入 GUI 時只顯示 IP/FQDN 與 port。"""
        self.assertEqual(
            clearpass_address_for_display("https://clearpass.example.com:443/"),
            "clearpass.example.com:443",
        )

    def test_form_builds_https_url_from_fqdn(self):
        """確認使用者輸入 FQDN 後，送往設定層的 URL 固定為 HTTPS。"""
        app = object.__new__(ClearPassDesktopApp)
        app.base_url_var = FakeVar("clearpass.example.com")
        app.client_id_var = FakeVar("client")
        app.client_secret_var = FakeVar("secret")
        app.verify_tls_var = FakeVar(True)
        app.ca_bundle_var = FakeVar("")
        app.initial_settings = Settings.from_values(
            base_url="clearpass.example.com",
            client_id="client",
            client_secret="secret",
            verify_tls=True,
        )

        settings = app._settings_from_form()

        self.assertEqual(
            settings.clearpass_base_url,
            "https://clearpass.example.com",
        )

    def test_form_rejects_user_supplied_scheme(self):
        """確認 scheme 不可在唯讀 HTTPS 前綴之外重複輸入。"""
        app = object.__new__(ClearPassDesktopApp)
        app.base_url_var = FakeVar("https://clearpass.example.com")

        with self.assertRaisesRegex(ConfigurationError, "不需包含 https://"):
            app._settings_from_form()

    @patch("clearpass_tool.gui.webbrowser.open_new_tab", return_value=True)
    def test_credentials_help_opens_official_documentation(self, open_new_tab):
        """確認資訊說明連結會開啟指定的 Aruba 官方文件。"""
        app = object.__new__(ClearPassDesktopApp)

        app._open_client_credentials_help()

        open_new_tab.assert_called_once_with(CLIENT_CREDENTIALS_HELP_URL)


if __name__ == "__main__":
    # 支援直接執行單一測試檔，也可由 unittest discover 統一載入。
    unittest.main()
