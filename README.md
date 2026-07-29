# HPE Aruba ClearPass

此倉庫集中保存 HPE Aruba ClearPass 的 Python 自動化與 API 測試工具。

## ClearPass Endpoint Attribute 更新工具

2026-07-29 新增的純 Python／Tkinter 桌面工具，主要功能如下：

- 使用 OAuth 2.0 `client_credentials` 連線 ClearPass。
- 按 MAC Address 查詢 Endpoint；查詢前會先驗證 OAuth 狀態。
- Attribute 名稱預設提供 `[ FS STATUS ]`，並合併 Endpoint 既有 attributes。
- Attribute 值可選 `[ UNKNOWN ]` 或 `[ HIGH RISK ]`，預設為 `[ HIGH RISK ]`。
- 更新前顯示目前值與目標值並要求使用者確認。
- PATCH 時保留其他 attributes，更新後再 GET 驗證寫入結果。
- 支援嚴格 TLS 驗證及自訂 CA bundle／PEM 憑證。
- OAuth Token 只保存在 Python 程序記憶體，不顯示於介面。

專案位置：[`CPPM-API-Endpoint change attribute value`](./CPPM-API-Endpoint%20change%20attribute%20value/)

完整安裝、設定、憑證、代碼架構與維護說明：

- [開啟專案 README](./CPPM-API-Endpoint%20change%20attribute%20value/README.md)

## 快速啟動

```bash
cd "CPPM-API-Endpoint change attribute value"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

> 此倉庫包含測試用途設定與紀錄。正式使用前應換發並限制 OAuth Client 權限，不應沿用測試憑證。
