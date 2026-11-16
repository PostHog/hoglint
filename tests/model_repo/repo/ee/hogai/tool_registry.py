import pkgutil

import products


# Finds each product's tools by walking the namespace package all products sit in.
def product_names() -> list[str]:
    return [module.name for module in pkgutil.iter_modules(products.__path__)]
