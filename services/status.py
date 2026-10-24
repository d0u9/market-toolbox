"""Status values shared by the worker, the database and the page."""

# Update status of a symbol (symbol_state.status).
UPDATED = "updated"
WAITING = "waiting"
FAILED = "failed"
INITIALIZING = "initializing"

# Outcome of one update attempt (update_runs.status). A failed attempt records
# the symbol status it led to (WAITING or FAILED).
RUN_RUNNING = "running"
RUN_PUBLISHED = "published"
RUN_UNCHANGED = "unchanged"
RUN_ABANDONED = "abandoned"

# Data quality of a published snapshot.
QUALITY_COMPLETE = "complete"
QUALITY_GAP = "history_gap"
QUALITY_INSUFFICIENT = "insufficient_history"

STATUS_TEXT = {
    UPDATED: "Updated",
    WAITING: "Waiting for data",
    FAILED: "Update failed",
    INITIALIZING: "Initializing",
}
QUALITY_TEXT = {
    QUALITY_COMPLETE: "Complete",
    QUALITY_GAP: "History gap",
    QUALITY_INSUFFICIENT: "Insufficient history",
}
