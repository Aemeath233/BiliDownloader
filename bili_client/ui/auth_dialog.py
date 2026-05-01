from __future__ import annotations

import time
from typing import Callable

import customtkinter as ctk
import qrcode
from PIL import Image

from bili_client.services.auth_service import AuthError, AuthService, AuthSession, QrPollResult
from bili_client.ui.async_tasks import UiTaskRunner
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE, PANEL_RADIUS


class QrLoginDialog(ctk.CTkToplevel):
    POLL_INTERVAL_SECONDS = 2.0
    EXPIRE_SECONDS = 180.0

    def __init__(
        self,
        master,
        auth_service: AuthService,
        on_success: Callable[[AuthSession], None],
    ) -> None:
        super().__init__(master)
        self.auth_service = auth_service
        self.on_success = on_success
        self._closed = False
        self._qr_image: ctk.CTkImage | None = None
        self.ui_tasks = UiTaskRunner(self)

        self.title("扫码登录 Bilibili")
        self.geometry("460x620")
        self.minsize(460, 620)
        self.resizable(False, False)
        self.configure(fg_color=PALETTE["app_bg"])
        self.transient(master.winfo_toplevel())
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._close)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        container = ctk.CTkFrame(self, fg_color=PALETTE["panel_bg"], corner_radius=PANEL_RADIUS)
        container.grid(row=0, column=0, sticky="nsew", padx=20, pady=20)
        container.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            container,
            text="Bilibili 扫码登录",
            text_color=PALETTE["text"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        ).grid(row=0, column=0, sticky="ew", padx=18, pady=(20, 4))

        ctk.CTkLabel(
            container,
            text="用哔哩哔哩手机端扫码，确认后会自动保存登录态",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 18))

        qr_holder = ctk.CTkFrame(container, fg_color=PALETTE["surface"], corner_radius=CARD_RADIUS)
        qr_holder.grid(row=2, column=0, padx=34, pady=(0, 18))

        self.qr_label = ctk.CTkLabel(
            qr_holder,
            text="正在生成二维码...",
            width=260,
            height=260,
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        )
        self.qr_label.grid(row=0, column=0, padx=12, pady=12)

        self.status_label = ctk.CTkLabel(
            container,
            text="正在连接 Bilibili 登录接口",
            text_color=PALETTE["accent"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
        )
        self.status_label.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 10))

        ctk.CTkLabel(
            container,
            text="如果二维码过期，关闭窗口后重新点击扫码登录即可",
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))

        ctk.CTkButton(
            container,
            text="取消",
            command=self._close,
            height=42,
            corner_radius=CARD_RADIUS,
            fg_color=PALETTE["secondary"],
            hover_color=PALETTE["secondary_hover"],
            text_color=PALETTE["text"],
        ).grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 22))

        self.after(0, self._center_on_parent)
        self.after(100, self._start_worker)

    def _start_worker(self) -> None:
        self.ui_tasks.run("bilibili-qr-login", self._login_worker)

    def _login_worker(self) -> None:
        try:
            qr_info = self.auth_service.create_qr_login()
            self._post_ui(lambda: self._show_qr(qr_info.url))

            deadline = time.monotonic() + self.EXPIRE_SECONDS
            while not self._closed and time.monotonic() < deadline:
                result = self.auth_service.poll_qr_login(qr_info.qrcode_key)
                if result.code == 0 and result.session is not None:
                    self._post_ui(lambda session=result.session: self._finish_success(session))
                    return
                self._post_ui(lambda item=result: self._update_status(item))
                if result.code == 86038:
                    return
                time.sleep(self.POLL_INTERVAL_SECONDS)

            if not self._closed:
                self._post_ui(lambda: self.status_label.configure(text="二维码已过期，请重新打开扫码窗口", text_color=PALETTE["warning"]))
        except AuthError as exc:
            self._post_ui(lambda error=exc: self.status_label.configure(text=str(error), text_color=PALETTE["error"]))
        except Exception as exc:
            self._post_ui(lambda error=exc: self.status_label.configure(text=f"登录失败：{error}", text_color=PALETTE["error"]))

    def _show_qr(self, login_url: str) -> None:
        image = self._make_qr_image(login_url)
        self._qr_image = ctk.CTkImage(light_image=image, dark_image=image, size=(240, 240))
        self.qr_label.configure(image=self._qr_image, text="")
        self.status_label.configure(text="等待扫码", text_color=PALETTE["accent"])

    def _update_status(self, result: QrPollResult) -> None:
        if result.code == 86101:
            text = "等待扫码"
            color = PALETTE["accent"]
        elif result.code == 86090:
            text = "已扫码，请在手机端确认登录"
            color = PALETTE["warning"]
        elif result.code == 86038:
            text = "二维码已过期，请重新打开扫码窗口"
            color = PALETTE["warning"]
        else:
            text = result.message or f"登录状态：{result.code}"
            color = PALETTE["muted"]
        self.status_label.configure(text=text, text_color=color)

    def _finish_success(self, session: AuthSession) -> None:
        self.status_label.configure(text="登录成功，正在保存", text_color=PALETTE["success"])
        self.on_success(session)
        self._close()

    def _post_ui(self, callback: Callable[[], None]) -> None:
        if self._closed:
            return
        try:
            self.after(0, callback)
        except RuntimeError:
            self._closed = True

    def _close(self) -> None:
        self._closed = True
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _center_on_parent(self) -> None:
        self.update_idletasks()
        parent = self.master.winfo_toplevel()
        width = self.winfo_width()
        height = self.winfo_height()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    @staticmethod
    def _make_qr_image(content: str) -> Image.Image:
        qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
        qr.add_data(content)
        qr.make(fit=True)
        image = qr.make_image(fill_color="#0f172a", back_color="white").convert("RGB")
        return image.resize((240, 240), Image.Resampling.LANCZOS)
