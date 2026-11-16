from products.gatekeeper.backend.logic.session import touch_session


def connect(registry) -> None:
    registry.register_post_delete_hook(touch_session)


class GateWiring:
    def __init__(self, registry_factory) -> None:
        self.registry = registry_factory()

    def connect(self) -> None:
        self.registry.register_post_delete_hook(touch_session)
