"""FreQ design tokens — the single source of truth for the UI.

Never hardcode a hex value, font size, or corner radius in widget code:
add a token here instead. Values are grouped by purpose, not by hue.
"""

# ── Color ────────────────────────────────────────────────────────
COLORS = {
    # Surfaces (outside → inside, darkest → lightest)
    "bg":              "#0a0e14",   # app background
    "bg_input":        "#0d1219",   # entry fields / wells
    "bg_card":         "#131920",   # panels / cards
    "bg_hover":        "#1a2230",   # hover on flat surfaces
    "bg_selected":     "#1c3050",   # selection fill
    "playing_bg":      "#0f1e30",   # now-playing row tint

    # Strokes
    "border":          "#262d38",
    "border_focus":    "#3d4a5c",
    "playing_border":  "#1f4e7f",
    "selected_border": "#3d4a5c",

    # Text
    "text":            "#e8edf4",
    "text2":           "#8b95a5",   # secondary
    "text3":           "#4e5769",   # faintest

    # Brand accent
    "accent":          "#58a6ff",
    "accent_hover":    "#79b8ff",

    # Semantic — exactly ONE tone per meaning (hover variants included)
    "green":           "#3fb950",
    "green_hover":     "#2ea043",
    "green_dark":      "#238636",
    "red":             "#f85149",
    "red_hover":       "#da3633",
    "red_dark":        "#b62324",
    "red_muted":       "#5a2020",   # dimmed destructive state (muted mixer row)
    "orange":          "#d29922",   # warning / solo highlight
    "purple":          "#bc8cff",   # spectrum / special accents

    # Data-viz gradients
    "spectrum_1":      "#58a6ff",
    "spectrum_2":      "#bc8cff",
    "spectrum_3":      "#f778ba",

    # Progress-tint ramps (fill color by position fraction ≤0.6 / ≤0.85 / rest)
    "ramp_ok":         "#0f2a1a",
    "ramp_warn":       "#2a2200",
    "ramp_err":        "#2a0f0f",
    "wave_ok":         "#1a3a5a",
    "wave_warn":       "#4a3a0a",
    "wave_err":        "#4a1a1a",

    # Extra hover variants (named reality — one tone per family)
    "orange_hover":    "#e3a928",
    "purple_hover":    "#a371f7",

    # Queue category tags (jingle/ads color coding)
    "cat_jingle":      "#a855f7",
    "cat_ads":         "#f97316",

    # Waveform playback marker
    "waveform_played": "#2a5a8a",

    # Splash artwork scrim (pixel-composite constant)
    "splash_bg":       "#10141b",
    "splash_panel":    "#14181f",   # scrim melt color — must match the composite
    "splash_text_dim": "#5a6478",
    "splash_track":    "#232b3a",
}

# ── Typography ───────────────────────────────────────────────────
# Canonical type scale (px). Nothing outside this set — remap instead.
FONT_SIZES = {
    "xs":   10,   # captions, badges, meta
    "sm":   11,   # secondary UI text
    "md":   12,   # body / default controls
    "lg":   14,   # emphasized rows, section titles
    "xl":   16,   # tab headers / big labels
    "hero": 20,   # hero numbers / dialog titles
}

# ── Shape ────────────────────────────────────────────────────────
# corner_radius scale. Circles use half the widget size (e.g. 22 on a 44px
# button) and stay inline by design.
RADII = {
    "none": 0,
    "chip": 4,    # tiny buttons / hairline accents
    "sm":   6,    # compact rows
    "md":   8,    # default for buttons/cards
    "lg":   12,   # large panels
    "pill": 17,   # PillTabBar tabs
}
