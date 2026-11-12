from products.notifications.backend.facade.api import notify


def connect(registry) -> None:
    registry.register_post_delete_hook(notify)
