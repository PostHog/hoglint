REGISTERED = {}


def register_model(kind, model_class):
    REGISTERED[kind] = model_class
