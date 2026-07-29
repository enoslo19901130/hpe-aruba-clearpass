"""驗證設定正規化、TLS 限制與 CA bundle 檢查。"""

import tempfile
import unittest
from pathlib import Path

from clearpass_tool.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    """涵蓋 Settings.from_values 的預設值與安全防護。"""

    def test_defaults_attribute_name_to_fs_status(self):
        """確認未指定名稱時預設使用業務 attribute「[ FS STATUS ]」。"""
        settings = Settings.from_values(
            base_url="https://clearpass.example.test",
            client_id="client",
            client_secret="secret",
            verify_tls=True,
        )

        self.assertEqual(settings.default_attribute_name, "[ FS STATUS ]")

    def test_normalizes_clearpass_ip_to_https(self):
        """確認裸 IP 會補上 HTTPS 並移除結尾斜線。"""
        settings = Settings.from_values(
            base_url="172.17.20.181/",
            client_id="client",
            client_secret="secret",
            verify_tls=True,
        )

        self.assertEqual(settings.clearpass_base_url, "https://172.17.20.181")
        self.assertTrue(settings.clearpass_verify)

    def test_rejects_unencrypted_remote_oauth(self):
        """確認遠端 ClearPass 不允許透過純 HTTP 傳送 OAuth secret。"""
        with self.assertRaises(ConfigurationError):
            Settings.from_values(
                base_url="http://172.17.20.181",
                client_id="client",
                client_secret="secret",
                verify_tls=False,
            )

    def test_allows_http_for_local_test_server(self):
        """確認本機假服務可使用 HTTP 進行離線整合測試。"""
        settings = Settings.from_values(
            base_url="http://127.0.0.1:8080",
            client_id="client",
            client_secret="secret",
            verify_tls=False,
        )

        self.assertEqual(settings.clearpass_base_url, "http://127.0.0.1:8080")

    def test_uses_existing_ca_bundle(self):
        """確認存在的 CA bundle 會轉為 requests 使用的絕對路徑。"""
        with tempfile.NamedTemporaryFile(suffix=".pem") as certificate:
            settings = Settings.from_values(
                base_url="https://clearpass.example.test",
                client_id="client",
                client_secret="secret",
                verify_tls=True,
                ca_bundle=certificate.name,
            )
            settings.ensure_clearpass_configured()

            self.assertEqual(settings.clearpass_verify, str(Path(certificate.name).resolve()))

    def test_disabled_tls_verification_ignores_ca_bundle(self):
        """確認停用 TLS 驗證時不會要求選填 CA bundle 必須存在。"""
        settings = Settings.from_values(
            base_url="https://clearpass.example.test",
            client_id="client",
            client_secret="secret",
            verify_tls=False,
            ca_bundle="/missing/ca.pem",
        )

        settings.ensure_clearpass_configured()
        self.assertFalse(settings.clearpass_verify)

    def test_missing_ca_bundle_is_rejected_when_verification_enabled(self):
        """確認啟用 TLS 驗證時會拒絕不存在的 CA bundle。"""
        settings = Settings.from_values(
            base_url="https://clearpass.example.test",
            client_id="client",
            client_secret="secret",
            verify_tls=True,
            ca_bundle="/missing/ca.pem",
        )

        with self.assertRaises(ConfigurationError):
            settings.ensure_clearpass_configured()


if __name__ == "__main__":
    # 支援直接執行單一測試檔，也可由 unittest discover 統一載入。
    unittest.main()
