"""Logging must go to stderr, and DB-query noise stays out of the admin tail.

Regression coverage for: error tracebacks appearing on stdout, and the per-query
PostgreSQL timing logs being suppressed from CloudWatch entirely.
"""

import io
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from overmind.main import CapturedStream, CapturedLoggingHandler, _is_db_query_log_record


def test_captured_stream_capture_flag_keeps_line_out_of_tail_but_on_stream():
    sink = io.StringIO()
    stream = CapturedStream("stderr", sink, max_lines=100)
    stream.write("normal error line\n")
    stream.write("PostgreSQL query noise\n", capture=False)
    # Both still reach the real stream (and therefore CloudWatch):
    assert "normal error line" in sink.getvalue()
    assert "PostgreSQL query noise" in sink.getvalue()
    # Only the captured line shows in the bounded admin tail:
    snapshot = stream.snapshot()
    assert "normal error line" in snapshot
    assert "PostgreSQL query noise" not in snapshot


def test_db_query_log_record_detection():
    rec = logging.LogRecord("overmind.postgres_store", logging.WARNING, __file__, 1,
                            "PostgreSQL query operation=execute error=UndefinedColumn", None, None)
    assert _is_db_query_log_record(rec) is True
    other = logging.LogRecord("overmind.main", logging.ERROR, __file__, 1,
                              "Unhandled request error", None, None)
    assert _is_db_query_log_record(other) is False


def test_logging_handler_writes_errors_to_stderr_not_stdout(monkeypatch):
    import overmind.main as m

    class _Cap:
        def __init__(self):
            self.stdout = CapturedStream("stdout", io.StringIO(), max_lines=100)
            self.stderr = CapturedStream("stderr", io.StringIO(), max_lines=100)

    cap = _Cap()
    monkeypatch.setattr(m, "_STREAM_LOG_CAPTURE", cap)
    handler = CapturedLoggingHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))

    err = logging.LogRecord("overmind.main", logging.ERROR, __file__, 1, "boom", None, None)
    handler.emit(err)
    # Error landed on stderr, nothing on stdout.
    assert "boom" in cap.stderr.snapshot()
    assert cap.stdout.snapshot() == ""

    # DB-query noise reaches the real stderr stream but not the admin tail.
    q = logging.LogRecord("overmind.postgres_store", logging.WARNING, __file__, 1,
                          "PostgreSQL query operation=execute", None, None)
    handler.emit(q)
    assert "PostgreSQL query" in cap.stderr.wrapped.getvalue()
    assert "PostgreSQL query" not in cap.stderr.snapshot()
