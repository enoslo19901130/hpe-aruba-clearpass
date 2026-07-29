"""驗證 MAC 與 Endpoint attribute 輸入檢查規則。"""

import unittest

from clearpass_tool.validation import (
    ValidationError,
    normalize_mac,
    validate_attribute_name,
    validate_attribute_value,
)


class ValidationTests(unittest.TestCase):
    """涵蓋格式正規化、拒絕規則及 attribute 值白名單。"""

    def test_normalizes_common_mac_formats(self):
        """確認冒號、連字號、Cisco 點號及純十六進位格式皆可正規化。"""
        examples = (
            "aa:bb:cc:dd:ee:ff",
            "AA-BB-CC-DD-EE-FF",
            "aabb.ccdd.eeff",
            "aabbccddeeff",
        )
        for example in examples:
            with self.subTest(example=example):
                self.assertEqual(normalize_mac(example), "AA:BB:CC:DD:EE:FF")

    def test_rejects_invalid_mac(self):
        """確認空值、長度錯誤與非十六進位 MAC 會被拒絕。"""
        for example in (None, "", "AA:BB:CC", "GG:BB:CC:DD:EE:FF"):
            with self.subTest(example=example):
                with self.assertRaises(ValidationError):
                    normalize_mac(example)

    def test_validates_attribute_name(self):
        """確認名稱會去除外側空白並拒絕危險或含控制字元內容。"""
        self.assertEqual(validate_attribute_name(" Risk Status "), "Risk Status")
        for example in (None, "", "__proto__", "bad\nname"):
            with self.subTest(example=example):
                with self.assertRaises(ValidationError):
                    validate_attribute_name(example)

    def test_only_accepts_dropdown_values(self):
        """確認 attribute 值只能取自 GUI 下拉選單白名單。"""
        allowed = ("[ UNKNOWN ]", "[ HIGH RISK ]")
        self.assertEqual(validate_attribute_value("[ UNKNOWN ]", allowed), "[ UNKNOWN ]")
        with self.assertRaises(ValidationError):
            validate_attribute_value("HIGH RISK", allowed)


if __name__ == "__main__":
    # 支援直接執行單一測試檔，也可由 unittest discover 統一載入。
    unittest.main()
