"""
RZ Automedata - Theme Constants & Utilities
Shared color palette, preview settings, and helper functions used across all UI modules.
"""

from PIL import Image

# ─── Theme Colors ────────────────────────────────────────────────────────────────
COLORS = {
    "bg_darkest":       "#060918",
    "bg_dark":          "#0a0e27",
    "bg_card":          "#0f1538",
    "bg_card_hover":    "#151d4a",
    "bg_input":         "#0c1230",
    "border":           "#1a2555",
    "border_glow":      "#0066ff",
    "neon_blue":        "#00d4ff",
    "neon_blue_dim":    "#0099cc",
    "accent_blue":      "#0066ff",
    "accent_purple":    "#7b2fff",
    "text_primary":     "#e8eaff",
    "text_secondary":   "#8890b5",
    "text_muted":       "#4a5280",
    "success":          "#00ff88",
    "error":            "#ff4466",
    "warning":          "#ffaa00",
    "stop_red":         "#ff2244",
    "clear_orange":     "#ff8800",
    "table_border":     "#1e2d6a",
    "table_row_even":   "#0c1235",
    "table_row_odd":    "#0f1740",
    "table_header":     "#111a45",
}

# Preview thumbnail size (small + compressed for speed)
PREVIEW_SIZE = (64, 48)


def compress_preview(img, max_size=PREVIEW_SIZE, quality=70):
    """Compress and resize preview image to reduce memory and speed up loading."""
    if img is None:
        return None
    img = img.copy()
    img.thumbnail(max_size, Image.LANCZOS)
    # Convert to RGB if RGBA
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, (12, 18, 48))
        bg.paste(img, mask=img.split()[3])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img
