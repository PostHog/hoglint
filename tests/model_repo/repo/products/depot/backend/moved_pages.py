from posthog.admin.console.registry import console


def register_moved_pages():
    for old in ("bins", "slots"):
        url = f"/depot/{old}/"
        console.register_moved_page(old, url)
