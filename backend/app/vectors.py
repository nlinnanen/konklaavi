"""The int8 vector encoding shipped in GET /index; `client/lib/embed.ts` encodes the same bytes."""

import base64
from collections.abc import Sequence

import numpy as np

INT8_SCALE = 127


def quantize(vector: Sequence[float]) -> str:
    """A unit vector as base64 of one int8 per value, round(v * 127) with halves rounded up."""
    scaled = np.floor(np.asarray(vector, dtype=np.float64) * INT8_SCALE + 0.5)
    return base64.b64encode(np.clip(scaled, -INT8_SCALE, INT8_SCALE).astype(np.int8).tobytes()).decode()


def dequantize(encoded: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(encoded), dtype=np.int8).astype(np.float32) / INT8_SCALE


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity; 0 when either vector is zero."""
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    norms = np.linalg.norm(a) * np.linalg.norm(b)
    return 0.0 if norms == 0 else float(a @ b / norms)
