from app.vectors import quantize


def test_quantize_encodes_the_same_bytes_as_the_extension():
    # client/lib/embed.test.ts expects the same encoding.
    assert quantize([1, -1, 0.5, -0.25, 0, 0.01]) == "f4FA4AAB"
