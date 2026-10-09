# ui_v4 — Field Notes

A redesign of the Lumen desktop UI in the Field Notes style: calm, editorial,
paper-and-ink grounds with a forest-green accent, Fraunces headings and
Instrument Sans body text (both bundled in `fonts/`). It talks to the same
daemon through the same `AppState` as ui_v3, so every feature and every
write-confirmation flow is unchanged. Only the look and the layout are new.

**ui_v3 is still the default.** `lumen`, `lumen-ui` and `lumen-ui-v3` all
launch ui_v3. You can run v4 alongside it: each version uses its own
single-instance socket.

## Launch

```sh
uv run lumen-ui-v4
# or
uv run python -m lumen.ui_v4
```

Passing `--toggle-launcher` (or sending that command to a running v4) opens
the window with the Ask bar focused. v4 has no separate launcher overlay.

## Layout

A 232px sidebar sits on the left and the current screen fills the rest. An
**Ask Lumen** bar runs along the bottom of every screen except Ask and
Settings.

- **Lumen**, with the caption "Local assistant" underneath
- **Today**
- *Daily*: **Inbox** (shows an unread badge), **Calendar**, **Todos** (shows
  the open count)
- *Library*: **Books**, **Files**, **Canvas**
- **Ask Lumen**: the one conversation. A question typed into the Ask bar
  moves here, along with the page you asked from.
- Footer: **Settings**, the theme toggle, the model status (a dot and a
  word) and today's date.

When the window is narrower than 900px, the sidebar shrinks to a 64px rail of
icons. Hover an icon to see its tooltip.

## Shortcuts

| Keys | Action |
|---|---|
| Ctrl+1 … Ctrl+8 | Today, Inbox, Calendar, Todos, Books, Files, Canvas, Ask Lumen |
| Ctrl+, | Settings |
| Ctrl+K | Command palette: go to any screen, add a todo, compose, add an event, ask, switch theme |
| Ctrl+L | Focus the Ask bar |
| Alt+Left / Alt+Right | Back / forward, also available as a two-finger swipe |
| Esc | Close the open dialog |

## Theme

v4 follows your system's light/dark setting by default. The theme item in the
sidebar footer steps through **system → light → dark → system**. Your choice
is saved in `QSettings("lumen", "ui_v4")` under `theme`. Screens repaint
straight away when the theme changes, including when the OS setting changes
while you're in system mode. Set `LUMEN_REDUCED_MOTION=1` to turn off
animation.

## Code map

- `app.py`: starts the app (WebEngine setup, QApplication, fonts and theme,
  daemon clients, tray, event loop)
- `main.py`: `LumenWindow` (sidebar, lazily built screens, Ask bar, history)
  and `build_window()`
- `theme.py`: design tokens. This is the only file allowed to contain colour
  literals.
- `styles.py`: the stylesheet, driven by objectName and properties
- `components.py`: the building blocks screens are made from
- `icons.py`: the outlined icon set
- `overlays.py`: confirm, compose, event, rule, label review, command palette
  and toast
- `screens/`: one module per destination. The contract is documented in
  `screens/__init__.py`.
