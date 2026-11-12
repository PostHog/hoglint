from abc import ABC, abstractmethod


class HealthCheckRegistration(ABC):
    @abstractmethod
    def run(self) -> bool: ...
