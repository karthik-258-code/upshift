"""Per-run spend cap with threshold notifications."""

from __future__ import annotations

from dataclasses import dataclass, field

THRESHOLDS_PCT = (50, 80, 90)


@dataclass
class Budget:
    cap_usd: float
    spent_usd: float = 0.0
    _crossed: set[int] = field(default_factory=set, repr=False)

    def spend(self, amount_usd: float) -> list[int]:
        """Record spend; return thresholds (percent) newly crossed by this charge."""
        self.spent_usd += max(amount_usd, 0.0)
        pct = self.spent_fraction * 100
        newly: list[int] = []
        for threshold in THRESHOLDS_PCT:
            if pct >= threshold and threshold not in self._crossed:
                self._crossed.add(threshold)
                newly.append(threshold)
        return newly

    @property
    def spent_fraction(self) -> float:
        if self.cap_usd <= 0:
            return 1.0
        return min(self.spent_usd / self.cap_usd, 1.0)

    @property
    def remaining_fraction(self) -> float:
        return 1.0 - self.spent_fraction

    @property
    def remaining_usd(self) -> float:
        return max(self.cap_usd - self.spent_usd, 0.0)

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd
