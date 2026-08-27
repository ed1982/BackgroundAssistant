from bgassist.core.transcript import TranscriptBuffer


def test_add_and_render():
    b = TranscriptBuffer(max_seconds=60)
    b.add(100.0, "hello")
    b.add(120.0, "world")
    text = b.recent_text()
    lines = text.splitlines()
    assert len(lines) == 2
    assert "hello" in lines[0] and "world" in lines[1]


def test_prune_by_age():
    b = TranscriptBuffer(max_seconds=60)
    b.add(100.0, "old")
    b.add(200.0, "new")  # "old" is now >60s before the newest item
    text = b.recent_text()
    assert "new" in text and "old" not in text


def test_prune_by_chars():
    b = TranscriptBuffer(max_seconds=10_000, max_chars=20)
    b.add(1.0, "a" * 15)
    b.add(2.0, "b" * 15)  # total 30 > 20 -> oldest dropped
    text = b.recent_text()
    assert "b" * 15 in text and ("a" * 15) not in text


def test_empty_and_clear():
    b = TranscriptBuffer()
    assert b.recent_text() == ""
    b.add(1.0, "x")
    assert len(b) == 1
    b.clear()
    assert len(b) == 0


def test_recent_window_seconds():
    b = TranscriptBuffer(max_seconds=10_000)
    b.add(10.0, "early")
    b.add(50.0, "late")
    assert "early" not in b.recent_text(seconds=30)
    assert "late" in b.recent_text(seconds=30)


def test_blank_text_ignored():
    b = TranscriptBuffer()
    b.add(1.0, "   ")
    assert len(b) == 0
