"""Colour palette, theme definitions and shared stylesheet builders.

Every literal colour and stylesheet string that used to be scattered through the
UI code lives here, so restyling happens in one place.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Brand ---
ACCENT_RED = "#881122"

# Kept as an alias because saved configs and older code refer to this name.
NEW_RED = ACCENT_RED


@dataclass(frozen=True)
class Palette:
    """The set of colours that make up one UI theme."""

    background: str
    content_box: str
    sidebar: str
    selection: str
    text: str
    border: str


DARK = Palette(
    background="#1A1A1A",
    content_box="#111111",
    sidebar="#111111",
    selection="#2C2C2C",
    text="#FFFFFF",
    border="#2C2C2C",
)

LIGHT = Palette(
    background="#FFFFFF",
    content_box="#F0F0F0",
    sidebar="#F0F0F0",
    selection="#DCDCDC",
    text="#000000",
    border="#DCDCDC",
)

THEMES = {"Dark": DARK, "Light": LIGHT}
DEFAULT_THEME = "Dark"


def palette_for(theme_name: str) -> Palette:
    """Return the palette for ``theme_name``, falling back to the dark theme."""
    return THEMES.get(theme_name, DARK)


# --- Stylesheet builders ---
# These produce byte-for-byte the same CSS the original inline f-strings did.

def slider_style(handle_color: str) -> str:
    return (
        "QSlider::groove:horizontal{height:6px;background:#333;border-radius:3px}"
        f"QSlider::handle:horizontal{{width:12px;background:{handle_color};"
        "margin:-4px 0;border-radius:6px}"
    )


def color_button_style(color: str) -> str:
    return f"background-color:{color};border-radius:5px;"


def keybind_button_style() -> str:
    return "background:#333;color:#FFF;border:1px solid #555;border-radius:3px;padding:5px;"


def section_content_style(border_color: str) -> str:
    return (
        f"background-color: transparent; border-left: 2px solid {border_color}; "
        "margin-left: 7px; padding-left: 10px; padding-top: 10px;"
    )


def content_box_style(background: str) -> str:
    return f"background-color: {background}; border-radius: 15px;"


def page_title_style(text_color: str) -> str:
    return f"color:{text_color};font-size:24px; background: transparent;"


def nav_list_style() -> str:
    return (
        "QListWidget, QListWidget::item { border: none; background: transparent; "
        "padding: 0px; outline: 0; }"
    )


def arraylist_label_style(color: str, font_size: int, style_name: str) -> str:
    """Build the arraylist entry stylesheet for one of the three list styles."""
    base = f"color: {color}; font-size: {font_size}pt;"
    if style_name == "Classic":
        return f"{base} background: rgba(0,0,0,0.6); border-radius: 4px; padding: 4px 10px;"
    if style_name == "Edged":
        return (
            f"{base} background: rgba(0,0,0,0.6); border-radius: 4px; "
            f"border-left: 3px solid {color}; padding: 4px 10px;"
        )
    return f"{base} background: transparent; padding: 4px 10px;"


# --- UI option lists (single source of truth for dropdowns + validation) ---
AIM_PRIORITIES = ("Proximity", "Size")
AIM_POSITIONS = ("Body", "Head", "Custom")
ESP_MODES = ("Box", "Corner Box", "Head Circle", "Pixel")
ARRAYLIST_STYLES = ("Default", "Classic", "Edged")
CROSSHAIR_STYLES = ("Cross", "Dot", "Circle", "Cross + Dot")
THEME_NAMES = tuple(THEMES)
