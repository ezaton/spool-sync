"""Unit tests for the shadow store's echo suppression logic.

These test the State class methods that track recent writes and detect echoes,
without needing live SpoolEase/Spoolman instances.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spoolsync.state import State  # noqa: E402


class TestEchoSuppressionSpoolman:
    """Tests for is_echo_from_spoolman() / record_write_to_spoolman()."""

    def setup_method(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.state = State(self.db_path)

    def teardown_method(self):
        self.state.close()
        import os
        os.unlink(self.db_path)

    def test_echo_detected_matching_content(self):
        """A write followed by matching content should be suppressed."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1", "comment": "Test"})
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1", "comment": "Test"}) is True

    def test_echo_not_detected_different_content(self):
        """Different content should NOT be suppressed."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1", "comment": "Test"})
        assert self.state.is_echo_from_spoolman(42, {"location": "Garage", "comment": "Test"}) is False

    def test_echo_not_detected_different_comment(self):
        """Different comment should NOT be suppressed."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1", "comment": "Test"})
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1", "comment": "Changed"}) is False

    def test_echo_not_detected_unknown_spool(self):
        """Unknown spool ID should NOT be suppressed."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        assert self.state.is_echo_from_spoolman(999, {"location": "Drawer 1"}) is False

    def test_echo_not_detected_after_window(self):
        """Echo detection should expire after the time window."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        # Manually backdate the write to be older than the 30s default window
        self.state._db.execute(
            "UPDATE recent_writes SET timestamp = ? WHERE spoolman_spool_id = ?",
            (time.time() - 35, 42),
        )
        self.state._db.commit()
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1"}) is False

    def test_echo_not_detected_custom_window_expired(self):
        """Echo detection should respect custom window parameter."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        # Backdate to be older than a 5s custom window
        self.state._db.execute(
            "UPDATE recent_writes SET timestamp = ? WHERE spoolman_spool_id = ?",
            (time.time() - 6, 42),
        )
        self.state._db.commit()
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1"}, window_seconds=5.0) is False

    def test_echo_detected_within_custom_window(self):
        """Echo detection should work within a custom window."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1"}, window_seconds=5.0) is True

    def test_echo_suppression_key_only_in_current_is_mismatch(self):
        """If a tracked key exists in current but not in recorded, it is a mismatch."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        # "comment" is in current but not in recorded → None != "Test" → mismatch
        assert self.state.is_echo_from_spoolman(42, {"comment": "Test"}) is False

    def test_echo_suppression_key_absent_from_both_is_ignored(self):
        """If a tracked key is absent from both recorded and current, it is ignored."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        # Both dicts only have "location" — "comment" absent from both → ignored
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1"}) is True


class TestEchoSuppressionSpoolease:
    """Tests for is_echo_from_spoolease() / record_write_to_spoolease()."""

    def setup_method(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.state = State(self.db_path)

    def teardown_method(self):
        self.state.close()
        import os
        os.unlink(self.db_path)

    def test_echo_detected_matching_content(self):
        """A write-back followed by matching content should be suppressed."""
        self.state.record_write_to_spoolease(42, {"location": "Drawer 1", "comment": "Test"})
        assert self.state.is_echo_from_spoolease(42, {"location": "Drawer 1", "comment": "Test"}) is True

    def test_echo_not_detected_different_content(self):
        """Different content should NOT be suppressed."""
        self.state.record_write_to_spoolease(42, {"location": "Drawer 1", "comment": "Test"})
        assert self.state.is_echo_from_spoolease(42, {"location": "Garage", "comment": "Test"}) is False

    def test_echo_not_detected_unknown_spool(self):
        """Unknown spool ID should NOT be suppressed."""
        self.state.record_write_to_spoolease(42, {"location": "Drawer 1"})
        assert self.state.is_echo_from_spoolease(999, {"location": "Drawer 1"}) is False

    def test_write_to_spoolman_does_not_trigger_spoolease_echo(self):
        """A write to Spoolman should not be detected as a SpoolEase echo."""
        self.state.record_write_to_spoolman(42, {"location": "Drawer 1"})
        assert self.state.is_echo_from_spoolease(42, {"location": "Drawer 1"}) is False

    def test_write_to_spoolease_does_not_trigger_spoolman_echo(self):
        """A write to SpoolEase should not be detected as a Spoolman echo."""
        self.state.record_write_to_spoolease(42, {"location": "Drawer 1"})
        assert self.state.is_echo_from_spoolman(42, {"location": "Drawer 1"}) is False


class TestContentMatches:
    """Tests for the internal _content_matches logic."""

    def setup_method(self):
        import tempfile
        self._tmp = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
        self.db_path = self._tmp.name
        self._tmp.close()
        self.state = State(self.db_path)

    def teardown_method(self):
        self.state.close()
        import os
        os.unlink(self.db_path)

    def test_matching_location(self):
        assert self.state._content_matches(
            {"location": "Drawer 1"}, {"location": "Drawer 1"}
        ) is True

    def test_mismatching_location(self):
        assert self.state._content_matches(
            {"location": "Drawer 1"}, {"location": "Garage"}
        ) is False

    def test_matching_comment(self):
        assert self.state._content_matches(
            {"comment": "Test"}, {"comment": "Test"}
        ) is True

    def test_mismatching_comment(self):
        assert self.state._content_matches(
            {"comment": "Test"}, {"comment": "Changed"}
        ) is False

    def test_matching_price(self):
        assert self.state._content_matches(
            {"price": 10}, {"price": 10}
        ) is True

    def test_mismatching_price(self):
        assert self.state._content_matches(
            {"price": 10}, {"price": 20}
        ) is False

    def test_matching_archived(self):
        assert self.state._content_matches(
            {"archived": True}, {"archived": True}
        ) is True

    def test_mismatching_archived(self):
        assert self.state._content_matches(
            {"archived": True}, {"archived": False}
        ) is False

    def test_matching_remaining_weight(self):
        assert self.state._content_matches(
            {"remaining_weight": 500.0}, {"remaining_weight": 500.0}
        ) is True

    def test_mismatching_remaining_weight(self):
        assert self.state._content_matches(
            {"remaining_weight": 500.0}, {"remaining_weight": 400.0}
        ) is False

    def test_empty_dicts_match(self):
        assert self.state._content_matches({}, {}) is True

    def test_extra_keys_ignored_when_matching(self):
        """Extra keys in one dict that aren't in the other are ignored."""
        assert self.state._content_matches(
            {"location": "Drawer 1", "extra": "ignored"},
            {"location": "Drawer 1"}
        ) is True

    def test_none_values_differ_from_strings(self):
        """None and empty string are different."""
        assert self.state._content_matches(
            {"location": ""}, {"location": None}
        ) is False
