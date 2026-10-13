from __future__ import annotations

import customtkinter as ctk

from bili_client.ui.motion import mix_color, motion_for
from bili_client.ui.styles import CARD_RADIUS, FONT_FAMILY, PALETTE


class TextLabel(ctk.CTkLabel):
    def configure(self, require_redraw=False, **kwargs) -> None:
        super().configure(require_redraw=require_redraw, **kwargs)
        if hasattr(self, "_label"):
            self._label.lift()

    def _draw(self, no_color_updates=False) -> None:
        super()._draw(no_color_updates)
        # Deferred layouts can raise the rounded canvas over native Tk text.
        if hasattr(self, "_label"):
            self._label.lift()


class TextEntry(ctk.CTkEntry):
    def _draw(self, no_color_updates=False) -> None:
        super()._draw(no_color_updates)
        if hasattr(self, "_entry"):
            self._entry.lift()


class MotionButton(ctk.CTkButton):
    """Animate canvas colors without relaying out the button on every frame."""

    def __init__(self, *args, **kwargs) -> None:
        self._hovered = False
        self._display_color: str | None = None
        kwargs.setdefault("font", (FONT_FAMILY, 12))
        super().__init__(*args, **kwargs)

    def _base_color(self) -> str:
        color = self._fg_color if self._fg_color != "transparent" else self._bg_color
        return self._apply_appearance_mode(color)

    def _target_color(self) -> str:
        if self._hovered and self._hover and self._state == "normal" and self._hover_color is not None:
            return self._apply_appearance_mode(self._hover_color)
        return self._base_color()

    def _paint_color(self, color: str) -> None:
        self._display_color = color
        self._canvas.itemconfig("inner_parts", outline=color, fill=color)
        if self._text_label is not None:
            self._text_label.configure(bg=color)
        if self._image_label is not None:
            self._image_label.configure(bg=color)

    def _transition(self, duration_ms: int = 120) -> None:
        start = self._display_color or self._base_color()
        end = self._target_color()
        motion_for(self).tween(
            ("button", self), 0, 1, lambda value: self._paint_color(mix_color(start, end, value)), duration_ms
        )

    def _on_enter(self, event=None) -> None:
        self._hovered = True
        if self._state == "normal":
            self._transition()

    def _on_leave(self, event=None) -> None:
        self._hovered = False
        self._transition()

    def _clicked(self, event=None) -> None:
        if self._state != "disabled":
            self._paint_color(mix_color(self._target_color(), "#000000", 0.08))
            self._transition(180)
            if self._command is not None:
                self._command()

    def configure(self, require_redraw=False, **kwargs) -> None:
        color_changed = any(key in kwargs for key in ("state", "fg_color", "hover_color", "bg_color"))
        super().configure(require_redraw=require_redraw, **kwargs)
        if color_changed and hasattr(self, "_canvas"):
            self._transition()

    def _set_appearance_mode(self, mode_string) -> None:
        self._display_color = None
        super()._set_appearance_mode(mode_string)
        if hasattr(self, "_canvas"):
            motion_for(self).cancel(("button", self))
            self._paint_color(self._target_color())

    def destroy(self) -> None:
        motion_for(self).cancel(("button", self))
        super().destroy()


class MotionProgressBar(ctk.CTkProgressBar):
    def __init__(self, *args, **kwargs) -> None:
        self._display_value = 0.0
        self._target_value = 0.0
        super().__init__(*args, **kwargs)
        self._apply_value(0.0)

    def _apply_value(self, value: float) -> None:
        self._display_value = value
        super().set(value)

    def set(self, value: float, animate: bool = True) -> None:
        target = max(0.0, min(1.0, value))
        if target == self._target_value:
            return
        self._target_value = target
        motion = motion_for(self)
        if not animate or target < self._display_value or not self.winfo_viewable():
            motion.cancel(("progress", self))
            self._apply_value(target)
            return
        motion.tween(("progress", self), self._display_value, target, self._apply_value, 300)

    def settle(self) -> None:
        motion_for(self).cancel(("progress", self))
        self._apply_value(self._target_value)

    def destroy(self) -> None:
        motion_for(self).cancel(("progress", self))
        super().destroy()


class Toast(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(
            master,
            fg_color=PALETTE["panel_bg"],
            corner_radius=CARD_RADIUS,
            border_width=1,
            border_color=PALETTE["border"],
        )
        self._dismiss_id = None
        self._offset = 0.0
        self.label = TextLabel(
            self,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=PALETTE["text"],
            wraplength=420,
            justify="left",
        )
        self.label.pack(side="left", padx=(18, 10), pady=14)
        MotionButton(
            self,
            text="×",
            width=28,
            height=28,
            fg_color="transparent",
            hover_color=PALETTE["secondary"],
            text_color=PALETTE["muted"],
            command=self.dismiss,
        ).pack(side="right", padx=(0, 10))

    def show(self, message: str, kind: str = "success") -> None:
        if self._dismiss_id is not None:
            self.after_cancel(self._dismiss_id)
        self.configure(border_color=PALETTE.get(kind, PALETTE["border"]))
        self.label.configure(text=message)
        self.place(relx=1, rely=1, x=-24, y=-12, anchor="se")
        self.lift()
        motion_for(self).tween(("toast", self), 14, 0, self._position, 200)
        self._dismiss_id = self.after(4200, self.dismiss)

    def _position(self, offset: float) -> None:
        self._offset = offset
        self.place(relx=1, rely=1, x=-24, y=round(-24 + offset), anchor="se")

    def dismiss(self) -> None:
        if self._dismiss_id is not None:
            self.after_cancel(self._dismiss_id)
            self._dismiss_id = None
        motion_for(self).tween(("toast", self), self._offset, 14, self._position, 120, self.place_forget)


def notify(widget, message: str, kind: str = "success") -> None:
    callback = getattr(widget.winfo_toplevel(), "notify", None)
    if callback is not None:
        callback(message, kind)


def wrap_to_panel(panel, labels, padding: int = 64) -> None:
    state = {"width": None, "timer": None}

    def resize():
        state["timer"] = None
        width = max(80, round(panel._reverse_widget_scaling(panel.winfo_width())) - padding)
        for label in labels:
            label.configure(wraplength=width)

    def changed(event):
        if event.width == state["width"]:
            return
        state["width"] = event.width
        if state["timer"] is None:
            state["timer"] = panel.after(40, resize)

    panel.bind("<Configure>", changed, add="+")
