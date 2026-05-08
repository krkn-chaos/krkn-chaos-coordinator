"""Eval report model for comparing LLM filter results across models."""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EvalReport:
    """Results of running the same bugs through two LLM models and comparing."""

    eval_name: str
    baseline_model: str
    candidate_model: str
    sample_size: int
    agreement_rate: float          # % where both models agree
    false_negative_rate: float     # % candidate says "no" when baseline says "yes"
    false_positive_rate: float     # % candidate says "yes" when baseline says "no"
    disagreements: list[dict] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Pass criteria: agreement >= 90%, false negatives <= 5%."""
        return self.agreement_rate >= 0.90 and self.false_negative_rate <= 0.05

    def summary(self) -> str:
        """Return a human-readable summary of the eval results."""
        status = "PASS" if self.passed else "FAIL"
        return (
            f"[{status}] {self.eval_name}\n"
            f"  Models: {self.baseline_model} vs {self.candidate_model}\n"
            f"  Sample: {self.sample_size} bugs\n"
            f"  Agreement: {self.agreement_rate:.1%}\n"
            f"  False negatives: {self.false_negative_rate:.1%}\n"
            f"  False positives: {self.false_positive_rate:.1%}\n"
            f"  Disagreements: {len(self.disagreements)}"
        )
