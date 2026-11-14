"""Page-level recognition metrics. A page counts as flagged when any of its blocks or sentences matches a
narrative."""

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field, replace


@dataclass(frozen=True)
class Match:
    text: str  # the block holding the matched hash
    narrative_id: str
    narrative: str
    cosine: float | None = None  # a narrative-level match's cosine to the narrative's closest vector


@dataclass(frozen=True)
class Page:
    url: str
    positive: bool
    matches: list[Match] = field(default_factory=list)  # exact and variant matches
    # Per block with no exact or variant match, its closest narrative: a match at thresholds up to its cosine.
    near: list[Match] = field(default_factory=list)
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
        "matches": [
            {k: v for k, v in asdict(m).items() if v is not None or k != "cosine"} for m in page.matches
        ]
        or None,
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


def at_threshold(page: Page, threshold: float) -> Page:
    """The page with the narrative-level matches at the threshold added to its matches."""
    return replace(page, matches=page.matches + [m for m in page.near if m.cosine >= threshold], near=[])


def sweep(positives: dict[str, list[Page]], negatives: list[Page], thresholds: Iterable[float]) -> list[dict]:
    """Per threshold, the false-positive rate on the negatives and each positive set's precision and recall."""
    rows = []
    for threshold in thresholds:
        flagged_negatives = [at_threshold(p, threshold) for p in negatives]
        row = {
            "threshold": threshold,
            "false_positive_rate": metrics(flagged_negatives)["false_positive_rate"],
        }
        for name, pages in positives.items():
            m = metrics([at_threshold(p, threshold) for p in pages] + flagged_negatives)
            row[f"{name}_precision"] = m["precision"]
            row[f"{name}_recall"] = m["recall"]
        rows.append(row)
    return rows


def best_threshold(rows: list[dict], recall: str, max_false_positive_rate: float) -> float | None:
    """The threshold with the best recall among those within the false-positive rate; the higher on a tie."""
    within = [
        r
        for r in rows
        if r["false_positive_rate"] is not None
        and r["false_positive_rate"] <= max_false_positive_rate
        and r[recall] is not None
    ]
    return max(within, key=lambda r: (r[recall], r["threshold"]))["threshold"] if within else None
