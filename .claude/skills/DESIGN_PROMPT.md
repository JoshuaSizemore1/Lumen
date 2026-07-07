Design the UI for "Lumen" — a local-first daily assistant app that lives on a Linux desktop (Hyprland/Wayland, tiling window manager). It manages email, calendar, todos, and a personal book catalog, and answers general questions, all through a local LLM running in the background. The eventual implementation is PyQt6, so favor layouts and components that translate cleanly to a native desktop toolkit — no web-only patterns like hover-reveal navigation or scroll-jacking.

## Design direction
- Minimalist, developer-friendly, purposeful. No decorative chrome, no gradients-for-the-sake-of-it, no skeuomorphism.
- Dark theme by default, matching a typical tiling-WM aesthetic (think: terminal emulator, waybar, rofi/wofi launcher — flat, low-contrast surfaces with a single sharp accent color for interactive/important elements).
- Monospace or near-monospace type for data-dense areas (email lists, event times, book lists); a clean sans-serif is fine for prose/chat responses.
- Keyboard-first interaction should feel like the primary mode, mouse-first should feel secondary. Visually communicate this (visible keybind hints, command-palette-style input) rather than just relying on it working.
- Low visual weight when idle — this app should feel like it's barely there until you need it, then get out of the way again.

## Screens to design

1. **Quick-launcher (primary surface)** — invoked by a global hotkey, appears as a centered overlay/palette (like a spotlight/rofi launcher). Single input field for free-text queries — anything from "what's on my calendar today" to "add a todo: call the dentist" to "recommend a book like the last two I finished" to a general knowledge question. Shows a compact response inline below the input — text answer, or a short list (emails/events/todos/book suggestions) depending on query type. Should support both "answer and disappear" and "answer and let me keep typing" flows.

2. **Dashboard / tray dropdown** — a small panel from the tray icon showing: today's calendar at a glance, a handful of recent/unread emails, and open todos. Glanceable, not a full app window — this is a "check in for 5 seconds" surface, not a place to do work. Links out to the fuller Todo Manager and Book Catalog screens below for anything needing direct manipulation.

3. **Todo manager (dedicated view)** — a direct-manipulation list, not a chat interface: an input to add a todo, items grouped by due date (today / upcoming / no date), checkboxes to mark complete, a delete affordance per item. This is the "manage it yourself" surface — no LLM round-trip needed to check something off.

4. **Book catalog** — two parts on one screen: (a) your reading log — a list of books with title, author, date finished, a star rating, and free-text notes, plus a simple form to add a new entry; (b) a recommendations panel below or beside it, showing books the LLM has surfaced based on your catalog, each with a one-line rationale tied to specific books you've logged, and a clear visual distinction between "books you've read" and "suggested next reads" so the two never blur together.

5. **Confirmation dialog** — appears before any write action (sending an email, creating a calendar event, executing something the LLM proposed). Needs to clearly show exactly what will happen, with obvious confirm/cancel actions. This should feel deliberately slightly "heavier" than the rest of the UI since it's a safety checkpoint. Book catalog entries and todo edits do NOT need this — only actions that touch external accounts (email, calendar) do.

6. **Settings panel** — minimal: account connections (Gmail/Google Calendar), connected MCP servers (search, books lookup), model/idle-timeout config, sync interval. Should look like a config file rendered nicely, not a typical "Settings" app with sidebars and tabs — keep it flat and dense.

## Deliverable
HTML/React mockups showing all six screens (can be one file with view-switching, or separate). Include the actual component structure/hierarchy clearly enough that it can be reasonably translated to PyQt6 widgets afterward — favor standard layout primitives (stacks, grids, lists) over anything that only makes sense in a browser.
