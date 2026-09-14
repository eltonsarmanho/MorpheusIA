from app.api.rate_limit import RateLimiter


def test_check_allows_calls_under_both_limits():
    limiter = RateLimiter(per_minute=15, per_session=60, clock=lambda: 1000.0)

    for _ in range(10):
        assert limiter.check("s1") is True


def test_check_blocks_when_per_minute_limit_exceeded():
    limiter = RateLimiter(per_minute=3, per_session=1000, clock=lambda: 1000.0)

    assert limiter.check("s1") is True
    assert limiter.check("s1") is True
    assert limiter.check("s1") is True
    assert limiter.check("s1") is False


def test_check_blocks_when_per_session_limit_exceeded():
    limiter = RateLimiter(per_minute=1000, per_session=2, clock=lambda: 1000.0)

    assert limiter.check("s1") is True
    assert limiter.check("s1") is True
    assert limiter.check("s1") is False


def test_check_per_minute_window_resets_after_time_advances():
    current = {"t": 1000.0}
    limiter = RateLimiter(per_minute=2, per_session=1000, clock=lambda: current["t"])

    assert limiter.check("s1") is True
    assert limiter.check("s1") is True
    assert limiter.check("s1") is False  # 3rd call within the same 60s window blocked

    current["t"] += 61  # advance past the sliding 60s window

    assert limiter.check("s1") is True  # window reset, allowed again


def test_check_tracks_sessions_independently():
    limiter = RateLimiter(per_minute=1, per_session=1, clock=lambda: 1000.0)

    assert limiter.check("s1") is True
    assert limiter.check("s1") is False
    assert limiter.check("s2") is True
