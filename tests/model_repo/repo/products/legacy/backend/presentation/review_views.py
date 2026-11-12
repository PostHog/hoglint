from products.legacy.backend import drafts
from products.legacy.backend.reviews import tests as review_fixtures


def review_page() -> tuple[object, object]:
    return review_fixtures, drafts
