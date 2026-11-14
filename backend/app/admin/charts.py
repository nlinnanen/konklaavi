"""Geometry of the admin panel's inline SVG charts; the templates draw it."""

from collections import defaultdict, deque
from datetime import datetime, timedelta

# Categorical slots for the cluster map; past these, narratives share the neutral "other" colour.
MAP_SERIES = 3


def timeline(rows: list[tuple[datetime, str, int]], width: int = 640, height: int = 96) -> dict | None:
    """Stacked daily bars of fetch and detection observations, a bar per week past 90 days."""
    if not rows:
        return None
    first, last = rows[0][0], rows[-1][0]
    step = timedelta(days=7 if (last - first).days > 90 else 1)
    buckets: dict[int, dict[str, int]] = defaultdict(lambda: {"fetch": 0, "detection": 0})
    for day, source, count in rows:
        buckets[(day - first) // step][source] += count
    n = (last - first) // step + 1
    top = max(b["fetch"] + b["detection"] for b in buckets.values())
    slot = width / n
    bars = []
    for i in range(n):
        b = buckets.get(i, {"fetch": 0, "detection": 0})
        total = b["fetch"] + b["detection"]
        if not total:
            continue
        h_fetch = b["fetch"] / top * height
        h_det = b["detection"] / top * height
        bars.append(
            {
                "x": i * slot + 1,
                "w": min(max(slot - 2, 2), 28),
                "fetch_y": height - h_fetch,
                "fetch_h": h_fetch,
                "det_y": height - h_fetch - h_det,
                "det_h": h_det,
                "day": first + i * step,
                **b,
            }
        )
    return {
        "bars": bars,
        "width": width,
        "height": height,
        "top": top,
        "first": first,
        "last": last,
        "weekly": step.days == 7,
    }


def lineage(edges: dict[tuple[int, int], float | None], labels: dict[int, str]) -> dict | None:
    """A left-to-right tree of DERIVED_FROM edges: each parent's column precedes its children's."""
    if not edges:
        return None
    children: dict[int, list[int]] = defaultdict(list)
    has_parent = set()
    for child, parent in sorted(edges):
        children[parent].append(child)
        has_parent.add(child)
    nodes = {n for pair in edges for n in pair}
    roots = sorted(nodes - has_parent) or [min(nodes)]
    depth: dict[int, int] = {}
    queue = deque((r, 0) for r in roots)
    while queue:
        node, d = queue.popleft()
        if node in depth:
            continue
        depth[node] = d
        queue.extend((c, d + 1) for c in children[node])
    for node in nodes - depth.keys():
        depth[node] = 0
    columns: dict[int, list[int]] = defaultdict(list)
    for node in sorted(nodes, key=lambda n: (depth[n], n)):
        columns[depth[node]].append(node)
    col_w, row_h, node_w, node_h = 230, 46, 196, 34
    pos = {
        node: (16 + d * col_w, 12 + i * row_h)
        for d, column in columns.items()
        for i, node in enumerate(column)
    }
    return {
        "nodes": [
            {"id": n, "x": x, "y": y, "w": node_w, "h": node_h, "label": labels.get(n, f"#{n}")}
            for n, (x, y) in pos.items()
        ],
        "edges": [
            {
                "x1": pos[p][0] + node_w,
                "y1": pos[p][1] + node_h / 2,
                "x2": pos[c][0],
                "y2": pos[c][1] + node_h / 2,
                "jaccard": j,
            }
            for (c, p), j in sorted(edges.items())
        ],
        "width": 32 + (max(columns) + 1) * col_w - (col_w - node_w),
        "height": 24 + max(len(c) for c in columns.values()) * row_h,
    }


def cluster_map(points: list[dict], narratives: list[dict], width: int = 960, height: int = 600) -> dict:
    """Points placed in the plot; the MAP_SERIES largest note narratives get a colour slot each."""
    pad = 24
    scale = min(width, height) - 2 * pad
    x_max = max((p["x"] for p in points), default=0)
    y_max = max((p["y"] for p in points), default=0)
    # Centre the cloud; project() leaves both minima at 0.
    off_x = pad + (width - 2 * pad - x_max * scale) / 2
    off_y = pad + (height - 2 * pad - y_max * scale) / 2
    notes = [n for n in narratives if n["source"] == "note"]
    slots = {n["id"]: i + 1 for i, n in enumerate(notes[:MAP_SERIES])}
    by_id = {n["id"]: n for n in narratives}
    placed = []
    for p in points:
        n = by_id.get(p["narrative_id"], {})
        placed.append(
            {
                **p,
                "cx": round(off_x + p["x"] * scale, 1),
                "cy": round(off_y + (y_max - p["y"]) * scale, 1),
                "slot": slots.get(p["narrative_id"], 0),
                "candidate": n.get("source") == "cluster",
                "title": n.get("title", ""),
            }
        )
    # Claims on top of blocks, coloured narratives on top of the rest.
    placed.sort(key=lambda p: (p["kind"] == "claim", p["slot"] > 0))
    return {
        "points": placed,
        "width": width,
        "height": height,
        "series": [{"slot": slots[n["id"]], **n} for n in notes[:MAP_SERIES]],
    }


def bridging(notes: list[dict], width: int = 640, height: int = 360) -> dict | None:
    """Notes placed by factor (x) and intercept (y), with the scoring thresholds of app/jobs/score.py."""
    scored = [n for n in notes if n["intercept"] is not None and n["factor"] is not None]
    if not scored:
        return None
    fx = max([1.0] + [abs(n["factor"]) * 1.1 for n in scored])
    y_lo = min([-0.6] + [n["intercept"] - 0.1 for n in scored])
    y_hi = max([0.8] + [n["intercept"] + 0.1 for n in scored])
    pad_l, pad_b, pad_t, pad_r = 44, 30, 10, 10

    def sx(f: float) -> float:
        return round(pad_l + (f + fx) / (2 * fx) * (width - pad_l - pad_r), 1)

    def sy(i: float) -> float:
        return round(pad_t + (y_hi - i) / (y_hi - y_lo) * (height - pad_t - pad_b), 1)

    helpful, controversial = 0.40, 0.50
    # Where the not-helpful boundary leaves the plot, at its side or its bottom.
    edge = min(fx, (-0.05 - y_lo) / 0.8)
    return {
        "width": width,
        "height": height,
        "points": [{**n, "cx": sx(n["factor"]), "cy": sy(n["intercept"])} for n in scored],
        "zero_x": sx(0),
        "zero_y": sy(0),
        "helpful": {"x1": sx(-controversial), "x2": sx(controversial), "y": sy(helpful)},
        "controversial": [sx(-controversial), sx(controversial)],
        # Not helpful below i = -0.05 - 0.8 |f|: a V through these three points.
        "not_helpful": [
            (sx(-edge), sy(-0.05 - 0.8 * edge)),
            (sx(0), sy(-0.05)),
            (sx(edge), sy(-0.05 - 0.8 * edge)),
        ],
        "x_ticks": [{"x": sx(t), "label": f"{t:g}"} for t in (-fx, -controversial, 0, controversial, fx)],
        "y_ticks": [{"y": sy(t), "label": f"{t:g}"} for t in (y_lo, 0, helpful, y_hi)],
        "plot": {"x": pad_l, "y": pad_t, "w": width - pad_l - pad_r, "h": height - pad_t - pad_b},
    }
