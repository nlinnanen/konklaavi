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
    narrative_id: str | None = None  # the narrative seeded from the page's case, when there is one
    mirror_of: str | None = None  # the held-out URL whose text this page repeats
    reason: str | None = None  # why a positive page was not flagged

    @property
    def flagged(self) -> bool:
        return bool(self.matches)

    @property
    def flagged_by_own_narrative(self) -> bool:
        return self.narrative_id is not None and any(
            m.narrative_id == self.narrative_id for m in self.matches
        )


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


def entry(page: Page) -> dict:
    """A page as listed in the report, leaving out fields it has no value for."""
    fields = {
        "url": page.url,
        "case_id": page.case_id,
        "mirror_of": page.mirror_of,
        "reason": page.reason,
        "matches": [asdict(m) for m in page.matches] or None,
    }
    if page.narrative_id is not None and page.flagged:
        fields["flagged_by_own_narrative"] = page.flagged_by_own_narrative
    return {key: value for key, value in fields.items() if value is not None}


def report(pages: list[Page]) -> dict:
    """The metrics, and every false positive, false negative and true positive."""
    return {
        "metrics": metrics(pages),
        "false_positives": [entry(p) for p in pages if not p.positive and p.flagged],
        "false_negatives": [entry(p) for p in pages if p.positive and not p.flagged],
        "true_positives": [entry(p) for p in pages if p.positive and p.flagged],
    }
