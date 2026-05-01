from __future__ import annotations

import customtkinter as ctk


ctk.set_default_color_theme("blue")


def dual(light: str, dark: str) -> tuple[str, str]:
    return (light, dark)


FONT_FAMILY = "Microsoft YaHei UI"
CARD_RADIUS = 10
PANEL_RADIUS = 14


PALETTE = {
    "app_bg": dual("#f3f6fb", "#08111f"),
    "panel_bg": dual("#ffffff", "#111827"),
    "sidebar_bg": dual("#f9fbff", "#0b1220"),
    "sidebar_alt": dual("#eef4fa", "#121c2d"),
    "sidebar_hover": dual("#e4edf6", "#1b2a3f"),
    "sidebar_text": dual("#243244", "#d7e2ef"),
    "sidebar_heading": dual("#0f172a", "#f8fafc"),
    "primary": dual("#fb7299", "#fb7299"),
    "primary_hover": dual("#e85f88", "#ff85aa"),
    "accent": dual("#00a1d6", "#3bc7f3"),
    "accent_soft": dual("#e5f7fd", "#0b3344"),
    "muted": dual("#64748b", "#9aa7b8"),
    "text": dual("#0f172a", "#edf4ff"),
    "text_inverse": dual("#ffffff", "#ffffff"),
    "border": dual("#d8e2ec", "#28364a"),
    "surface": dual("#f6f9fc", "#172235"),
    "surface_alt": dual("#edf3f8", "#0f1a2b"),
    "input_bg": dual("#ffffff", "#0f172a"),
    "secondary": dual("#e7eef5", "#243247"),
    "secondary_hover": dual("#d6e1eb", "#334155"),
    "danger": dual("#dc2626", "#ef4444"),
    "danger_hover": dual("#b91c1c", "#dc2626"),
    "success": dual("#15803d", "#22c55e"),
    "warning": dual("#b45309", "#f59e0b"),
    "error": dual("#b91c1c", "#f87171"),
}


STATUS_STYLES = {
    "queued": (dual("#e8eef5", "#334155"), dual("#334155", "#e2e8f0")),
    "preparing": (dual("#e5f7fd", "#0b3344"), dual("#047da8", "#9ae6ff")),
    "downloading": (dual("#fce7f0", "#4a182b"), dual("#c0265c", "#ffb3c9")),
    "merging": (dual("#fef3c7", "#4a3007"), dual("#92400e", "#fde68a")),
    "completed": (dual("#dcfce7", "#12351f"), dual("#166534", "#bbf7d0")),
    "failed": (dual("#fee2e2", "#4a1515"), dual("#991b1b", "#fecaca")),
    "canceled": (dual("#e5e7eb", "#2f3a4a"), dual("#4b5563", "#d1d5db")),
}
