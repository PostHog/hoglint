from products.inventory.backend.facade.hooks import register_restock_hook


def install(callback):
    register_restock_hook(callback)
