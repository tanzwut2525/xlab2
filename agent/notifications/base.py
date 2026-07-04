from abc import ABC, abstractmethod
from typing import Literal

Severity = Literal["info", "warning", "critical"]


class Notifier(ABC):
    @abstractmethod
    def send(self, title: str, message: str, severity: Severity = "info") -> None:
        """Deliver a human-readable notification describing what happened."""
