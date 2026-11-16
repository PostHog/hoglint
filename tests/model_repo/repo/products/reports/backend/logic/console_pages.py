from posthog.admin.console.registry import ConsoleRegistry, console


class PageIndex:
    def register_moved_page(self, old: str, new: str) -> None:
        return None


class PageMover:
    def __init__(self) -> None:
        self.registry: ConsoleRegistry = console
        self.index = PageIndex()

    def move(self) -> None:
        self.registry.register_moved_page("old", "new")
        self.index.register_moved_page("old", "new")
        self.registry.register_moved_action("old", "new")
