"""Unit tests for handle_spoolman_event() — the Spoolman → SpoolEase event handler.

Uses mock objects for SpooleaseClient and State to test the event processing logic
without needing live services.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spoolsync.reconcile_sm_to_se import handle_spoolman_event  # noqa: E402


def _make_event(**overrides) -> dict:
    """Build a minimal Spoolman websocket event dict."""
    base = {
        "type": "updated",
        "resource": "spool",
        "date": "2026-11-16T10:00:00Z",
        "payload": {
            "id": 42,
            "location": "Drawer 1",
            "comment": "Updated comment",
        },
    }
    base["payload"].update(overrides)
    return base


def _make_mock_state(mapping_id: str | None = "se-abc123", is_echo: bool = False) -> MagicMock:
    """Build a mock State with configurable behavior."""
    state = MagicMock()
    state.get_spoolease_id_by_spoolman_id.return_value = mapping_id
    state.is_echo_from_spoolman.return_value = is_echo
    state.record_write_to_spoolease = MagicMock()
    return state


def _make_mock_se(success: bool = True) -> MagicMock:
    """Build a mock SpooleaseClient with configurable write_record."""
    se = MagicMock()
    se.write_record = MagicMock(return_value=success)
    return se


class TestHandleSpoolmanEventResourceFiltering:
    """Test that non-spool events are ignored."""

    def test_non_spool_event_ignored(self):
        """Events for non-spool resources should be silently ignored."""
        event = {"type": "updated", "resource": "filament", "payload": {"id": 1}}
        se = _make_mock_se()
        state = _make_mock_state()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        # write_record should never be called
        se.write_record.assert_not_called()

    def test_missing_resource_ignored(self):
        """Events without a resource field should be ignored."""
        event = {"type": "updated", "payload": {"id": 1}}
        se = _make_mock_se()
        state = _make_mock_state()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_not_called()

    def test_spool_event_without_id_ignored(self):
        """Spool events without an ID should be logged and skipped."""
        event = {"type": "updated", "resource": "spool", "payload": {}}
        se = _make_mock_se()
        state = _make_mock_state()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_not_called()


class TestHandleSpoolmanEventEchoSuppression:
    """Test echo suppression logic."""

    def test_echo_suppressed(self):
        """Echo events should be suppressed — no write-back."""
        event = _make_event()
        state = _make_mock_state(is_echo=True)
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_not_called()
        state.is_echo_from_spoolman.assert_called_once()

    def test_non_echo_not_suppressed(self):
        """Non-echo events should proceed to write-back."""
        event = _make_event()
        state = _make_mock_state(is_echo=False)
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_called_once()


class TestHandleSpoolmanEventMapping:
    """Test SpoolEase ID resolution."""

    def test_no_mapping_skips_write(self):
        """Events for unmapped spools should be skipped."""
        event = _make_event()
        state = _make_mock_state(mapping_id=None)
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_not_called()

    def test_mapping_lookup_called(self):
        """The state should be queried for the SpoolEase ID."""
        event = _make_event()
        state = _make_mock_state(mapping_id="se-abc123")
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        state.get_spoolease_id_by_spoolman_id.assert_called_once_with(42)


class TestHandleSpoolmanEventFieldExtraction:
    """Test that only Spoolman-owned fields are extracted."""

    def test_location_extracted(self):
        """Location field should be extracted from the payload."""
        event = _make_event(location="Garage")
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        # write_record should be called with updates containing location
        args = se.write_record.call_args
        updates = args[0][1]  # (record_id, updates)
        assert updates["location"] == "Garage"

    def test_comment_extracted(self):
        """Comment field should be extracted from the payload."""
        event = _make_event(comment="New comment")
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        args = se.write_record.call_args
        updates = args[0][1]
        assert updates["comment"] == "New comment"

    def test_both_location_and_comment_extracted(self):
        """Both location and comment should be extracted together."""
        event = _make_event(location="Drawer 1", comment="Test comment")
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        args = se.write_record.call_args
        updates = args[0][1]
        assert updates["location"] == "Drawer 1"
        assert updates["comment"] == "Test comment"

    def test_none_values_are_included_in_updates(self):
        """None values for existing keys ARE included in updates (key present in payload)."""
        event = _make_event(location=None, comment=None)
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        # The implementation uses "key in payload" check, so None values ARE included
        args = se.write_record.call_args
        updates = args[0][1]
        assert updates["location"] is None
        assert updates["comment"] is None


class TestHandleSpoolmanEventWriteBack:
    """Test the write-back to SpoolEase."""

    def test_write_record_called_with_correct_id(self):
        """write_record should be called with the resolved SpoolEase ID."""
        event = _make_event()
        state = _make_mock_state(mapping_id="se-xyz789")
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        se.write_record.assert_called_once_with("se-xyz789", {"location": "Drawer 1", "comment": "Updated comment"})

    def test_write_record_success_records_echo(self):
        """Successful write should record for echo suppression."""
        event = _make_event()
        state = _make_mock_state()
        se = _make_mock_se(success=True)

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        state.record_write_to_spoolease.assert_called_once()

    def test_write_record_failure_does_not_record_echo(self):
        """Failed write should NOT record for echo suppression."""
        event = _make_event()
        state = _make_mock_state()
        se = _make_mock_se(success=False)

        asyncio.run(
            handle_spoolman_event(event, se, state)
        )

        state.record_write_to_spoolease.assert_not_called()


class TestHandleSpoolmanEventDryRun:
    """Test dry-run mode."""

    def test_dry_run_skips_write(self):
        """In dry-run mode, write_record should not be called."""
        event = _make_event()
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state, dry_run=True)
        )

        se.write_record.assert_not_called()

    def test_dry_run_still_resolves_mapping(self):
        """Even in dry-run, the SpoolEase ID should be resolved."""
        event = _make_event()
        state = _make_mock_state()
        se = _make_mock_se()

        asyncio.run(
            handle_spoolman_event(event, se, state, dry_run=True)
        )

        state.get_spoolease_id_by_spoolman_id.assert_called_once()
