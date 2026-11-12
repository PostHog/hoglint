def mutable_receiver(signal, **kwargs):
    def wrap(func):
        return func

    return wrap
