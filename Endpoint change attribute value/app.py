"""ClearPass Endpoint Attribute 桌面工具的可執行入口。"""

from clearpass_tool.gui import run_app


if __name__ == "__main__":
    # 僅在直接執行 app.py 時啟動 GUI；匯入此模組不會產生視窗。
    run_app()
