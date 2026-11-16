def get_or_create_team_extension(team, model_class):
    return model_class


class TeamExtensionDescriptor:
    """Loads a product's team extension model by its dotted path on first read."""

    def __init__(self, path: str) -> None:
        self.path = path
