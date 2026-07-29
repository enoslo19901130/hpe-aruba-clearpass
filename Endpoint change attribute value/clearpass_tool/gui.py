"""以 Tkinter 實作 ClearPass Endpoint 查詢與 attribute 更新桌面介面。"""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Callable

from .client import ClearPassClient, ClearPassError, ClearPassResponseError
from .config import ConfigurationError, Settings
from .validation import (
    ValidationError,
    normalize_mac,
    validate_attribute_name,
    validate_attribute_value,
)


# 全域配色集中管理，避免各區塊自行定義造成狀態顏色不一致。
BACKGROUND = "#f3f5f4"
CARD = "#ffffff"
INK = "#14201c"
MUTED = "#63736c"
GREEN = "#147a57"
GREEN_SOFT = "#e6f4ee"
RED = "#b83a2f"
RED_SOFT = "#fbeceb"
BORDER = "#d8e0dc"
NOT_SET = "（尚未設定）"
CLIENT_CREDENTIALS_HELP_URL = (
    "https://developer.arubanetworks.com/cppm/v6.12.7/docs/"
    "getting-started-with-the-clearpass-policy-manager-api"
)


def display_value(value: Any) -> str:
    """把 API 值轉成適合 GUI 顯示的字串，並標示空值。"""
    if value is None or value == "":
        return NOT_SET
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def clearpass_address_for_display(base_url: str) -> str:
    """移除設定值的 URL scheme，讓 GUI 只顯示 ClearPass IP、FQDN 與選填 port。"""
    value = base_url.strip().rstrip("/")
    _scheme, separator, address = value.partition("://")
    return address if separator else value


def attribute_name_options(
    attributes: dict[str, Any], preferred_name: str
) -> tuple[str, ...]:
    """合併 Endpoint 既有名稱與固定首選名稱，去重後排序供下拉選單使用。"""
    names = {str(name) for name in attributes}
    preferred = preferred_name.strip()
    if preferred:
        names.add(preferred)
    return tuple(sorted(names))


class ClearPassDesktopApp(ttk.Frame):
    """協調 Tkinter 狀態、背景 API 工作及使用者確認流程的主視窗。"""

    def __init__(
        self,
        root: tk.Tk,
        settings: Settings,
        client_factory: Callable[[Settings], ClearPassClient] = ClearPassClient,
    ) -> None:
        """建立應用程式狀態、Tk 變數、元件及背景結果輪詢器。"""
        self.root = root
        self.initial_settings = settings
        self.client_factory = client_factory
        self.client: ClearPassClient | None = None
        self.client_key: tuple[Any, ...] | None = None
        self.endpoint: dict[str, Any] | None = None
        self.oauth_authenticated = False
        self.busy = False
        self.closing = False
        self.insecure_tls_approved = False
        self.result_queue: queue.Queue[tuple] = queue.Queue()

        # Tk 變數是畫面與應用程式狀態之間的單一資料來源。
        self.base_url_var = tk.StringVar(
            value=clearpass_address_for_display(settings.clearpass_base_url)
        )
        self.client_id_var = tk.StringVar(value=settings.clearpass_client_id)
        self.client_secret_var = tk.StringVar(value=settings.clearpass_client_secret)
        self.verify_tls_var = tk.BooleanVar(value=settings.clearpass_verify_tls)
        self.ca_bundle_var = tk.StringVar(value=settings.clearpass_ca_bundle)
        self.mac_var = tk.StringVar()
        self.attribute_var = tk.StringVar(value=settings.default_attribute_name)
        self.attribute_value_var = tk.StringVar(value=settings.default_attribute_value)
        self.endpoint_id_var = tk.StringVar(value="—")
        self.endpoint_status_var = tk.StringVar(value="—")
        self.current_value_var = tk.StringVar(value=NOT_SET)
        self.target_value_var = tk.StringVar(value=settings.default_attribute_value)
        self.connection_status_var = tk.StringVar(value="尚未測試連線")
        self.tls_hint_var = tk.StringVar()
        self.status_var = tk.StringVar(value="就緒")

        self._configure_window()
        self._configure_styles()
        super().__init__(root, style="Root.TFrame", padding=(24, 18, 24, 14))
        self.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1, uniform="cards")
        self._build_interface()
        self._bind_events()
        self._update_tls_hint()
        self._refresh_action_states()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(80, self._poll_results)

    def _configure_window(self) -> None:
        """設定視窗標題、初始尺寸、最小尺寸與可伸縮網格。"""
        self.root.title("ClearPass Endpoint Attribute")
        self.root.geometry("980x700")
        self.root.minsize(860, 640)
        self.root.configure(background=BACKGROUND)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

    def _configure_styles(self) -> None:
        """建立整個介面共用的 ttk 樣式與按鈕狀態配色。"""
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Root.TFrame", background=BACKGROUND)
        style.configure("Root.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabelframe", background=CARD, bordercolor=BORDER, relief="solid")
        style.configure(
            "Card.TLabelframe.Label",
            background=CARD,
            foreground=INK,
            font=("Helvetica Neue", 13, "bold"),
        )
        style.configure("Card.TLabel", background=CARD, foreground=INK)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Hint.TLabel", background=CARD, foreground=MUTED, font=("Helvetica Neue", 10))
        style.configure("Success.TLabel", background=CARD, foreground=GREEN, font=("Helvetica Neue", 11, "bold"))
        style.configure("Warning.TLabel", background=CARD, foreground=RED, font=("Helvetica Neue", 10, "bold"))
        style.configure("Accent.TButton", foreground="white", background=GREEN, bordercolor=GREEN, padding=(16, 9))
        style.map("Accent.TButton", background=[("active", "#0f6849"), ("disabled", "#9ab8ac")])
        style.configure("Danger.TButton", foreground="white", background=RED, bordercolor=RED, padding=(18, 9))
        style.map("Danger.TButton", background=[("active", "#992f27"), ("disabled", "#caa8a4")])
        style.configure("Secondary.TButton", padding=(13, 8))
        style.configure("TEntry", padding=7)
        style.configure("TCombobox", padding=7)

    def _build_interface(self) -> None:
        """依序組合標題、連線、Endpoint、紀錄與狀態列區塊。"""
        self._build_header()
        self._build_connection_card()
        self._build_endpoint_card()
        self._build_activity_card()
        self._build_footer()

    def _build_header(self) -> None:
        """建立應用程式標題及 OAuth 連線狀態徽章。"""
        header = tk.Frame(self, background=BACKGROUND)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header,
            text="ClearPass Endpoint Attribute",
            background=BACKGROUND,
            foreground=INK,
            font=("Helvetica Neue", 25, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            header,
            text="純 Python 桌面工具 · OAuth 憑證不會離開本機程式",
            background=BACKGROUND,
            foreground=MUTED,
            font=("Helvetica Neue", 11),
        ).grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.connection_badge = tk.Label(
            header,
            textvariable=self.connection_status_var,
            background=BORDER,
            foreground=MUTED,
            padx=13,
            pady=7,
            font=("Helvetica Neue", 10, "bold"),
        )
        self.connection_badge.grid(row=0, column=1, rowspan=2, sticky="e")

    def _build_connection_card(self) -> None:
        """建立 ClearPass IP/FQDN、OAuth 憑證與 TLS 憑證設定區。"""
        card = ttk.LabelFrame(
            self,
            text="  連線與憑證設定  ",
            style="Card.TLabelframe",
            padding=(20, 16),
        )
        card.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(0, 12))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="ClearPass IP / FQDN", style="Card.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        address_row = ttk.Frame(card, style="Card.TFrame")
        address_row.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(5, 12)
        )
        address_row.columnconfigure(1, weight=1)
        # HTTPS scheme 固定由程式提供，使用者只能編輯 ClearPass 位址。
        ttk.Label(address_row, text="https://", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 3)
        )
        self.base_url_entry = ttk.Entry(address_row, textvariable=self.base_url_var)
        self.base_url_entry.grid(row=0, column=1, sticky="ew")

        ttk.Label(card, text="Client ID", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8))
        ttk.Label(card, text="Client Secret", style="Card.TLabel").grid(row=2, column=1, sticky="w", padx=(8, 0))
        # 連結直接指向 Aruba 官方 API Client 建立與憑證取得說明。
        self.credentials_help_link = tk.Label(
            card,
            text="取得資訊說明",
            background=CARD,
            foreground="#1769aa",
            activeforeground=GREEN,
            cursor="hand2",
            font=("Helvetica Neue", 10, "underline"),
            takefocus=True,
        )
        self.credentials_help_link.grid(row=2, column=1, sticky="e")
        self.credentials_help_link.bind("<Button-1>", self._open_client_credentials_help)
        self.credentials_help_link.bind("<Return>", self._open_client_credentials_help)
        self.client_id_entry = ttk.Entry(card, textvariable=self.client_id_var)
        self.client_id_entry.grid(row=3, column=0, sticky="ew", padx=(0, 8), pady=(5, 12))
        self.client_secret_entry = ttk.Entry(card, textvariable=self.client_secret_var, show="•")
        self.client_secret_entry.grid(row=3, column=1, sticky="ew", padx=(8, 0), pady=(5, 12))

        tls_row = ttk.Frame(card, style="Card.TFrame")
        tls_row.grid(row=4, column=0, columnspan=2, sticky="ew")
        tls_row.columnconfigure(1, weight=1)
        self.verify_tls_check = ttk.Checkbutton(
            tls_row,
            text="驗證 TLS 伺服器憑證",
            variable=self.verify_tls_var,
            command=self._on_tls_toggle,
        )
        self.verify_tls_check.grid(row=0, column=0, sticky="w")
        self.tls_hint_label = ttk.Label(tls_row, textvariable=self.tls_hint_var, style="Hint.TLabel")
        self.tls_hint_label.grid(row=0, column=1, sticky="e")

        ttk.Label(card, text="CA bundle / PEM（可選）", style="Card.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(11, 0))
        certificate_row = ttk.Frame(card, style="Card.TFrame")
        certificate_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        certificate_row.columnconfigure(0, weight=1)
        self.ca_bundle_entry = ttk.Entry(certificate_row, textvariable=self.ca_bundle_var)
        self.ca_bundle_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.browse_button = ttk.Button(certificate_row, text="選擇憑證…", style="Secondary.TButton", command=self._browse_ca_bundle)
        self.browse_button.grid(row=0, column=1)
        self.test_connection_button = ttk.Button(card, text="測試 OAuth 連線", style="Accent.TButton", command=self._test_connection)
        self.test_connection_button.grid(row=7, column=1, sticky="e", pady=(14, 0))

    def _open_client_credentials_help(self, _event: tk.Event | None = None) -> None:
        """使用系統預設瀏覽器開啟 Aruba API Client 資訊取得說明。"""
        try:
            opened = webbrowser.open_new_tab(CLIENT_CREDENTIALS_HELP_URL)
        except webbrowser.Error as error:
            messagebox.showerror(
                "無法開啟說明",
                f"請手動開啟以下網址：\n{CLIENT_CREDENTIALS_HELP_URL}\n\n{error}",
                parent=self.root,
            )
            return

        if not opened:
            messagebox.showwarning(
                "無法開啟說明",
                f"請手動開啟以下網址：\n{CLIENT_CREDENTIALS_HELP_URL}",
                parent=self.root,
            )

    def _build_endpoint_card(self) -> None:
        """建立 MAC 查詢、Endpoint 摘要、attribute 選擇與更新確認區。"""
        card = ttk.LabelFrame(
            self,
            text="  Endpoint attribute 更新  ",
            style="Card.TLabelframe",
            padding=(20, 16),
        )
        card.grid(row=1, column=1, sticky="nsew", padx=(7, 0), pady=(0, 12))
        card.columnconfigure(0, weight=1)
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="MAC Address", style="Card.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        mac_row = ttk.Frame(card, style="Card.TFrame")
        mac_row.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 12))
        mac_row.columnconfigure(0, weight=1)
        self.mac_entry = ttk.Entry(mac_row, textvariable=self.mac_var)
        self.mac_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.query_button = ttk.Button(mac_row, text="查詢 Endpoint", style="Accent.TButton", command=self._lookup_endpoint)
        self.query_button.grid(row=0, column=1)

        summary = ttk.Frame(card, style="Card.TFrame", padding=(0, 2, 0, 10))
        summary.grid(row=2, column=0, columnspan=2, sticky="ew")
        summary.columnconfigure(1, weight=1)
        summary.columnconfigure(3, weight=1)
        ttk.Label(summary, text="Endpoint ID", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(summary, textvariable=self.endpoint_id_var, style="Success.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 24))
        ttk.Label(summary, text="ClearPass 狀態", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Label(summary, textvariable=self.endpoint_status_var, style="Success.TLabel").grid(row=0, column=3, sticky="w", padx=(8, 0))

        ttk.Separator(card).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        ttk.Label(card, text="Attribute 名稱（可選擇或直接輸入）", style="Card.TLabel").grid(row=4, column=0, sticky="w", padx=(0, 8))
        ttk.Label(card, text="更新後值", style="Card.TLabel").grid(row=4, column=1, sticky="w", padx=(8, 0))
        self.attribute_combo = ttk.Combobox(
            card,
            textvariable=self.attribute_var,
            values=attribute_name_options(
                {}, self.initial_settings.default_attribute_name
            ),
            state="disabled",
        )
        self.attribute_combo.grid(row=5, column=0, sticky="ew", padx=(0, 8), pady=(5, 12))
        self.attribute_value_combo = ttk.Combobox(
            card,
            textvariable=self.attribute_value_var,
            values=self.initial_settings.attribute_values,
            state="disabled",
        )
        self.attribute_value_combo.grid(row=5, column=1, sticky="ew", padx=(8, 0), pady=(5, 12))

        preview = tk.Frame(card, background="#f7faf8", highlightbackground=BORDER, highlightthickness=1, padx=15, pady=11)
        preview.grid(row=6, column=0, columnspan=2, sticky="ew")
        preview.columnconfigure(0, weight=1)
        preview.columnconfigure(2, weight=1)
        tk.Label(preview, text="目前值", background="#f7faf8", foreground=MUTED, font=("Helvetica Neue", 9)).grid(row=0, column=0, sticky="w")
        tk.Label(preview, text="→", background="#f7faf8", foreground=MUTED).grid(row=0, column=1, rowspan=2, padx=18)
        tk.Label(preview, text="更新後", background="#f7faf8", foreground=MUTED, font=("Helvetica Neue", 9)).grid(row=0, column=2, sticky="e")
        tk.Label(preview, textvariable=self.current_value_var, background="#f7faf8", foreground=INK, font=("Menlo", 11, "bold")).grid(row=1, column=0, sticky="w", pady=(4, 0))
        tk.Label(preview, textvariable=self.target_value_var, background="#f7faf8", foreground=GREEN, font=("Menlo", 11, "bold")).grid(row=1, column=2, sticky="e", pady=(4, 0))

        self.update_button = ttk.Button(card, text="確認並套用更新", style="Danger.TButton", command=self._update_attribute)
        self.update_button.grid(row=7, column=1, sticky="e", pady=(14, 0))

    def _build_activity_card(self) -> None:
        """建立只讀、帶時間戳及成功／錯誤顏色的操作紀錄區。"""
        card = ttk.LabelFrame(self, text="  操作紀錄  ", style="Card.TLabelframe", padding=(14, 10))
        card.grid(row=2, column=0, columnspan=2, sticky="nsew")
        self.rowconfigure(2, weight=1)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=1)
        self.activity_text = scrolledtext.ScrolledText(
            card,
            height=4,
            wrap="word",
            state="disabled",
            borderwidth=0,
            background="#fbfcfb",
            foreground=INK,
            font=("Menlo", 10),
        )
        self.activity_text.grid(row=0, column=0, sticky="nsew")
        self.activity_text.tag_configure("success", foreground=GREEN)
        self.activity_text.tag_configure("error", foreground=RED)
        self._log("程式已啟動；尚未向 ClearPass 傳送任何資料。")

    def _build_footer(self) -> None:
        """建立目前操作狀態文字及背景工作進度指示器。"""
        footer = ttk.Frame(self, style="Root.TFrame")
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var, style="Root.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.progress.grid(row=0, column=1, sticky="e")

    def _bind_events(self) -> None:
        """綁定輸入變更預覽及 MAC 欄位 Enter 快捷查詢。"""
        self.attribute_var.trace_add("write", lambda *_args: self._refresh_preview())
        self.attribute_value_var.trace_add("write", lambda *_args: self._refresh_preview())
        self.mac_entry.bind("<Return>", lambda _event: self._lookup_endpoint())

    def _settings_from_form(self) -> Settings:
        """讀取連線表單並回傳完成安全檢查的 Settings。"""
        address = self.base_url_var.get().strip()
        if not address:
            raise ConfigurationError("請輸入 ClearPass IP 或 FQDN")
        if "://" in address:
            raise ConfigurationError(
                "ClearPass IP / FQDN 不需包含 https://，請只輸入主機位址"
            )

        settings = Settings.from_values(
            # GUI 不接受可變 scheme，所有 OAuth 與 API 請求一律使用 HTTPS。
            base_url=f"https://{address}",
            client_id=self.client_id_var.get(),
            client_secret=self.client_secret_var.get(),
            verify_tls=self.verify_tls_var.get(),
            ca_bundle=self.ca_bundle_var.get(),
            timeout_seconds=self.initial_settings.clearpass_timeout_seconds,
            default_attribute_name=self.initial_settings.default_attribute_name,
        )
        settings.ensure_clearpass_configured()
        return settings

    def _prepare_client(self) -> ClearPassClient | None:
        """驗證表單與 TLS 風險，並重用或依設定建立 API client。"""
        try:
            settings = self._settings_from_form()
        except ConfigurationError as error:
            messagebox.showerror("連線設定不完整", str(error), parent=self.root)
            self._set_status(str(error), error=True)
            return None

        if not settings.clearpass_verify_tls and not self.insecure_tls_approved:
            approved = messagebox.askyesno(
                "TLS 憑證驗證已關閉",
                "關閉憑證驗證可能讓 client secret、Bearer token 與 Endpoint 資料遭到攔截。\n\n"
                "僅可在隔離的測試環境暫時使用。仍要繼續嗎？",
                icon="warning",
                parent=self.root,
            )
            if not approved:
                return None
            self.insecure_tls_approved = True

        # 憑證、主機或 TLS 任一項改變都必須丟棄舊 token 與 Endpoint 狀態。
        key = (
            settings.clearpass_base_url,
            settings.clearpass_client_id,
            settings.clearpass_client_secret,
            settings.clearpass_verify_tls,
            settings.clearpass_ca_bundle,
            settings.clearpass_timeout_seconds,
        )
        if key != self.client_key:
            if self.client:
                self.client.close()
            self.client = self.client_factory(settings)
            self.client_key = key
            self.endpoint = None
            self.oauth_authenticated = False
            self._set_connection_status("尚未驗證 OAuth")
            self._clear_endpoint_view()
        return self.client

    def _test_connection(self) -> None:
        """由「測試 OAuth」按鈕啟動不會查詢 Endpoint 的認證測試。"""
        client = self._prepare_client()
        if not client:
            return
        self._start_oauth_check(
            "測試 OAuth 連線",
            client,
            self._on_connection_success,
        )

    def _start_oauth_check(
        self,
        label: str,
        client: ClearPassClient,
        on_success: Callable[[dict[str, Any]], None],
    ) -> None:
        """以背景執行緒驗證 OAuth，成功時在 Tk 主執行緒呼叫指定 callback。"""
        self.oauth_authenticated = False
        self._set_connection_status("OAuth 驗證中…")
        self._run_async(label, client.test_connection, on_success)

    def _on_connection_success(self, result: dict[str, Any]) -> None:
        """將 OAuth 成功結果反映到徽章、紀錄與底部狀態列。"""
        self.oauth_authenticated = True
        self._set_connection_status("OAuth 連線成功", state="success")
        self._log(f"OAuth 認證成功｜{result['base_url']}｜{result['tls']}", "success")
        self._set_status("OAuth token 已取得並安全保留在記憶體中")

    def _lookup_endpoint(self) -> None:
        """驗證 MAC 與連線設定，並先驗證 OAuth 再進行 Endpoint 查詢。"""
        try:
            mac_address = normalize_mac(self.mac_var.get())
        except ValidationError as error:
            messagebox.showerror("MAC Address 格式錯誤", str(error), parent=self.root)
            return
        client = self._prepare_client()
        if not client:
            return
        self.mac_var.set(mac_address)
        # 拆成兩階段可確保 OAuth 失敗時不會呼叫 Endpoint API，錯誤也更清楚。
        self._start_oauth_check(
            "查詢前 OAuth 驗證",
            client,
            lambda result: self._on_lookup_oauth_success(
                client,
                mac_address,
                result,
            ),
        )

    def _on_lookup_oauth_success(
        self,
        client: ClearPassClient,
        mac_address: str,
        result: dict[str, Any],
    ) -> None:
        """OAuth 成功後才排程第二階段的 MAC Endpoint 查詢。"""
        self._on_connection_success(result)
        self._run_async(
            "查詢 Endpoint",
            lambda: client.get_endpoint_by_mac(mac_address),
            self._on_lookup_success,
        )

    def _on_lookup_success(self, endpoint: dict[str, Any]) -> None:
        """保存 Endpoint、更新摘要及組合 attribute 名稱下拉選單。"""
        attributes = endpoint.get("attributes") or {}
        if not isinstance(attributes, dict):
            raise ClearPassResponseError("ClearPass Endpoint attributes 格式不正確")
        self.endpoint = endpoint
        self.endpoint_id_var.set(display_value(endpoint.get("id", endpoint.get("endpoint_id"))))
        self.endpoint_status_var.set(display_value(endpoint.get("status")))
        preferred = self.initial_settings.default_attribute_name
        names = attribute_name_options(attributes, preferred)
        self.attribute_combo.configure(values=names)
        if preferred:
            self.attribute_var.set(preferred)
        elif len(names) == 1:
            self.attribute_var.set(names[0])
        else:
            self.attribute_var.set("")
        self._refresh_preview()
        self._refresh_action_states()
        self._log(
            f"查詢成功｜MAC {display_value(endpoint.get('mac_address'))}｜"
            f"Endpoint ID {self.endpoint_id_var.get()}｜{len(attributes)} 個既有 attributes",
            "success",
        )
        self._set_status("Endpoint 查詢完成；請選擇或輸入 attribute")

    def _update_attribute(self) -> None:
        """驗證更新內容、要求人工確認後，以背景工作送出 PATCH。"""
        if not self.endpoint:
            return
        client = self._prepare_client()
        if not client:
            return
        if not self.endpoint:
            messagebox.showwarning(
                "連線設定已變更",
                "ClearPass 連線或憑證設定已變更，請重新查詢 Endpoint 後再更新。",
                parent=self.root,
            )
            return
        try:
            mac_address = normalize_mac(self.mac_var.get())
            attribute_name = validate_attribute_name(self.attribute_var.get())
            attribute_value = validate_attribute_value(
                self.attribute_value_var.get(), self.initial_settings.attribute_values
            )
        except ValidationError as error:
            messagebox.showerror("輸入內容錯誤", str(error), parent=self.root)
            return

        attributes = self.endpoint.get("attributes") or {}
        previous_value = attributes.get(attribute_name) if isinstance(attributes, dict) else None
        # 寫入屬於有副作用操作，必須顯示舊值與新值供使用者再次確認。
        confirmed = messagebox.askyesno(
            "確認更新 Endpoint",
            f"MAC Address：{mac_address}\n"
            f"Attribute：{attribute_name}\n"
            f"目前值：{display_value(previous_value)}\n"
            f"更新後：{attribute_value}\n\n"
            "確定要送出至 ClearPass 嗎？",
            icon="warning",
            parent=self.root,
        )
        if not confirmed:
            return
        self._run_async(
            "更新 Endpoint attribute",
            lambda: client.update_endpoint_attribute(
                mac_address=mac_address,
                attribute_name=attribute_name,
                attribute_value=attribute_value,
            ),
            self._on_update_success,
        )

    def _on_update_success(self, result: dict[str, Any]) -> None:
        """顯示 API 更新及重新驗證完成後的最終結果。"""
        self.endpoint = result["endpoint"]
        self.attribute_var.set(result["attribute_name"])
        self._refresh_preview()
        action = "更新完成" if result["changed"] else "值已相同，無需更新"
        self._log(
            f"{action}｜{result['attribute_name']}｜"
            f"{display_value(result['previous_value'])} → {result['attribute_value']}",
            "success",
        )
        self._set_status(action)
        messagebox.showinfo(
            action,
            f"{result['attribute_name']}：{result['attribute_value']}",
            parent=self.root,
        )

    def _browse_ca_bundle(self) -> None:
        """讓使用者選擇 PEM／憑證檔，選取後自動開啟 TLS 驗證。"""
        path = filedialog.askopenfilename(
            parent=self.root,
            title="選擇 ClearPass CA bundle",
            filetypes=(
                ("PEM 憑證", "*.pem"),
                ("憑證檔案", "*.crt *.cer"),
                ("所有檔案", "*"),
            ),
        )
        if path:
            self.ca_bundle_var.set(path)
            self.verify_tls_var.set(True)
            self._update_tls_hint()

    def _on_tls_toggle(self) -> None:
        """TLS 選項改變時撤銷先前風險同意並刷新提示。"""
        self.insecure_tls_approved = False
        self._update_tls_hint()

    def _update_tls_hint(self) -> None:
        """依 TLS 驗證狀態切換提示文字、樣式與 CA bundle 控制項。"""
        if self.verify_tls_var.get():
            self.tls_hint_var.set("建議：使用與憑證 SAN 相符的 FQDN")
            self.tls_hint_label.configure(style="Hint.TLabel")
            self.ca_bundle_entry.configure(state="normal")
            self.browse_button.configure(state="normal")
        else:
            self.tls_hint_var.set("警告：憑證驗證已關閉")
            self.tls_hint_label.configure(style="Warning.TLabel")
            self.ca_bundle_entry.configure(state="disabled")
            self.browse_button.configure(state="disabled")

    def _refresh_preview(self) -> None:
        """即時顯示目前 attribute 值與準備更新的新值。"""
        attributes = self.endpoint.get("attributes") if self.endpoint else {}
        if not isinstance(attributes, dict):
            attributes = {}
        name = self.attribute_var.get().strip()
        self.current_value_var.set(display_value(attributes.get(name)))
        self.target_value_var.set(display_value(self.attribute_value_var.get()))

    def _clear_endpoint_view(self) -> None:
        """清除與舊連線綁定的 Endpoint 資料，但保留固定 attribute 選項。"""
        self.endpoint_id_var.set("—")
        self.endpoint_status_var.set("—")
        self.attribute_combo.configure(
            values=attribute_name_options(
                {}, self.initial_settings.default_attribute_name
            )
        )
        self.current_value_var.set(NOT_SET)
        self._refresh_action_states()

    def _run_async(
        self,
        label: str,
        operation: Callable[[], Any],
        on_success: Callable[[Any], None],
    ) -> None:
        """在 daemon 執行緒執行網路工作，並把結果放入主執行緒佇列。"""
        if self.busy:
            return
        self.busy = True
        self._set_status(f"{label}中…")
        self.progress.start(10)
        self._refresh_action_states()

        def worker() -> None:
            """捕捉背景工作結果；不得在此執行緒直接操作 Tk 元件。"""
            try:
                result = operation()
            except Exception as error:  # handled on the Tk main thread
                self.result_queue.put((False, label, error, None))
            else:
                self.result_queue.put((True, label, result, on_success))

        threading.Thread(target=worker, name="clearpass-request", daemon=True).start()

    def _poll_results(self) -> None:
        """由 Tk 定時輪詢背景結果，並在主執行緒更新元件或顯示錯誤。"""
        if self.closing:
            return
        try:
            while True:
                succeeded, label, payload, callback = self.result_queue.get_nowait()
                self.busy = False
                self.progress.stop()
                self._refresh_action_states()
                if succeeded:
                    try:
                        callback(payload)
                    except Exception as error:
                        self._show_operation_error(label, error)
                else:
                    self._show_operation_error(label, payload)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_results)

    def _show_operation_error(self, label: str, error: Exception) -> None:
        """將內部例外轉成安全訊息，並區分 OAuth 與 Endpoint 操作狀態。"""
        if isinstance(error, (ClearPassError, ClearPassResponseError, ConfigurationError, ValidationError)):
            message = str(error)
        else:
            message = "程式發生未預期錯誤"
        if isinstance(error, ClearPassError) and error.status_code == 404:
            message = "ClearPass 找不到此 MAC Address 對應的 Endpoint"
        is_oauth_error = "OAuth" in label or (
            isinstance(error, ClearPassError) and error.status_code == 401
        )
        # Endpoint 404/403 不代表 OAuth 失效，因此只有認證錯誤才將徽章設為紅色。
        if is_oauth_error:
            self.oauth_authenticated = False
            self._set_connection_status("OAuth 連線失敗", state="error")
        elif self.oauth_authenticated:
            self._set_connection_status("OAuth 連線成功", state="success")
        else:
            self._set_connection_status("連線/操作失敗", state="error")
        self._log(f"{label}失敗｜{message}", "error")
        self._set_status(message, error=True)
        messagebox.showerror(f"{label}失敗", message, parent=self.root)

    def _set_connection_status(self, message: str, state: str = "neutral") -> None:
        """以 neutral、success 或 error 配色更新 OAuth 狀態徽章。"""
        colors = {
            "neutral": (BORDER, MUTED),
            "success": (GREEN_SOFT, GREEN),
            "error": (RED_SOFT, RED),
        }
        background, foreground = colors[state]
        self.connection_status_var.set(message)
        self.connection_badge.configure(background=background, foreground=foreground)

    def _refresh_action_states(self) -> None:
        """依背景工作與 Endpoint 狀態啟用或停用可執行操作。"""
        basic_state = "disabled" if self.busy else "normal"
        self.test_connection_button.configure(state=basic_state)
        self.query_button.configure(state=basic_state)
        if self.endpoint and not self.busy:
            self.attribute_combo.configure(state="normal")
            self.attribute_value_combo.configure(state="readonly")
            self.update_button.configure(state="normal")
        else:
            self.attribute_combo.configure(state="disabled")
            self.attribute_value_combo.configure(state="disabled")
            self.update_button.configure(state="disabled")

    def _set_status(self, message: str, error: bool = False) -> None:
        """更新底部狀態文字；error 參數保留給未來錯誤樣式擴充。"""
        self.status_var.set(message)

    def _log(self, message: str, tag: str | None = None) -> None:
        """將帶本機時間戳的訊息附加至只讀操作紀錄。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_text.configure(state="normal")
        self.activity_text.insert("end", f"{timestamp}  {message}\n", tag or "")
        self.activity_text.see("end")
        self.activity_text.configure(state="disabled")

    def _close(self) -> None:
        """停止輪詢、關閉 HTTP session 並銷毀 Tk 視窗。"""
        self.closing = True
        if self.client:
            self.client.close()
        self.root.destroy()


def run_app(
    settings: Settings | None = None,
    client_factory: Callable[[Settings], ClearPassClient] = ClearPassClient,
) -> None:
    """載入設定、建立主視窗並進入 Tk 事件迴圈。"""
    startup_error = ""
    if settings is None:
        try:
            settings = Settings.from_environment()
        except ConfigurationError as error:
            startup_error = str(error)
            settings = Settings()

    root = tk.Tk()
    app = ClearPassDesktopApp(root, settings, client_factory)
    if startup_error:
        root.after(
            150,
            lambda: messagebox.showwarning(
                "設定檔內容錯誤", startup_error, parent=root
            ),
        )
    app.mac_entry.focus_set()
    root.mainloop()
