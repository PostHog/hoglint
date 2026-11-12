from products.gatekeeper.backend.logic.session import touch_session


def connect(registry) -> None:
    registry.register_post_delete_hook(touch_session)
