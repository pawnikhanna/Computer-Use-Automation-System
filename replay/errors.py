from __future__ import annotations
from artifacts.schema import BusinessOutcome


class HardFailure(Exception):
    def __init__(self, step_id: str, expected: str, observed: str):
        self.step_id = step_id
        self.expected = expected
        self.observed = observed
        super().__init__(f"Hard failure at step {step_id}: expected {expected!r}, observed {observed!r}")


class BusinessOutcomeMatch(Exception):
    def __init__(self, outcome: BusinessOutcome):
        self.outcome = outcome
        super().__init__(f"Business outcome matched: {outcome.name}")


class RequiresEscalation(Exception):
    def __init__(self, step_id: str, reason: str, operator_notes: str = ""):
        self.step_id = step_id
        self.reason = reason
        self.operator_notes = operator_notes
        super().__init__(f"Escalation required at step {step_id}: {reason}")