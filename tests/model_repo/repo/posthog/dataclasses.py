import dataclasses


def frozen(cls=None, **kwargs):
    return dataclasses.dataclass(cls, frozen=True, **kwargs)
