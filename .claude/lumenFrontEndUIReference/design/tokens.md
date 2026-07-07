# Lumen — Design Tokens

Extracted from the HTML/React mockups for use when writing PyQt6 QSS stylesheets.
All values are literal (hex + px) so screens can be styled without re-deriving them from CSS.

> **Accent is themeable.** The mockups default to Tokyo-Night blue `#7aa2f7`. The app
> ships an accent picker (blue / green / amber / purple / pink). Treat `--ac` as a single
> variable threaded through QSS — everything else is fixed.

---

## Color palette

### Surfaces (dark, low-contrast, flat)
| Token            | Hex        | Use                                                        |
|------------------|------------|------------------------------------------------------------|
| `bg.app`         | `#0d0e14`  | Desktop / app backdrop behind the window                   |
| `bg.window`      | `#1a1b26`  | Main window body                                           |
| `bg.chrome`      | `#16161e`  | Title bar + tab bar                                        |
| `bg.overlay`     | `#101018`  | Launcher / dashboard canvas (slightly off the window)      |
| `bg.panel`       | `#171821`  | Cards, launcher palette, dashboard blocks, add-forms       |
| `bg.panel.alt`   | `#14151d`  | Inset headers, footers, calendar grid background           |
| `bg.field`       | `#0f1017`  | Text inputs / selects inside panels                        |
| `bg.inset`       | `#12131b`  | Confirmation "what will happen" box                        |
| `bg.waybar`      | `#0c0d13`  | Faux status bar strip at top of launcher/dashboard         |

### Borders / dividers
| Token            | Hex        | Use                                                        |
|------------------|------------|------------------------------------------------------------|
| `border.strong`  | `#2a2f45`  | Panel borders, inputs, tab-bar segments                    |
| `border.med`     | `#262838`  | Confirmation dialog internal dividers                      |
| `border.soft`    | `#20222e`  | Section dividers inside chrome/panels                      |
| `border.faint`   | `#1a1c26`  | List-row separators                                        |
| `border.faintest`| `#1e202c`  | Book-log row separators                                    |

### Text
| Token            | Hex        | Use                                                        |
|------------------|------------|------------------------------------------------------------|
| `text.primary`   | `#c0caf5`  | Primary content, headings, active tab                      |
| `text.secondary` | `#a9b1d6`  | Secondary content, list subjects                           |
| `text.muted`     | `#787c99`  | Notes, previews, tertiary                                  |
| `text.dim`       | `#565f89`  | Labels, metadata, inactive tabs, timestamps                |
| `text.faint`     | `#3b4261`  | Section eyebrows, keybind hints, disabled                  |
| `text.ghost`     | `#2a2f45`  | Footnotes / captions on canvases                           |

### Accent (default = blue; swappable)
| Token            | Hex        | Use                                                        |
|------------------|------------|------------------------------------------------------------|
| `accent`         | `#7aa2f7`  | Interactive/important: active tab underline, prompt caret, primary buttons, "now" markers |
| `accent.soft`    | `#7aa2f71f`| ~12% accent — button fills, active segment bg, event blocks|
| `accent.mid`     | `#7aa2f733`| ~20% accent — text selection, "next event" block bg        |

Accent options: blue `#7aa2f7` · green `#9ece6a` · amber `#e0af68` · purple `#bb9af7` · pink `#f7768e`.

### Semantic / status
| Token            | Hex        | Use                                                        |
|------------------|------------|------------------------------------------------------------|
| `status.ok`      | `#9ece6a`  | Connected, synced, success toast, completed-todo check     |
| `status.warn`    | `#e0af68`  | Star ratings, "upcoming" due chips, write-action caution   |
| `status.now`     | `#f7768e`  | "Now" line on the calendar                                 |
| `status.info`    | `#7dcfff`  | Config numbers, "answer" response header                   |
| `accent.book`    | `#bb9af7`  | Book recommendations accent (distinct from read log)       |
| `accent.book.dim`| `#8f83ac`  | Recommendation rationale text                              |
| `tag.new`        | `#ff9e64`  | Freshly-added todo tag                                     |

### Todo tag colors (chips)
`work #7aa2f7` · `personal #bb9af7` · `home #9ece6a` · `admin #e0af68` · `tinker #7dcfff` · `new #ff9e64`
Chip border: `#2a2f45`, fallback text `#565f89`.

---

## Typography

Two families only.

| Role        | Family                              | Notes                                              |
|-------------|-------------------------------------|----------------------------------------------------|
| Mono (default) | **JetBrains Mono**, weights 400–700 | Everything data-dense: lists, times, tables, chrome, config, launcher input |
| Sans (prose)| **IBM Plex Sans**, weights 400–600  | Chat/answer bodies, book notes, recommendation rationale, email body, dialog intro. In mockups this is the `.rl-sans` class |

Nothing uses a serif. Avoid Inter/Roboto/Arial.

### Type scale (px)
| Size | Use                                                            |
|------|----------------------------------------------------------------|
| 18   | Email subject headline (reading pane)                          |
| 17   | Dashboard date heading                                         |
| 16   | Launcher input; screen `<h2>` titles                           |
| 15   | Confirmation dialog "heavier" feel; gear icon                  |
| 14   | Answer body; book title; dialog title; email body             |
| 13.5 | Todo item text; add-entry inputs                               |
| 13   | List primary text; buttons                                     |
| 12.5 | Config lines, notes, secondary body                            |
| 12   | Tab labels, list secondary, metadata rows                     |
| 11   | Section actions, small buttons, chips-context                  |
| 10.5 | Eyebrows, keybind hints, status text                           |
| 10   | Section eyebrow labels (letter-spaced 1px)                     |
| 9.5  | Calendar hour ticks, event block time                          |

Minimum readable size is ~9.5px and only for the calendar gutter — keep body ≥12px.

---

## Spacing & layout

- **Window:** 1320px wide, `border-radius: 11px`, 1px `border.strong`, big soft shadow.
- **Title bar height:** 38px. **Tab bar height:** 38px. **Faux waybar:** 24px.
- **Content viewport height in mockups:** 722px (scrolls internally).
- **Panel radius:** 8–10px. **Button/segment radius:** 5–7px. **Chip radius:** 3px. **Dialog radius:** 11px.
- **Standard gaps:** 8 / 10 / 11 / 12 px between related items; 20–24px between screen sections.
- **Screen padding:** ~20–26px around dedicated views.
- **List row vertical padding:** 6–10px (density-dependent — see below).
- **Density presets** (app-level): `compact` rowPy 4px · `cozy` (default) 7px · `comfortable` 10px.

### Key layout grids (translate to QVBoxLayout / QHBoxLayout / QGridLayout)
- **Window shell:** vertical stack → [title bar] · [tab bar] · [stacked view area]. Tab bar is a QHBoxLayout; the gear is pushed right with a stretch/`margin-left:auto`.
- **Dashboard:** 3-column grid `290px | 1fr | 320px` → [today's todos] · [day calendar] · [unread mail].
- **Mail:** 2-column split `334px | 1fr` → [message list] · [reading pane].
- **Books:** flex row → [reading log ≥420px, grows] · [recommendations 360px fixed].
- **Todos:** single centered column, max-width 820px, grouped Today / Upcoming / No date.
- **Settings:** single centered column, max-width 760px, flat config sections.
- **Launcher / dialog:** centered overlay; palette 620px, dialog 480px.

---

## Components → PyQt6 widget hints

| Mockup element        | PyQt6 translation                                             |
|-----------------------|---------------------------------------------------------------|
| Tab bar               | Custom QPushButtons w/ bottom-border on `checked`, or QTabBar restyled |
| Gear (settings)       | Flat QToolButton, right-aligned via stretch                   |
| Stacked views         | QStackedWidget                                                |
| Todo item             | QCheckBox + QLabel + tag QLabel + delete QToolButton in a row |
| Launcher palette      | Frameless QDialog, QLineEdit + results QListWidget/QVBox below |
| Calendar day view     | Absolute-positioned event QFrames over an hour-grid QWidget (custom paint or manual geometry) |
| Toggle switch         | Custom QCheckBox styled as pill, or QAbstractButton           |
| Confirmation dialog   | Modal QDialog; key/value rows in a QFormLayout inside a framed box |
| Toast                 | Frameless QLabel, auto-dismiss QTimer (~2.6s)                  |
| Star rating           | `★`/`☆` QLabel string, color `#e0af68`                        |

---

## Interaction principles (carry into PyQt6)

- **Keyboard-first.** Visible keybind hints (`↵ run`, `⌘↵ keep open`, `↑↓ nav`, `esc`, `super+space`). Number keys 1–6 select tabs.
- **Confirmation only for external writes** (email send, calendar create). Todo/book edits are direct, no dialog.
- **Low idle weight.** Flat surfaces, one accent, no gradients (except the one intentional dashed recommendation panel to separate "suggested" from "read").
- **Selection cues** are a left accent border + `accent.soft` fill, not heavy highlights.
