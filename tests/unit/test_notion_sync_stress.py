"""
Stress tests for notion.py folder<->Notion sync logic.

Tests the 4 key scenarios:
1. File deleted from folder -> deleted from Notion
2. Files moved between folders -> moved between funds/firms on Notion
3. Folder removed -> fund/firm removed from Notion
4. Folder renamed -> fund/firm renamed on Notion

Uses mocked Notion API calls to verify correct behavior without a real Notion instance.
"""

import json
import shutil
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from fofproject.notion import (
    NotionFolderHandler,
    parse_fund_folder_name,
    _normalize_filename,
    _remove_file_from_page,
    _rename_page_title,
    _rename_file_on_page,
    _archive_page,
    FIRM_TITLE_PROP,
    FUND_TITLE_PROP,
    SKIP_SUBFOLDERS,
)


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def watch_dir(tmp_path):
    """Create a watch directory with a basic firm/fund structure."""
    watch = tmp_path / "watch"
    watch.mkdir()
    return watch


@pytest.fixture
def firm_fund_structure(watch_dir):
    """Create a firm with two funds, each with files."""
    firm = watch_dir / "TestFirm"
    firm.mkdir()
    (firm / "firm_report.pdf").write_bytes(b"firm content")

    fund_a = firm / "FundAlpha - 1001"
    fund_a.mkdir()
    (fund_a / "factsheet.pdf").write_bytes(b"alpha factsheet")
    (fund_a / "monthly_letter.pdf").write_bytes(b"alpha letter")

    fund_b = firm / "FundBeta - 1002"
    fund_b.mkdir()
    (fund_b / "overview.pdf").write_bytes(b"beta overview")

    return watch_dir


@pytest.fixture
def multi_firm_structure(watch_dir):
    """Create two firms with funds for cross-firm move tests."""
    firm_a = watch_dir / "FirmA"
    firm_a.mkdir()
    fund_a1 = firm_a / "FundA1 - 2001"
    fund_a1.mkdir()
    (fund_a1 / "report_a1.pdf").write_bytes(b"a1 report")
    (fund_a1 / "letter_a1.pdf").write_bytes(b"a1 letter")

    firm_b = watch_dir / "FirmB"
    firm_b.mkdir()
    fund_b1 = firm_b / "FundB1 - 3001"
    fund_b1.mkdir()
    (fund_b1 / "report_b1.pdf").write_bytes(b"b1 report")

    return watch_dir


@pytest.fixture
def handler(watch_dir):
    """Create a NotionFolderHandler with mock DB IDs."""
    return NotionFolderHandler(watch_dir, "firm-db-id", "fund-db-id")


# Mock helpers
def make_mock_page(page_id, title="Test", title_prop="Name"):
    return {
        "id": page_id,
        "properties": {
            title_prop: {
                "type": "title",
                "title": [{"plain_text": title}],
            }
        },
    }


def make_mock_query_result(page_id, title, title_prop="Fund Name"):
    """Simulate _query_database_by_title returning a page."""
    return make_mock_page(page_id, title, title_prop)


# =============================================================================
# SCENARIO 1: File deleted from folder -> deleted from Notion
# =============================================================================

class TestFileDeletedFromFolder:
    """Scenario 1: When a file is deleted from a folder, it should be deleted from Notion."""

    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_file_deletion_triggers_notion_removal(self, mock_query, mock_remove, firm_fund_structure, handler):
        """Deleting a file from a fund folder should remove it from the Notion fund page."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")

        from watchdog.events import FileDeletedEvent
        fund_file = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        event = FileDeletedEvent(str(fund_file))

        handler.on_deleted(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("fund-db-id", "FundAlpha", FUND_TITLE_PROP)
        mock_remove.assert_called_once_with("fund-page-alpha", "factsheet.pdf")

    @patch("fofproject.notion._remove_file_from_firm_property")
    @patch("fofproject.notion._query_database_by_title")
    def test_firm_file_deletion_triggers_notion_removal(self, mock_query, mock_remove, firm_fund_structure, handler):
        """Deleting a file from a firm folder should remove it from the Notion firm page."""
        mock_query.return_value = make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)

        from watchdog.events import FileDeletedEvent
        firm_file = firm_fund_structure / "TestFirm" / "firm_report.pdf"
        event = FileDeletedEvent(str(firm_file))

        handler.on_deleted(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("firm-db-id", "TestFirm", FIRM_TITLE_PROP)
        mock_remove.assert_called_once_with("firm-page-1", "firm_report.pdf")

    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_not_found_skips_silently(self, mock_query, mock_remove, firm_fund_structure, handler):
        """If fund doesn't exist in Notion, deletion should be a no-op."""
        mock_query.return_value = None

        from watchdog.events import FileDeletedEvent
        fund_file = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        event = FileDeletedEvent(str(fund_file))

        handler.on_deleted(event)
        time.sleep(0.3)

        mock_remove.assert_not_called()

    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_skip_subfolder_files_not_deleted(self, mock_query, mock_remove, firm_fund_structure, handler):
        """Files in SKIP_SUBFOLDERS (graph/, json/, etc.) should NOT trigger deletion."""
        for subfolder in ["graph", "json", "meetings", "researches"]:
            sub = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / subfolder
            sub.mkdir(exist_ok=True)
            test_file = sub / "test.png"
            test_file.write_bytes(b"x")

            from watchdog.events import FileDeletedEvent
            event = FileDeletedEvent(str(test_file))
            handler.on_deleted(event)

        time.sleep(0.3)
        mock_remove.assert_not_called()


# =============================================================================
# SCENARIO 2: Files moved between folders -> moved on Notion
# =============================================================================

class TestFileMovedBetweenFolders:
    """Scenario 2: When a file is moved between folders, it should be moved on Notion."""

    @patch("fofproject.notion._rename_file_on_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_renamed_within_fund(self, mock_query, mock_rename, firm_fund_structure, handler):
        """Renaming a file within a fund folder should rename it on Notion."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")
        mock_rename.return_value = True

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        new = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet_v2.pdf"
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.5)

        mock_rename.assert_called_once_with("fund-page-alpha", "factsheet.pdf", "factsheet_v2.pdf")

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_moved_between_funds(self, mock_query, mock_remove, mock_sections, mock_upload, firm_fund_structure, handler):
        """Moving a file from FundAlpha to FundBeta should remove from Alpha and upload to Beta."""
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            if name == "FundBeta":
                return make_mock_query_result("fund-page-beta", "FundBeta")
            return None

        mock_query.side_effect = query_side_effect
        mock_sections.return_value = {"Others": "callout-1"}

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        new = firm_fund_structure / "TestFirm" / "FundBeta - 1002" / "factsheet.pdf"
        # Ensure the destination file exists for upload
        new.write_bytes(b"alpha factsheet")
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(1.0)

        # Should remove from Alpha
        mock_remove.assert_called_once_with("fund-page-alpha", "factsheet.pdf")
        # Should upload to Beta via sections
        mock_sections.assert_called_once_with("fund-page-beta")
        mock_upload.assert_called_once()

    @patch("fofproject.notion._upload_firm_files_to_property")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_moved_from_fund_to_firm(self, mock_query, mock_remove, mock_upload, firm_fund_structure, handler):
        """Moving a file from a fund folder to its parent firm folder."""
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            if name == "TestFirm":
                return make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)
            return None

        mock_query.side_effect = query_side_effect

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        new = firm_fund_structure / "TestFirm" / "factsheet.pdf"
        new.write_bytes(b"alpha factsheet")
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(1.0)

        # Should remove from fund page
        mock_remove.assert_called_once_with("fund-page-alpha", "factsheet.pdf")
        # Should upload to firm property
        mock_upload.assert_called_once_with("firm-page-1", [new])

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_firm_property")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_moved_from_firm_to_fund(self, mock_query, mock_firm_remove, mock_sections, mock_upload, firm_fund_structure, handler):
        """Moving a file from firm level to a fund subfolder."""
        def query_side_effect(db_id, name, prop):
            if name == "TestFirm":
                return make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            return None

        mock_query.side_effect = query_side_effect
        mock_sections.return_value = {"Others": "callout-1"}

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "firm_report.pdf"
        new = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "firm_report.pdf"
        new.write_bytes(b"firm content")
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(1.0)

        # Should remove from firm property
        mock_firm_remove.assert_called_once_with("firm-page-1", "firm_report.pdf")
        # Should upload to fund section
        mock_sections.assert_called_once_with("fund-page-alpha")
        mock_upload.assert_called_once()


# =============================================================================
# SCENARIO 3: Folder removed -> fund/firm removed from Notion
# =============================================================================

class TestFolderRemoved:
    """Scenario 3: When a folder is removed, the corresponding Notion page should be archived."""

    @patch("fofproject.notion._archive_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_folder_deleted_archives_page(self, mock_query, mock_archive, firm_fund_structure, handler):
        """Deleting a fund folder should archive the corresponding Notion page."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")

        from watchdog.events import DirDeletedEvent
        fund_dir = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        event = DirDeletedEvent(str(fund_dir))

        handler.on_deleted(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("fund-db-id", "FundAlpha", FUND_TITLE_PROP)
        mock_archive.assert_called_once_with("fund-page-alpha")

    @patch("fofproject.notion._archive_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_firm_folder_deleted_archives_page(self, mock_query, mock_archive, firm_fund_structure, handler):
        """Deleting a firm folder should archive the corresponding Notion firm page."""
        mock_query.return_value = make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)

        from watchdog.events import DirDeletedEvent
        firm_dir = firm_fund_structure / "TestFirm"
        event = DirDeletedEvent(str(firm_dir))

        handler.on_deleted(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("firm-db-id", "TestFirm", FIRM_TITLE_PROP)
        mock_archive.assert_called_once_with("firm-page-1")

    @patch("fofproject.notion._archive_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_not_found_skips_archive(self, mock_query, mock_archive, firm_fund_structure, handler):
        """If fund not in Notion, folder deletion should be a no-op."""
        mock_query.return_value = None

        from watchdog.events import DirDeletedEvent
        fund_dir = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        event = DirDeletedEvent(str(fund_dir))

        handler.on_deleted(event)
        time.sleep(0.3)

        mock_archive.assert_not_called()

    @patch("fofproject.notion._archive_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_skip_subfolder_deletion_no_archive(self, mock_query, mock_archive, firm_fund_structure, handler):
        """Deleting a SKIP_SUBFOLDER (graph/, json/) should NOT trigger page archive."""
        for subfolder in ["graph", "json", "meetings", "researches"]:
            sub = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / subfolder
            sub.mkdir(exist_ok=True)

            from watchdog.events import DirDeletedEvent
            event = DirDeletedEvent(str(sub))
            handler.on_deleted(event)

        time.sleep(0.3)
        # These are nested too deep to match fund/firm patterns, so no archive
        mock_archive.assert_not_called()


# =============================================================================
# SCENARIO 4: Folder renamed -> fund/firm renamed on Notion
# =============================================================================

class TestFolderRenamed:
    """Scenario 4: When a folder is renamed, the Notion page title should be updated."""

    @patch("fofproject.notion._update_page_properties")
    @patch("fofproject.notion._rename_page_title")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_folder_renamed(self, mock_query, mock_rename, mock_update, firm_fund_structure, handler):
        """Renaming a fund folder should rename the Notion fund page and update identifier."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")

        from watchdog.events import DirMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        new = firm_fund_structure / "TestFirm" / "FundAlphaRenamed - 1001"
        event = DirMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("fund-db-id", "FundAlpha", FUND_TITLE_PROP)
        mock_rename.assert_called_once_with("fund-page-alpha", "FundAlphaRenamed", FUND_TITLE_PROP)

    @patch("fofproject.notion._rename_page_title")
    @patch("fofproject.notion._query_database_by_title")
    def test_firm_folder_renamed(self, mock_query, mock_rename, firm_fund_structure, handler):
        """Renaming a firm folder should rename the Notion firm page."""
        mock_query.return_value = make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)

        from watchdog.events import DirMovedEvent
        old = firm_fund_structure / "TestFirm"
        new = firm_fund_structure / "TestFirmRenamed"
        event = DirMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.5)

        mock_query.assert_called_once_with("firm-db-id", "TestFirm", FIRM_TITLE_PROP)
        mock_rename.assert_called_once_with("firm-page-1", "TestFirmRenamed", FIRM_TITLE_PROP)

    @patch("fofproject.notion._update_page_properties")
    @patch("fofproject.notion._rename_page_title")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_identifier_updated_on_rename(self, mock_query, mock_rename, mock_update, firm_fund_structure, handler):
        """Renaming a fund folder with a new identifier should update both name and identifier."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")

        from watchdog.events import DirMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        new = firm_fund_structure / "TestFirm" / "FundAlpha - 2002"
        event = DirMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.5)

        # Identifier should be updated to "2002"
        mock_update.assert_called_once()
        update_call = mock_update.call_args
        assert update_call[0][0] == "fund-page-alpha"
        props = update_call[0][1]
        assert "Identifier" in props
        id_content = props["Identifier"]["rich_text"][0]["text"]["content"]
        assert id_content == "2002"

    @patch("fofproject.notion._update_page_properties")
    @patch("fofproject.notion._rename_page_title")
    @patch("fofproject.notion._query_database_by_title")
    def test_fund_not_found_in_notion_skips(self, mock_query, mock_rename, mock_update, firm_fund_structure, handler):
        """If old fund name not found in Notion, rename should be a no-op."""
        mock_query.return_value = None

        from watchdog.events import DirMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        new = firm_fund_structure / "TestFirm" / "FundAlphaRenamed - 1001"
        event = DirMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.3)

        mock_rename.assert_not_called()
        mock_update.assert_not_called()


# =============================================================================
# CONCURRENT / RAPID MOVE STRESS TESTS
# =============================================================================

class TestConcurrentMoves:
    """Stress test: multiple consecutive/concurrent file moves happening at the same time."""

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_two_files_moved_simultaneously_between_funds(
        self, mock_query, mock_remove, mock_sections, mock_upload, firm_fund_structure, handler
    ):
        """Two files moved from FundAlpha to FundBeta at the same time."""
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            if name == "FundBeta":
                return make_mock_query_result("fund-page-beta", "FundBeta")
            return None

        mock_query.side_effect = query_side_effect
        mock_sections.return_value = {"Others": "callout-1"}

        from watchdog.events import FileMovedEvent

        fund_alpha = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        fund_beta = firm_fund_structure / "TestFirm" / "FundBeta - 1002"

        # Move file 1
        old1 = fund_alpha / "factsheet.pdf"
        new1 = fund_beta / "factsheet.pdf"
        new1.write_bytes(b"alpha factsheet")

        # Move file 2
        old2 = fund_alpha / "monthly_letter.pdf"
        new2 = fund_beta / "monthly_letter.pdf"
        new2.write_bytes(b"alpha letter")

        # Fire both events rapidly
        handler.on_moved(FileMovedEvent(str(old1), str(new1)))
        handler.on_moved(FileMovedEvent(str(old2), str(new2)))

        time.sleep(2.0)

        # Both files should be removed from Alpha
        assert mock_remove.call_count == 2
        remove_calls = {c.args[1] for c in mock_remove.call_args_list}
        assert "factsheet.pdf" in remove_calls
        assert "monthly_letter.pdf" in remove_calls

        # Both files should be uploaded to Beta
        assert mock_upload.call_count == 2

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._remove_file_from_firm_property")
    @patch("fofproject.notion._upload_firm_files_to_property")
    @patch("fofproject.notion._query_database_by_title")
    def test_files_moved_to_different_destinations_simultaneously(
        self, mock_query, mock_firm_upload, mock_firm_remove, mock_fund_remove,
        mock_sections, mock_fund_upload, firm_fund_structure, handler
    ):
        """One file moves fund→fund, another moves fund→firm, at the same time."""
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            if name == "FundBeta":
                return make_mock_query_result("fund-page-beta", "FundBeta")
            if name == "TestFirm":
                return make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)
            return None

        mock_query.side_effect = query_side_effect
        mock_sections.return_value = {"Others": "callout-1"}

        from watchdog.events import FileMovedEvent

        fund_alpha = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        fund_beta = firm_fund_structure / "TestFirm" / "FundBeta - 1002"
        firm = firm_fund_structure / "TestFirm"

        # File 1: FundAlpha → FundBeta
        old1 = fund_alpha / "factsheet.pdf"
        new1 = fund_beta / "factsheet.pdf"
        new1.write_bytes(b"alpha factsheet")

        # File 2: FundAlpha → TestFirm (firm level)
        old2 = fund_alpha / "monthly_letter.pdf"
        new2 = firm / "monthly_letter.pdf"
        new2.write_bytes(b"alpha letter")

        handler.on_moved(FileMovedEvent(str(old1), str(new1)))
        handler.on_moved(FileMovedEvent(str(old2), str(new2)))

        time.sleep(2.0)

        # Both files removed from fund Alpha
        assert mock_fund_remove.call_count == 2

        # File 1 uploaded to fund Beta
        mock_fund_upload.assert_called_once()

        # File 2 uploaded to firm
        mock_firm_upload.assert_called_once()

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_three_consecutive_moves_same_file(
        self, mock_query, mock_remove, mock_sections, mock_upload, firm_fund_structure, handler
    ):
        """A file is moved A→B, then B→C in rapid succession.

        Each move event should be handled independently. The handler uses
        the path info from the event, so even if the file has already moved
        by the time the thread runs, the remove still uses the correct old name.
        """
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            if name == "FundBeta":
                return make_mock_query_result("fund-page-beta", "FundBeta")
            return None

        mock_query.side_effect = query_side_effect
        mock_sections.return_value = {"Others": "callout-1"}

        from watchdog.events import FileMovedEvent

        fund_alpha = firm_fund_structure / "TestFirm" / "FundAlpha - 1001"
        fund_beta = firm_fund_structure / "TestFirm" / "FundBeta - 1002"

        # Move 1: Alpha → Beta
        old1 = fund_alpha / "factsheet.pdf"
        new1 = fund_beta / "factsheet.pdf"
        new1.write_bytes(b"content")

        # Move 2: Beta → Alpha (move it back)
        old2 = fund_beta / "factsheet.pdf"
        new2 = fund_alpha / "factsheet.pdf"
        new2.write_bytes(b"content")

        handler.on_moved(FileMovedEvent(str(old1), str(new1)))
        handler.on_moved(FileMovedEvent(str(old2), str(new2)))

        time.sleep(2.0)

        # Should have 2 remove calls (one from Alpha, one from Beta)
        assert mock_remove.call_count == 2
        # Should have 2 upload calls (one to Beta, one back to Alpha)
        assert mock_upload.call_count == 2

    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_rapid_delete_multiple_files(self, mock_query, mock_remove, firm_fund_structure, handler):
        """Rapidly deleting multiple files should trigger separate Notion removals."""
        mock_query.return_value = make_mock_query_result("fund-page-alpha", "FundAlpha")

        from watchdog.events import FileDeletedEvent
        files = [
            firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf",
            firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "monthly_letter.pdf",
        ]
        for f in files:
            handler.on_deleted(FileDeletedEvent(str(f)))

        time.sleep(1.0)

        assert mock_remove.call_count == 2


# =============================================================================
# EDGE CASES & NICHE SCENARIOS
# =============================================================================

class TestEdgeCases:
    """Niche edge cases that are easy to miss."""

    def test_parse_fund_folder_name_with_dashes_in_name(self):
        """Fund name with dashes (e.g. 'Long-Short Fund - 123') should parse correctly."""
        result = parse_fund_folder_name("Long-Short Fund - 123")
        assert result is not None
        assert result[0] == "Long-Short Fund"
        assert result[1] == "123"

    def test_parse_fund_folder_name_no_identifier(self):
        """Folder without identifier pattern should return None."""
        assert parse_fund_folder_name("JustAFolder") is None

    def test_parse_fund_folder_name_alpha_identifier_fails(self):
        """Identifier must be numeric — alpha IDs should not match."""
        assert parse_fund_folder_name("Fund - abc") is None

    def test_normalize_filename(self):
        """Filename normalization should handle spaces and case."""
        assert _normalize_filename("My Report.pdf") == "my_report.pdf"
        assert _normalize_filename("MY_REPORT.PDF") == "my_report.pdf"

    @patch("fofproject.notion._query_database_by_title")
    def test_fund_folder_with_conflict_prefix_skipped(self, mock_query, firm_fund_structure, handler):
        """Fund folders with CONFLICT_IDENTIFIER_PREFIX should be skipped."""
        from watchdog.events import DirCreatedEvent
        conflict_dir = firm_fund_structure / "TestFirm" / "404 multiple identifier FundX"
        conflict_dir.mkdir()
        event = DirCreatedEvent(str(conflict_dir))

        handler.on_created(event)
        time.sleep(0.3)

        mock_query.assert_not_called()

    @patch("fofproject.notion._rename_page_title")
    @patch("fofproject.notion._query_database_by_title")
    def test_firm_rename_then_fund_rename_sequentially(self, mock_query, mock_rename, firm_fund_structure, handler):
        """Renaming firm then fund should update both pages."""
        mock_query.side_effect = [
            make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP),
            make_mock_query_result("fund-page-alpha", "FundAlpha"),
        ]

        from watchdog.events import DirMovedEvent

        # Rename firm
        event1 = DirMovedEvent(
            str(firm_fund_structure / "TestFirm"),
            str(firm_fund_structure / "NewFirm"),
        )
        handler.on_moved(event1)
        time.sleep(0.3)

        # Rename fund (under the now-renamed firm)
        event2 = DirMovedEvent(
            str(firm_fund_structure / "NewFirm" / "FundAlpha - 1001"),
            str(firm_fund_structure / "NewFirm" / "NewFundAlpha - 1001"),
        )
        handler.on_moved(event2)
        time.sleep(0.3)

        assert mock_rename.call_count == 2

    @patch("fofproject.notion._rename_file_on_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_renamed_within_firm(self, mock_query, mock_rename, firm_fund_structure, handler):
        """Renaming a file within a firm folder should rename on Notion."""
        mock_query.return_value = make_mock_query_result("firm-page-1", "TestFirm", FIRM_TITLE_PROP)
        mock_rename.return_value = True

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "firm_report.pdf"
        new = firm_fund_structure / "TestFirm" / "firm_report_v2.pdf"
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(0.5)

        mock_rename.assert_called_once_with("firm-page-1", "firm_report.pdf", "firm_report_v2.pdf")

    @patch("fofproject.notion._upload_files_to_sections")
    @patch("fofproject.notion._find_section_callout_ids")
    @patch("fofproject.notion._remove_file_from_page")
    @patch("fofproject.notion._query_database_by_title")
    def test_file_moved_but_dest_not_in_notion(self, mock_query, mock_remove, mock_sections, mock_upload, firm_fund_structure, handler):
        """Moving a file to a fund that doesn't exist in Notion should still remove from source."""
        def query_side_effect(db_id, name, prop):
            if name == "FundAlpha":
                return make_mock_query_result("fund-page-alpha", "FundAlpha")
            return None  # FundBeta not in Notion

        mock_query.side_effect = query_side_effect

        from watchdog.events import FileMovedEvent
        old = firm_fund_structure / "TestFirm" / "FundAlpha - 1001" / "factsheet.pdf"
        new = firm_fund_structure / "TestFirm" / "FundBeta - 1002" / "factsheet.pdf"
        new.write_bytes(b"content")
        event = FileMovedEvent(str(old), str(new))

        handler.on_moved(event)
        time.sleep(1.0)

        # Should still remove from source
        mock_remove.assert_called_once_with("fund-page-alpha", "factsheet.pdf")
        # Should NOT upload (dest not found)
        mock_upload.assert_not_called()


# =============================================================================
# UNIT TESTS for helper functions
# =============================================================================

class TestHelperFunctions:
    """Test the low-level helper functions for correctness."""

    def test_parse_fund_folder_name_normal(self):
        assert parse_fund_folder_name("MyFund - 12345") == ("MyFund", "12345")

    def test_parse_fund_folder_name_extra_spaces(self):
        result = parse_fund_folder_name("  MyFund  -  12345  ")
        assert result is not None
        assert result[0] == "MyFund"
        assert result[1] == "12345"

    def test_parse_fund_folder_name_multiple_dashes(self):
        result = parse_fund_folder_name("My-Long-Named-Fund - 99999")
        assert result is not None
        assert result[0] == "My-Long-Named-Fund"
        assert result[1] == "99999"

    def test_normalize_filename_underscore_space_equivalence(self):
        assert _normalize_filename("my report.pdf") == _normalize_filename("my_report.pdf")

    def test_skip_subfolders_set(self):
        """Verify the SKIP_SUBFOLDERS set contains expected entries."""
        assert "graph" in SKIP_SUBFOLDERS
        assert "json" in SKIP_SUBFOLDERS
        assert "meetings" in SKIP_SUBFOLDERS
        assert "researches" in SKIP_SUBFOLDERS

    @patch("fofproject.notion._notion_request")
    def test_archive_page_sends_correct_payload(self, mock_request):
        """_archive_page should PATCH the page with archived: true."""
        _archive_page("test-page-id")
        mock_request.assert_called_once_with(
            "patch", "/pages/test-page-id",
            json={"archived": True}
        )
