from posthog.admin.console.registry import console


def entry_panel(entry_id: int) -> str:
    return f"entry {entry_id} on {console}"
