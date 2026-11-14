"""Page-level detection metrics. A page counts as flagged when any of its blocks or sentences is."""

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class Match:
    text: str  # the block holding the flagged hash
    narrative_id: str | None  # None when votes alone flag it
    narrative: str | None


@dataclass(frozen=True)
class Page:
    url: str
    positive: bool
    matches: list[Match] = field(default_factory=list)
    case_id: str | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.matches)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def metrics(pages: list[Page]) -> dict:
    """Confusion counts, precision, recall and false-positive rate; a ratio with no denominator is None."""
    tp = sum(p.positive and p.flagged for p in pages)
    fn = sum(p.positive and not p.flagged for p in pages)
    fp = sum(not p.positive and p.flagged for p in pages)
    tn = sum(not p.positive and not p.flagged for p in pages)
    return {
        "true_positives": tp,
        "false_negatives": fn,
        "false_positives": fp,
        "true_negatives": tn,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "false_positive_rate": _ratio(fp, fp + tn),
    }


def report(pages: list[Page]) -> dict:
    """The metrics, and the matches of every flagged page split into false and true positives."""

    def entry(page: Page) -> dict:
        return {"url": page.url, "case_id": page.case_id, "matches": [asdict(m) for m in page.matches]}

    return {
        "metrics": metrics(pages),
        "false_positives": [entry(p) for p in pages if not p.positive and p.flagged],
        "true_positives": [entry(p) for p in pages if p.positive and p.flagged],
    }
