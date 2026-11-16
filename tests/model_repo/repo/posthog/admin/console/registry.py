class ConsoleRegistry:
    def register_moved_page(self, old: str, new: str) -> None:
        return None

    def register_moved_action(self, old: str, new: str) -> None:
        return None


def _made_registry() -> ConsoleRegistry:
    return ConsoleRegistry()


console = ConsoleRegistry()
actions = _made_registry()
