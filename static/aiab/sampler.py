"""Monitor-mode sampling; preserve should_sample(request_id: str) -> bool."""

import math
import secrets


class Sampler:
    def __init__(self, sample_rate: float = 0.05) -> None:
        if not math.isfinite(sample_rate) or not 0 <= sample_rate <= 1:
            raise ValueError("Invalid sampling probability")
        self.sample_rate = sample_rate
        self._random = secrets.SystemRandom()

    def should_sample(self, request_id: str) -> bool:
        """Independent Bernoulli decision; the identifier is reserved for future designs."""
        return self._random.random() < self.sample_rate
