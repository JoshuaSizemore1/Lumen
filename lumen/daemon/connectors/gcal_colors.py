"""Google Calendar's fixed event-colour palette. Its own module so the UIs
can read it without importing the daemon's router."""

# colorId -> display name: the edit dialog's colour picker and the update
# confirm dialog (#12).
GCAL_COLOR_NAMES = {
    "1": "Lavender", "2": "Sage", "3": "Grape", "4": "Flamingo", "5": "Banana",
    "6": "Tangerine", "7": "Peacock", "8": "Graphite", "9": "Blueberry",
    "10": "Basil", "11": "Tomato"}
# The hex Google renders each colorId as — so the picker's swatches match the
# calendar, and an event's own colour can be painted from the cache.
GCAL_COLOR_HEX = {
    "1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73",
    "5": "#f6bf26", "6": "#f4511e", "7": "#039be5", "8": "#616161",
    "9": "#3f51b5", "10": "#0b8043", "11": "#d50000"}
