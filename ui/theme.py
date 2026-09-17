"""One visual language for the GCS, in the manner of a ground control station.

Dark, because these are used in the field and on long watches: a bright panel
beside a satellite map is glare, and it wrecks night vision.

The rules the rest of the UI follows:

  Labels name a field. Uppercase, letter-spaced, muted, never bold — they are
  signposts, not content.
  Values are the content. Brighter than their label, and numbers are set in a
  monospace face so columns line up and a digit changing is visible without
  reading the number.
  Colour means state, and nothing else. It is never decoration, so a coloured
  thing on screen always means something.
  Sections are marked by a coloured edge rather than an icon. An icon has to
  be learned; a consistent edge colour is read at a glance, and it survives
  being small.

Icons and emoji are avoided. At panel size they are noise, they carry a
different meaning to everyone reading them, and they render differently on
every machine — a word does none of that.
"""

from html import escape as html_escape

# --- surfaces --------------------------------------------------------------
BG = "#12161c"            # the window behind everything
SURFACE = "#1a1f27"       # a panel sitting on it
SURFACE_RAISED = "#222932"  # a card on the panel
SURFACE_HOVER = "#2a323d"
BORDER = "#2a323d"
BORDER_STRONG = "#3a4553"

# --- text ------------------------------------------------------------------
TEXT = "#e6edf3"          # values, headings
TEXT_DIM = "#9aa7b4"      # labels, secondary facts
TEXT_MUTED = "#6b7885"    # asides, units, timestamps

# --- state -----------------------------------------------------------------
# Threat bands, warm for worse, in the order an operator reads them.
BAND_COLOURS = {
    "CRITICAL": "#ff4d4f",
    "HIGH": "#ff8c1a",
    "MODERATE": "#ffc53d",
    "LOW": "#52c41a",
    "UNSCORED": "#6b7885",
}
BAND_DEFAULT = "#6b7885"

ACCENT_EQUIPMENT = "#f59e0b"   # what the object is
ACCENT_RESPONSE = "#4dabf7"    # what would be sent
ACCENT_CHECKPOINT = "#2dd4bf"  # what is at risk
ACCENT_OK = "#52c41a"
ACCENT_ALERT = "#ff4d4f"

# --- type ------------------------------------------------------------------
# Two families only: one to read, one to compare. The stacks name what is
# actually installed on Windows and Linux, so neither falls back to something
# that breaks the column alignment.
FONT_UI = "'Segoe UI', 'Inter', 'DejaVu Sans', sans-serif"
FONT_MONO = "'Cascadia Mono', 'Consolas', 'DejaVu Sans Mono', monospace"

SIZE_TITLE = 13
SIZE_BODY = 12
SIZE_SMALL = 11
SIZE_TINY = 10


def _upper(text):
    """Uppercase, then escape.

    The order matters: escaping first and uppercasing after turns &lt; into
    &LT;, which parsers decode back to '<'. Styling would then undo the
    escaping. Everything here that uppercases does it through this.
    """
    return html_escape(str(text).upper())


def label(text, size=SIZE_TINY, colour=None):
    """A field name: uppercase, spaced out, quiet."""
    return (f"<span style=\"font-family:{FONT_UI};font-size:{size}px;"
            f"color:{colour or TEXT_MUTED};letter-spacing:0.8px;\">"
            f"{_upper(text)}</span>")


def value(text, size=SIZE_SMALL, colour=None, mono=True, bold=False):
    """A field's content, monospaced by default so columns align.

    Does NOT escape: it is also given text already built here, such as two
    values joined together. Escape external data before passing it in —
    ``rows`` below does that for you.
    """
    family = FONT_MONO if mono else FONT_UI
    weight = "600" if bold else "400"
    return (f"<span style=\"font-family:{family};font-size:{size}px;"
            f"color:{colour or TEXT};font-weight:{weight};\">{text}</span>")


def chip(text, colour, filled=False):
    """A short state word, boxed so it reads as a status and not prose."""
    if filled:
        return (f"<span style=\"font-family:{FONT_UI};font-size:{SIZE_TINY}px;"
                f"background:{colour};color:{BG};padding:1px 6px;"
                f"border-radius:2px;font-weight:600;letter-spacing:0.6px;\">"
                f"{_upper(text)}</span>")
    return (f"<span style=\"font-family:{FONT_UI};font-size:{SIZE_TINY}px;"
            f"color:{colour};border:1px solid {colour};padding:0px 5px;"
            f"border-radius:2px;letter-spacing:0.6px;\">{_upper(text)}</span>")


def section_style(accent):
    """Stylesheet for a block, marked by a coloured edge rather than an icon."""
    return (f"background:{SURFACE_RAISED};"
            f"border-left:2px solid {accent};"
            f"padding:7px 9px;")


def bar(fraction, colour, height=4):
    """A proportion, as a bar. Zero draws an empty track, not a full one."""
    filled = max(0.0, min(100.0, fraction))
    cells = ""
    if filled > 0:
        cells += (f"<td width=\"{filled:.0f}%\" style=\"background:{colour};"
                  f"font-size:1px;line-height:{height}px;\">&nbsp;</td>")
    if filled < 100:
        cells += (f"<td width=\"{100 - filled:.0f}%\" style=\"background:"
                  f"{BORDER};font-size:1px;line-height:{height}px;\">&nbsp;</td>")
    return (f"<table cellspacing=\"0\" cellpadding=\"0\" width=\"100%\" "
            f"style=\"margin:5px 0 6px 0;\"><tr>{cells}</tr></table>")


def rows(pairs, label_colour=None, value_colour=None):
    """A label/value table: names on the left, figures right-aligned.

    Both sides are plain text and are escaped here, so callers hand over data
    rather than markup and cannot forget.

    The labels here sit on a raised surface and are read, not merely skimmed
    past, so they use the brighter dim tone rather than the muted one: muted
    is for asides, and against SURFACE_RAISED it falls under the contrast a
    field name needs at this size.
    """
    body = ""
    for name, text in pairs:
        body += (f"<tr>"
                 f"<td>{label(str(name), colour=label_colour or TEXT_DIM)}</td>"
                 f"<td align=\"right\">"
                 f"{value(html_escape(str(text)), colour=value_colour)}</td>"
                 f"</tr>")
    return (f"<table cellspacing=\"0\" cellpadding=\"1\" width=\"100%\">"
            f"{body}</table>")


def heading(text, accent, trailing=""):
    """A section title, with an optional figure at the right."""
    left = (f"<span style=\"font-family:{FONT_UI};font-size:{SIZE_TINY}px;"
            f"color:{accent};letter-spacing:1.1px;font-weight:600;\">"
            f"{_upper(text)}</span>")
    if not trailing:
        return f"<div style=\"margin-bottom:3px;\">{left}</div>"
    return (f"<table cellspacing=\"0\" cellpadding=\"0\" width=\"100%\" "
            f"style=\"margin-bottom:3px;\"><tr><td>{left}</td>"
            f"<td align=\"right\">{trailing}</td></tr></table>")


def note(text, colour=None):
    """An aside: smaller and quieter than the content it follows."""
    return (f"<div style=\"font-family:{FONT_UI};font-size:{SIZE_TINY}px;"
            f"color:{colour or TEXT_MUTED};margin-top:3px;\">{text}</div>")


# --- application stylesheet ------------------------------------------------
APP_STYLESHEET = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: {FONT_UI};
    font-size: {SIZE_BODY}px;
}}
QToolBar {{
    background: {SURFACE};
    border-bottom: 1px solid {BORDER};
    padding: 3px;
    spacing: 3px;
}}
QToolBar QToolButton {{
    color: {TEXT_DIM};
    padding: 5px 12px;
    border: 1px solid transparent;
    border-radius: 2px;
}}
QToolBar QToolButton:hover {{
    color: {TEXT};
    background: {SURFACE_HOVER};
    border: 1px solid {BORDER_STRONG};
}}
QStatusBar {{
    background: {SURFACE};
    color: {TEXT_DIM};
    border-top: 1px solid {BORDER};
    font-family: {FONT_MONO};
    font-size: {SIZE_SMALL}px;
}}
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 3px;
    margin-top: 8px;
    padding-top: 6px;
    font-size: {SIZE_TINY}px;
    color: {TEXT_MUTED};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    letter-spacing: 1px;
}}
QScrollArea {{ border: none; background: {BG}; }}
QScrollBar:vertical {{
    background: {BG}; width: 9px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {TEXT_MUTED}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; }}
QLabel {{ background: transparent; }}
QDialog {{ background: {SURFACE}; }}
QComboBox, QLineEdit {{
    background: {SURFACE_RAISED};
    border: 1px solid {BORDER_STRONG};
    border-radius: 2px;
    padding: 4px 6px;
    color: {TEXT};
}}
QComboBox:focus, QLineEdit:focus {{ border: 1px solid {ACCENT_RESPONSE}; }}
QComboBox QAbstractItemView {{
    background: {SURFACE_RAISED};
    color: {TEXT};
    selection-background-color: {SURFACE_HOVER};
}}
"""
