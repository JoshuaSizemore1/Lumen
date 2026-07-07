# Classifies each incoming request as a direct LLM answer or a needs-connector-data
# request, dispatches to the relevant connector(s), and enforces the confirm-before-write
# flow for any send/create/modify/delete action before it reaches a connector's write path.
