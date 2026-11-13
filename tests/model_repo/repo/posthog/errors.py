from posthog.models.feature_flag import FeatureFlag


class CoreLookupError(Exception):
    def flag(self) -> FeatureFlag:
        raise NotImplementedError
