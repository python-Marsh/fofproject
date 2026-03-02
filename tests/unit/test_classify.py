"""
Unit tests for fofproject.classify module.

Tests cover:
1. Pure functions (sanitize_folder_name, normalize_firm_name, etc.)
2. Complex integration tests (classify_and_organize_emails)
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from fofproject.classify import (
    sanitize_folder_name,
    normalize_firm_name,
    check_email_override,
    apply_folder_reassignment,
    extract_domain_hints,
    classify_and_organize_emails,
    add_fund_to_firm,
    _parse_firm_fund_from_path,
    _build_destination_index,
    sync_moved_artifacts,
    extract_links_with_filter_log,
    _finalize_artifact_classification,
    _condense_skipped_artifact,
    _blank_link_artifact,
    _blank_attachment_artifact,
    NEEDS_REVIEW_FOLDER,
    CLASSIFICATION_CACHE_FILE,
    CLASSIFICATION_REPORT_FILE,
    FIRM_MAPPINGS_FILE,
    REASON_CODE_OTHER,
)


def create_email_folder(parent_dir: Path, folder_name: str, metadata: dict) -> Path:
    """
    Helper function to create an email folder with metadata.json for testing.

    Args:
        parent_dir: Parent directory (email_input_dir fixture)
        folder_name: Name of the email folder
        metadata: Email metadata dictionary

    Returns:
        Path to the created folder
    """
    folder = parent_dir / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    metadata_file = folder / "metadata.json"
    metadata_file.write_text(json.dumps(metadata, indent=2))
    return folder


# =============================================================================
# Tests for sanitize_folder_name()
# =============================================================================

class TestSanitizeFolderName:
    """Tests for sanitize_folder_name function."""

    def test_removes_invalid_windows_characters(self):
        """Should remove characters invalid on Windows: < > : " / \\ | ? *"""
        assert sanitize_folder_name('Firm<>Name') == 'FirmName'
        assert sanitize_folder_name('Test:File') == 'TestFile'
        assert sanitize_folder_name('Path/Name') == 'PathName'
        assert sanitize_folder_name('Query?String') == 'QueryString'
        assert sanitize_folder_name('Wild*Card') == 'WildCard'

    def test_handles_reserved_windows_names(self):
        """Should wrap reserved Windows names with underscores."""
        assert sanitize_folder_name('CON') == '_CON_'
        assert sanitize_folder_name('PRN') == '_PRN_'
        assert sanitize_folder_name('AUX') == '_AUX_'
        assert sanitize_folder_name('NUL') == '_NUL_'
        assert sanitize_folder_name('COM1') == '_COM1_'
        assert sanitize_folder_name('LPT1') == '_LPT1_'

    def test_limits_length_to_100_characters(self):
        """Should truncate names longer than 100 characters."""
        long_name = 'A' * 150
        result = sanitize_folder_name(long_name)
        assert len(result) <= 100

    def test_removes_leading_trailing_dots_and_spaces(self):
        """Should remove leading/trailing dots and spaces (Windows restriction)."""
        assert sanitize_folder_name('  Firm Name  ') == 'Firm Name'
        assert sanitize_folder_name('...Firm...') == 'Firm'
        assert sanitize_folder_name('. . Firm . .') == 'Firm'

    def test_returns_unknown_for_empty_input(self):
        """Should return 'UNKNOWN' for empty or None input."""
        assert sanitize_folder_name('') == 'UNKNOWN'
        assert sanitize_folder_name(None) == 'UNKNOWN'
        assert sanitize_folder_name('   ') == 'UNKNOWN'

    def test_collapses_multiple_spaces(self):
        """Should collapse multiple consecutive spaces into single space."""
        assert sanitize_folder_name('Firm    Name    Here') == 'Firm Name Here'


# =============================================================================
# Tests for normalize_firm_name()
# =============================================================================

class TestNormalizeFirmName:
    """Tests for normalize_firm_name function."""

    def test_returns_canonical_name_for_exact_match(self, sample_mappings):
        """Should return canonical name when input matches exactly (case-insensitive)."""
        assert normalize_firm_name('SPRINGS CAPITAL', sample_mappings) == 'SPRINGS CAPITAL'
        assert normalize_firm_name('springs capital', sample_mappings) == 'SPRINGS CAPITAL'
        assert normalize_firm_name('Springs Capital', sample_mappings) == 'SPRINGS CAPITAL'

    def test_resolves_alias_to_canonical_name(self, sample_mappings):
        """Should resolve aliases to their canonical firm name."""
        assert normalize_firm_name('springs-capital', sample_mappings) == 'SPRINGS CAPITAL'
        assert normalize_firm_name('Springs Capital HK', sample_mappings) == 'SPRINGS CAPITAL'
        assert normalize_firm_name('e20capital', sample_mappings) == 'E20 CAPITAL'

    def test_removes_common_suffixes(self, empty_mappings):
        """Should remove common business suffixes (LLC, Ltd., Inc., LP, etc.)."""
        assert normalize_firm_name('Acme Capital LLC', empty_mappings) == 'ACME CAPITAL'
        assert normalize_firm_name('Test Fund Ltd.', empty_mappings) == 'TEST FUND'
        assert normalize_firm_name('Investment Inc.', empty_mappings) == 'INVESTMENT'
        assert normalize_firm_name('Partners LP', empty_mappings) == 'PARTNERS'
        assert normalize_firm_name('Holdings LLP', empty_mappings) == 'HOLDINGS'

    def test_returns_uppercase_when_no_match(self, empty_mappings):
        """Should return uppercase cleaned name when no canonical match found."""
        assert normalize_firm_name('New Firm Name', empty_mappings) == 'NEW FIRM NAME'
        assert normalize_firm_name('  spaced name  ', empty_mappings) == 'SPACED NAME'

    def test_substring_matches_existing_canonical(self):
        """Shorter input should match longer canonical via substring."""
        mappings = {
            "canonical_names": {
                "SG CAPITAL MANAGEMENT": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        assert normalize_firm_name('SG Capital', mappings) == 'SG CAPITAL MANAGEMENT'

    def test_canonical_substring_of_input(self):
        """Longer input (after suffix strip) should match shorter canonical."""
        mappings = {
            "canonical_names": {
                "SG CAPITAL": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        assert normalize_firm_name('SG Capital Management Ltd', mappings) == 'SG CAPITAL'

    def test_generic_words_skip_substring_match(self):
        """All-generic-word names should not substring-match."""
        mappings = {
            "canonical_names": {
                "SG CAPITAL MANAGEMENT": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        # "Capital" is all-generic → should NOT match "SG CAPITAL MANAGEMENT"
        assert normalize_firm_name('Capital', mappings) == 'CAPITAL'
        # "Capital Management" is all-generic → should NOT match
        assert normalize_firm_name('Capital Management', mappings) == 'CAPITAL MANAGEMENT'

    def test_unique_single_word_substring_matches(self):
        """Unique single-word name should substring-match existing canonical."""
        mappings = {
            "canonical_names": {
                "CITADEL SECURITIES": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        assert normalize_firm_name('Citadel', mappings) == 'CITADEL SECURITIES'

    def test_longest_overlap_wins(self):
        """When multiple canonicals match, longest overlap should win."""
        mappings = {
            "canonical_names": {
                "SG CAPITAL MANAGEMENT": {"aliases": [], "funds": {}},
                "SG CAPITAL": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        # "SG Capital Management Group" contains both canonicals;
        # "SG CAPITAL MANAGEMENT" (21 chars) has longer overlap than "SG CAPITAL" (10 chars)
        assert normalize_firm_name('SG Capital Management Group', mappings) == 'SG CAPITAL MANAGEMENT'

    def test_no_substring_match_for_unrelated(self):
        """Unrelated names should not substring-match."""
        mappings = {
            "canonical_names": {
                "SG CAPITAL MANAGEMENT": {"aliases": [], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        assert normalize_firm_name('ABC Capital Partners', mappings) == 'ABC CAPITAL PARTNERS'

    def test_substring_matches_via_alias(self):
        """Substring match should also check aliases."""
        mappings = {
            "canonical_names": {
                "SPRINGS CAPITAL": {"aliases": ["Springs Cap HK"], "funds": {}},
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }
        # "Springs Cap" is substring of alias "Springs Cap HK"
        assert normalize_firm_name('Springs Cap', mappings) == 'SPRINGS CAPITAL'


# =============================================================================
# Tests for check_email_override()
# =============================================================================

class TestCheckEmailOverride:
    """Tests for check_email_override function."""

    def test_returns_firm_for_exact_email_match(self, sample_mappings):
        """Should return firm name when exact email address matches."""
        assert check_email_override('john@specific.com', sample_mappings) == 'ACME CORP'
        assert check_email_override('jane.doe@example.com', sample_mappings) == 'SPRINGS CAPITAL'

    def test_email_match_is_case_insensitive(self, sample_mappings):
        """Should match email addresses case-insensitively."""
        assert check_email_override('JOHN@SPECIFIC.COM', sample_mappings) == 'ACME CORP'
        assert check_email_override('John@Specific.Com', sample_mappings) == 'ACME CORP'

    def test_returns_firm_for_domain_match(self, sample_mappings):
        """Should return firm name when email domain matches domain_overrides."""
        assert check_email_override('anyone@springscap.com', sample_mappings) == 'SPRINGS CAPITAL'
        assert check_email_override('info@e20capital.com', sample_mappings) == 'E20 CAPITAL'

    def test_returns_none_when_no_match(self, sample_mappings):
        """Should return None when no email or domain override matches."""
        assert check_email_override('unknown@unknown.com', sample_mappings) is None
        assert check_email_override('test@nomatch.org', sample_mappings) is None

    def test_returns_none_for_empty_input(self, sample_mappings):
        """Should return None for empty or None email address."""
        assert check_email_override('', sample_mappings) is None
        assert check_email_override(None, sample_mappings) is None


# =============================================================================
# Tests for apply_folder_reassignment()
# =============================================================================

class TestApplyFolderReassignment:
    """Tests for apply_folder_reassignment function."""

    def test_returns_reassigned_name_when_found(self, sample_mappings):
        """Should return the new firm name when reassignment is configured."""
        assert apply_folder_reassignment('OLD FIRM', sample_mappings) == 'NEW FIRM'
        assert apply_folder_reassignment('LEGACY CAPITAL', sample_mappings) == 'SPRINGS CAPITAL'

    def test_reassignment_is_case_insensitive(self, sample_mappings):
        """Should match firm names case-insensitively for reassignment."""
        assert apply_folder_reassignment('old firm', sample_mappings) == 'NEW FIRM'
        assert apply_folder_reassignment('Old Firm', sample_mappings) == 'NEW FIRM'

    def test_returns_original_when_no_reassignment(self, sample_mappings):
        """Should return original name when no reassignment is configured."""
        assert apply_folder_reassignment('SPRINGS CAPITAL', sample_mappings) == 'SPRINGS CAPITAL'
        assert apply_folder_reassignment('UNKNOWN FIRM', sample_mappings) == 'UNKNOWN FIRM'

    def test_returns_original_for_empty_input(self, sample_mappings):
        """Should return original value for empty or None input."""
        assert apply_folder_reassignment('', sample_mappings) == ''
        assert apply_folder_reassignment(None, sample_mappings) is None


# =============================================================================
# Tests for extract_domain_hints()
# =============================================================================

class TestExtractDomainHints:
    """Tests for extract_domain_hints function."""

    def test_extracts_meaningful_domain_parts(self):
        """Should extract meaningful parts from email domain."""
        hints = extract_domain_hints('john@springscapital.com')
        assert 'springscapital' in hints

    def test_filters_common_words(self):
        """Should filter out common words like mail, info, www."""
        hints = extract_domain_hints('contact@mail.example.com')
        assert 'mail' not in hints

        hints = extract_domain_hints('support@info.company.com')
        assert 'info' not in hints

    def test_handles_missing_at_symbol(self):
        """Should return empty list for invalid email without @."""
        assert extract_domain_hints('invalidemail.com') == []
        assert extract_domain_hints('no-at-symbol') == []

    def test_returns_empty_for_empty_input(self):
        """Should return empty list for empty or None input."""
        assert extract_domain_hints('') == []
        assert extract_domain_hints(None) == []


# =============================================================================
# Tests for classify_and_organize_emails() - Complex Integration Tests
# =============================================================================

class TestClassifyAndOrganizeEmails:
    """
    Integration tests for classify_and_organize_emails().

    These tests use:
    - tmp_path fixture for isolated file system operations
    - Mocked GPT responses to avoid real API calls
    - Helper functions to create test email folders
    """

    def test_email_with_domain_override_skips_gpt(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt
    ):
        """Email matching a domain override should use override and skip GPT."""
        # Setup: Create email from a domain that has an override
        metadata = {
            "id": "test-email-001",
            "subject": "Monthly Update",
            "from": {"emailAddress": {"name": "John", "address": "john@springscap.com"}},
        }
        create_email_folder(email_input_dir, "email_001", metadata)

        # Create firm mappings with domain override
        mappings = {
            "canonical_names": {"SPRINGS CAPITAL": {"aliases": [], "funds": {}}},
            "email_overrides": {},
            "domain_overrides": {"springscap.com": "SPRINGS CAPITAL"},
            "folder_reassignments": {},
            "_metadata": {"created": "2025-01-01"},
        }
        mappings_file = output_dir / "firm_fund_mappings.json"
        mappings_file.write_text(json.dumps(mappings))

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: GPT was NOT called (domain override used)
        patch_classify_email_with_gpt.assert_not_called()

        # Assert: Email was classified correctly
        assert report["total_emails"] == 1
        assert report["hedge_fund_related"] == 1

        # Assert: Email was copied to firm folder (flat structure)
        firm_folder = output_dir / "SPRINGS CAPITAL"
        assert firm_folder.exists()

    def test_cached_email_skips_gpt(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt
    ):
        """Email already in cache should use cached result and skip GPT."""
        # Setup: Create email
        metadata = {
            "id": "cached-email-123",
            "subject": "Cached Email Test",
            "from": {"emailAddress": {"name": "Test", "address": "test@unknown.com"}},
        }
        create_email_folder(email_input_dir, "email_cached", metadata)

        # Pre-populate cache with classification
        cache = {
            "cached-email-123": {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 0.95,
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": "From cache",
                    "email_type": "other",
                },
                "firm_name": "CACHED FIRM",
                "artifact_assignments": {
                    "included_attachments": [],
                    "included_links": [],
                    "skipped_attachments": [],
                    "skipped_links": [],
                    "summary": {
                        "total_attachments": 0,
                        "total_links": 0,
                        "included_count": 0,
                        "skipped_count": 0,
                    },
                },
            }
        }
        cache_file = output_dir / "classification_cache.json"
        cache_file.write_text(json.dumps(cache))

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: GPT was NOT called (cache used)
        patch_classify_email_with_gpt.assert_not_called()

        # Assert: Email was copied using cached firm name
        firm_folder = output_dir / "CACHED FIRM"
        assert firm_folder.exists()

    def test_hedge_fund_email_copied_to_hedge_funds_folder(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt, mock_gpt_hedge_fund_response
    ):
        """Hedge fund email should be copied to hedge funds/{FIRM}/ folder."""
        # Setup: Create email
        metadata = {
            "id": "hf-email-001",
            "subject": "Fund Performance Update",
            "from": {"emailAddress": {"name": "PM", "address": "pm@testcapital.com"}},
        }
        create_email_folder(email_input_dir, "email_hf", metadata)

        # Mock GPT to return hedge fund classification
        patch_classify_email_with_gpt.return_value = mock_gpt_hedge_fund_response

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: Classified as hedge fund
        assert report["hedge_fund_related"] == 1

        # Assert: Email copied to firm folder (flat structure)
        firm_folder = output_dir / "TEST CAPITAL"
        assert firm_folder.exists()

    def test_third_party_email_copied_to_third_parties_folder(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt, mock_gpt_third_party_response
    ):
        """Third-party email should be copied to 3rd parties/{FIRM}/ folder."""
        # Setup: Create email
        metadata = {
            "id": "tp-email-001",
            "subject": "Cap Intro Opportunity",
            "from": {"emailAddress": {"name": "Broker", "address": "capintro@gs.com"}},
        }
        create_email_folder(email_input_dir, "email_tp", metadata)

        # Mock GPT to return third-party classification
        patch_classify_email_with_gpt.return_value = mock_gpt_third_party_response

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: Email copied to firm folder (flat structure, no 3rd parties split)
        firm_folder = output_dir / "GOLDMAN SACHS"
        assert firm_folder.exists()

    def test_non_hedge_fund_email_not_copied(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt, mock_gpt_not_hedge_fund_response
    ):
        """Non-hedge-fund email should not be copied to any firm folder."""
        # Setup: Create email
        metadata = {
            "id": "non-hf-001",
            "subject": "Generic Marketing",
            "from": {"emailAddress": {"name": "Marketing", "address": "spam@random.com"}},
        }
        create_email_folder(email_input_dir, "email_non_hf", metadata)

        # Mock GPT to return non-hedge-fund classification
        patch_classify_email_with_gpt.return_value = mock_gpt_not_hedge_fund_response

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: Classified as non-hedge-fund
        assert report["non_hedge_fund"] == 1
        assert report["hedge_fund_related"] == 0

        # Assert: No firm folders created in output dir
        # Only system files (cache, mappings, report) should be in output_dir, no firm folders
        firm_dirs = [f for f in output_dir.iterdir() if f.is_dir()]
        assert len(firm_dirs) == 0

    def test_metadata_error_increments_error_count(self, email_input_dir, output_dir):
        """Email folder with invalid metadata.json should increment error count."""
        # Setup: Create folder with invalid JSON
        bad_folder = email_input_dir / "bad_email"
        bad_folder.mkdir()
        (bad_folder / "metadata.json").write_text("{ invalid json }")

        # Run classification
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

        # Assert: Error was counted
        assert report["errors"] == 1
        assert report["total_emails"] == 1

    def test_force_reclassify_ignores_cache(
        self, email_input_dir, output_dir, patch_classify_email_with_gpt, mock_gpt_hedge_fund_response
    ):
        """force_reclassify=True should call GPT even if email is in cache."""
        # Setup: Create email
        metadata = {
            "id": "force-reclass-001",
            "subject": "Force Reclassify Test",
            "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
        }
        create_email_folder(email_input_dir, "email_force", metadata)

        # Pre-populate cache (should be ignored)
        cache = {
            "force-reclass-001": {
                "is_hedge_fund_related": False,
                "confidence": 0.5,
                "firm_name": "",
            }
        }
        cache_file = output_dir / "classification_cache.json"
        cache_file.write_text(json.dumps(cache))

        # Mock GPT to return hedge fund classification
        patch_classify_email_with_gpt.return_value = mock_gpt_hedge_fund_response

        # Run classification with force_reclassify=True
        with patch("fofproject.classify.get_openai_client"):
            report = classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
                force_reclassify=True,
            )

        # Assert: GPT WAS called (cache ignored)
        patch_classify_email_with_gpt.assert_called_once()

        # Assert: New classification was used
        assert report["hedge_fund_related"] == 1


# =============================================================================
# Tests for new utility functions
# =============================================================================

class TestAddFundToFirm:
    """Tests for add_fund_to_firm function."""

    def test_adds_fund_to_existing_firm(self, sample_mappings):
        fund_id = add_fund_to_firm(
            "SPRINGS CAPITAL",
            "Springs Global Macro Fund",
            ["Global Macro"],
            sample_mappings
        )
        assert fund_id == "fund_springs_global_macro"
        assert fund_id in sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["funds"]
        fund_info = sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["funds"][fund_id]
        assert fund_info["display_name"] == "Springs Global Macro Fund"
        assert "Global Macro" in fund_info["aliases"]

    def test_returns_existing_fund_id_for_duplicate(self, sample_mappings):
        # "Springs China Alpha Fund" already exists
        fund_id = add_fund_to_firm(
            "SPRINGS CAPITAL",
            "Springs China Alpha Fund",
            [],
            sample_mappings
        )
        assert fund_id == "fund_springs_china_alpha"

    def test_returns_empty_for_unknown_firm(self, sample_mappings):
        fund_id = add_fund_to_firm(
            "NONEXISTENT FIRM",
            "Some Fund",
            [],
            sample_mappings
        )
        assert fund_id == ""


# =============================================================================
# Tests for _parse_firm_fund_from_path()
# =============================================================================

class TestParseFirmFundFromPath:
    def test_firm_only(self, tmp_path):
        file_path = tmp_path / "SPRINGS CAPITAL" / "2026-02-28_report.pdf"
        firm, fund = _parse_firm_fund_from_path(file_path, tmp_path)
        assert firm == "SPRINGS CAPITAL"
        assert fund == ""

    def test_firm_and_fund(self, tmp_path):
        file_path = tmp_path / "SPRINGS CAPITAL" / "CHINA ALPHA" / "report.pdf"
        firm, fund = _parse_firm_fund_from_path(file_path, tmp_path)
        assert firm == "SPRINGS CAPITAL"
        assert fund == "CHINA ALPHA"

    def test_needs_review(self, tmp_path):
        file_path = tmp_path / NEEDS_REVIEW_FOLDER / "report.pdf"
        firm, fund = _parse_firm_fund_from_path(file_path, tmp_path)
        assert firm == ""
        assert fund == ""

    def test_root_file(self, tmp_path):
        file_path = tmp_path / "some_file.json"
        firm, fund = _parse_firm_fund_from_path(file_path, tmp_path)
        assert firm == ""
        assert fund == ""

    def test_outside_output_dir(self, tmp_path):
        file_path = Path("/totally/different/path/file.pdf")
        firm, fund = _parse_firm_fund_from_path(file_path, tmp_path)
        assert firm == ""
        assert fund == ""


# =============================================================================
# Tests for _build_destination_index()
# =============================================================================

class TestBuildDestinationIndex:
    def test_builds_index_from_report(self):
        report = {
            "classifications": [
                {
                    "email_id": "email_001",
                    "destinations": ["/out/FIRM_A/2026-02-28_report.pdf"],
                    "artifact_assignments": {
                        "included_attachments": [
                            {"artifact_id": "att_1", "filename": "report.pdf"}
                        ],
                        "included_links": [],
                    },
                }
            ]
        }
        index = _build_destination_index(report)
        assert "/out/FIRM_A/2026-02-28_report.pdf" in index["by_path"]
        record = index["by_path"]["/out/FIRM_A/2026-02-28_report.pdf"]
        assert record["email_id"] == "email_001"
        assert record["list_key"] == "included_attachments"
        assert record["idx"] == 0

    def test_indexes_both_attachments_and_links(self):
        report = {
            "classifications": [
                {
                    "email_id": "email_002",
                    "destinations": [
                        "/out/FIRM/att.pdf",
                        "/out/FIRM/link.link.json",
                    ],
                    "artifact_assignments": {
                        "included_attachments": [
                            {"artifact_id": "att_1", "filename": "att.pdf"}
                        ],
                        "included_links": [
                            {"artifact_id": "link:1", "url": "https://example.com"}
                        ],
                    },
                }
            ]
        }
        index = _build_destination_index(report)
        assert index["by_path"]["/out/FIRM/att.pdf"]["list_key"] == "included_attachments"
        assert index["by_path"]["/out/FIRM/link.link.json"]["list_key"] == "included_links"

    def test_empty_report(self):
        index = _build_destination_index({"classifications": []})
        assert index["by_path"] == {}
        assert index["by_filename"] == {}

    def test_filename_index_built(self):
        report = {
            "classifications": [
                {
                    "email_id": "email_003",
                    "destinations": ["/out/FIRM/2026-02-28_report.pdf"],
                    "artifact_assignments": {
                        "included_attachments": [
                            {"artifact_id": "att_1", "filename": "report.pdf"}
                        ],
                        "included_links": [],
                    },
                }
            ]
        }
        index = _build_destination_index(report)
        assert "2026-02-28_report.pdf" in index["by_filename"]
        assert len(index["by_filename"]["2026-02-28_report.pdf"]) == 1


# =============================================================================
# Tests for sync_moved_artifacts()
# =============================================================================

class TestSyncMovedArtifacts:
    def _setup_output_dir(self, tmp_path):
        """Create a minimal output dir with cache, report, mappings, and one artifact."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        firm_dir = output_dir / "FIRM_A"
        firm_dir.mkdir()

        # Create an artifact file
        artifact_file = firm_dir / "2026-02-28_report.pdf"
        artifact_file.write_text("fake pdf content")

        # Classification cache
        cache = {
            "email_001": {
                "email_classification": {
                    "is_hedge_fund_related": True,
                    "confidence": 0.9,
                    "email_type": "Monthly performance update",
                    "from_third_party": False,
                    "source_priority": "highest",
                    "reasoning": "test",
                },
                "firm_name": "FIRM_A",
                "artifact_assignments": {
                    "included_attachments": [
                        {
                            "artifact_id": "att_1",
                            "filename": "report.pdf",
                            "mime_type": "application/pdf",
                            "assigned_firm_name": "FIRM_A",
                            "assigned_fund_name": "",
                            "confidence": 0.9,
                            "method": "email_context",
                            "evidence": "test evidence",
                            "reason_code": "fund_document",
                            "contains_monthly_net_performance_update": False,
                            "_original_firm_name": "FIRM_A",
                            "_original_fund_name": "",
                            "firm_recovery_method": "",
                        }
                    ],
                    "included_links": [],
                    "skipped_attachments": [],
                    "skipped_links": [],
                    "summary": {
                        "total_attachments": 1,
                        "total_links": 0,
                        "included_count": 1,
                        "skipped_count": 0,
                    },
                },
            }
        }
        (output_dir / CLASSIFICATION_CACHE_FILE).write_text(
            json.dumps(cache, indent=2)
        )

        # Report
        report = {
            "classifications": [
                {
                    "email_id": "email_001",
                    "email_folder": "folder_001",
                    "subject": "Test Email",
                    "from": "test@example.com",
                    **cache["email_001"],
                    "destinations": [str(artifact_file)],
                    "organized_count": 1,
                    "needs_review_count": 0,
                }
            ]
        }
        (output_dir / CLASSIFICATION_REPORT_FILE).write_text(
            json.dumps(report, indent=2)
        )

        # Firm mappings
        mappings = {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": [],
                    "description": "",
                    "funds": {},
                }
            },
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
            "_metadata": {"created": "2026-01-01", "last_updated": "2026-01-01"},
        }
        (output_dir / FIRM_MAPPINGS_FILE).write_text(
            json.dumps(mappings, indent=2)
        )

        return output_dir, artifact_file

    def test_no_moves_detected(self, tmp_path):
        output_dir, _ = self._setup_output_dir(tmp_path)
        result = sync_moved_artifacts(output_dir)
        assert result["moved"] == []
        assert result["errors"] == []

    def test_move_to_different_firm(self, tmp_path):
        output_dir, artifact_file = self._setup_output_dir(tmp_path)

        # Move the file to FIRM_B
        firm_b_dir = output_dir / "FIRM_B"
        firm_b_dir.mkdir()
        new_path = firm_b_dir / artifact_file.name
        artifact_file.rename(new_path)

        result = sync_moved_artifacts(output_dir)
        assert len(result["moved"]) == 1
        assert result["moved"][0]["firm"] == "FIRM_B"
        assert result["moved"][0]["from"] == "FIRM_A"
        assert result["moved"][0]["to"] == "FIRM_B"

        # Verify cache was updated
        cache = json.loads((output_dir / CLASSIFICATION_CACHE_FILE).read_text())
        att = cache["email_001"]["artifact_assignments"]["included_attachments"][0]
        assert att["assigned_firm_name"] == "FIRM_B"
        assert att["method"] == "manual_reassignment"

        # Verify firm_mappings was updated
        mappings = json.loads((output_dir / FIRM_MAPPINGS_FILE).read_text())
        assert "FIRM_B" in mappings["canonical_names"]

    def test_move_to_firm_and_fund(self, tmp_path):
        output_dir, artifact_file = self._setup_output_dir(tmp_path)

        # Move file to FIRM_B/FUND_X/
        fund_dir = output_dir / "FIRM_B" / "FUND_X"
        fund_dir.mkdir(parents=True)
        new_path = fund_dir / artifact_file.name
        artifact_file.rename(new_path)

        result = sync_moved_artifacts(output_dir)
        assert len(result["moved"]) == 1
        assert result["moved"][0]["firm"] == "FIRM_B"
        assert result["moved"][0]["fund"] == "FUND_X"

        cache = json.loads((output_dir / CLASSIFICATION_CACHE_FILE).read_text())
        att = cache["email_001"]["artifact_assignments"]["included_attachments"][0]
        assert att["assigned_firm_name"] == "FIRM_B"
        assert att["assigned_fund_name"] == "FUND_X"

    def test_move_to_needs_review(self, tmp_path):
        output_dir, artifact_file = self._setup_output_dir(tmp_path)

        # Move to _NEEDS_REVIEW
        review_dir = output_dir / NEEDS_REVIEW_FOLDER
        review_dir.mkdir()
        new_path = review_dir / artifact_file.name
        artifact_file.rename(new_path)

        result = sync_moved_artifacts(output_dir)
        assert len(result["moved"]) == 1
        assert result["moved"][0]["to"] == "_NEEDS_REVIEW"
        assert result["moved"][0]["firm"] == ""

        # Verify cache was blanked
        cache = json.loads((output_dir / CLASSIFICATION_CACHE_FILE).read_text())
        att = cache["email_001"]["artifact_assignments"]["included_attachments"][0]
        assert att["assigned_firm_name"] == ""
        assert att["method"] == "manual_rejection"

        # Verify .review.json was created
        review_files = list(review_dir.glob("*.review.json"))
        assert len(review_files) == 1

    def test_move_out_of_needs_review(self, tmp_path):
        output_dir, artifact_file = self._setup_output_dir(tmp_path)

        # First move to _NEEDS_REVIEW
        review_dir = output_dir / NEEDS_REVIEW_FOLDER
        review_dir.mkdir()
        review_path = review_dir / artifact_file.name
        artifact_file.rename(review_path)
        sync_moved_artifacts(output_dir)  # Process the rejection

        # Now move out to FIRM_C
        firm_c_dir = output_dir / "FIRM_C"
        firm_c_dir.mkdir()
        final_path = firm_c_dir / review_path.name
        review_path.rename(final_path)

        result = sync_moved_artifacts(output_dir)
        assert len(result["moved"]) == 1
        assert result["moved"][0]["firm"] == "FIRM_C"
        assert result["moved"][0]["from"] == "_NEEDS_REVIEW"

        # Verify .review.json was cleaned up
        review_files = list(review_dir.glob("*.review.json"))
        assert len(review_files) == 0


# =============================================================================
# Tests for extract_links_with_filter_log()
# =============================================================================

class TestExtractLinksWithFilterLog:
    """Tests for extract_links_with_filter_log function."""

    def _make_email(self, body_html="", body_preview=""):
        return {
            "subject": "Test Subject",
            "bodyPreview": body_preview,
            "body": {"contentType": "html", "content": body_html},
        }

    def test_returns_candidates_and_filter_log(self):
        """Should return a tuple of (candidates, filter_log)."""
        metadata = self._make_email(
            body_html='<a href="https://example.com/report">Report</a>'
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert isinstance(candidates, list)
        assert isinstance(filter_log, list)

    def test_valid_link_becomes_candidate(self):
        """A valid non-filtered link should appear in candidates, not filter_log."""
        metadata = self._make_email(
            body_html='<a href="https://fundreport.com/data">Fund Report</a>'
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 1
        assert candidates[0]["url"] == "https://fundreport.com/data"
        assert len(filter_log) == 0

    def test_non_content_link_logged(self):
        """Unsubscribe/social links should appear in filter_log with reason."""
        metadata = self._make_email(
            body_html='<a href="https://example.com/unsubscribe">Unsubscribe</a>'
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 0
        assert len(filter_log) == 1
        assert filter_log[0]["reason"] == "non_content_link"

    def test_social_media_link_logged(self):
        """Social media links should be filtered as non_content_link."""
        metadata = self._make_email(
            body_html='<a href="https://linkedin.com/in/user">LinkedIn</a>'
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 0
        assert len(filter_log) == 1
        assert filter_log[0]["reason"] == "non_content_link"

    def test_homepage_link_logged(self):
        """A bare homepage without fund/investor context should be filtered."""
        metadata = self._make_email(
            body_html='<a href="https://example.com/">Example</a>',
            body_preview="Some generic text",
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 0
        assert len(filter_log) == 1
        assert filter_log[0]["reason"] == "homepage_filtered"

    def test_duplicate_link_logged(self):
        """Duplicate normalized URLs should produce one candidate and one filter entry."""
        metadata = self._make_email(
            body_html=(
                '<a href="https://example.com/report">Report</a>'
                '<a href="https://www.example.com/report">Report Again</a>'
            )
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 1
        assert len(filter_log) == 1
        assert filter_log[0]["reason"] == "duplicate"

    def test_invalid_url_logged(self):
        """A javascript: URL should be filtered as invalid_url."""
        metadata = self._make_email(
            body_html='<a href="javascript:void(0)">Click</a>'
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 0
        assert len(filter_log) == 1
        assert filter_log[0]["reason"] == "invalid_url"

    def test_gap_free_artifact_ids(self):
        """Artifact IDs should be sequential with no gaps."""
        metadata = self._make_email(
            body_html=(
                '<a href="https://linkedin.com/in/user">LinkedIn</a>'
                '<a href="https://fundreport.com/a">A</a>'
                '<a href="https://fundreport.com/b">B</a>'
            )
        )
        candidates, filter_log = extract_links_with_filter_log(metadata)
        assert len(candidates) == 2
        assert candidates[0]["artifact_id"] == "link:1"
        assert candidates[1]["artifact_id"] == "link:2"


# =============================================================================
# Tests for _finalize_artifact_classification reconciliation
# =============================================================================

class TestReconciliation:
    """Tests for the reconciliation pass in _finalize_artifact_classification."""

    def _make_candidates(self):
        return {
            "attachments": [],
            "links": [
                {
                    "artifact_id": "link:1",
                    "url": "https://fund-a.com/report",
                    "anchor_text": "Report A",
                    "description_context": "subject=Test | host=fund-a.com",
                    "source": "html_anchor",
                },
                {
                    "artifact_id": "link:2",
                    "url": "https://fund-b.com/factsheet",
                    "anchor_text": "Factsheet B",
                    "description_context": "subject=Test | host=fund-b.com",
                    "source": "html_anchor",
                },
            ],
        }

    def test_omitted_link_creates_skipped_entry(self):
        """When LLM omits a link, it should appear in skipped_links."""
        candidates = self._make_candidates()
        raw_result = {
            "email_classification": {
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "medium",
                "reasoning": "test",
            },
            "artifacts": [
                {
                    "artifact_id": "link:1",
                    "artifact_type": "link",
                    "is_hedge_fund_related": True,
                    "reason_code": "fund_document",
                    "assigned_firm_name": "FUND A",
                    "assigned_fund_name": "",
                    "confidence": 0.9,
                    "method": "link_context",
                    "evidence": "Fund report link",
                },
                # link:2 is omitted by LLM
            ],
        }
        result = _finalize_artifact_classification(raw_result, candidates)
        assignments = result["artifact_assignments"]

        assert len(assignments["included_links"]) == 1
        assert assignments["included_links"][0]["artifact_id"] == "link:1"

        # link:2 should be in skipped (condensed format)
        skipped = assignments["skipped_links"]
        assert len(skipped) == 1
        assert skipped[0]["artifact_id"] == "link:2"
        assert skipped[0]["method"] == "llm_omitted"

        assert assignments["summary"]["llm_omitted_count"] == 1

    def test_all_candidates_returned_no_omitted(self):
        """When LLM returns all candidates, llm_omitted_count should be 0."""
        candidates = self._make_candidates()
        raw_result = {
            "email_classification": {
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "medium",
                "reasoning": "test",
            },
            "artifacts": [
                {
                    "artifact_id": "link:1",
                    "artifact_type": "link",
                    "is_hedge_fund_related": True,
                    "reason_code": "fund_document",
                    "assigned_firm_name": "FUND A",
                    "assigned_fund_name": "",
                    "confidence": 0.9,
                    "method": "link_context",
                    "evidence": "Fund report link",
                },
                {
                    "artifact_id": "link:2",
                    "artifact_type": "link",
                    "is_hedge_fund_related": False,
                    "reason_code": "non_fund_marketing",
                    "assigned_firm_name": "",
                    "assigned_fund_name": "",
                    "confidence": 0.8,
                    "method": "link_context",
                    "evidence": "Marketing page",
                },
            ],
        }
        result = _finalize_artifact_classification(raw_result, candidates)
        assignments = result["artifact_assignments"]

        assert len(assignments["included_links"]) == 1
        assert len(assignments["skipped_links"]) == 1
        assert assignments["summary"]["llm_omitted_count"] == 0

    def test_omitted_attachment_creates_skipped_entry(self):
        """When LLM omits an attachment, it should appear in skipped_attachments."""
        candidates = {
            "attachments": [
                {
                    "artifact_id": "att:1",
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                    "description_context": "subject=Test | filename=report.pdf",
                },
            ],
            "links": [],
        }
        raw_result = {
            "email_classification": {
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "lowest",
                "reasoning": "test",
            },
            "artifacts": [],  # LLM returned nothing
        }
        result = _finalize_artifact_classification(raw_result, candidates)
        assignments = result["artifact_assignments"]

        skipped = assignments["skipped_attachments"]
        assert len(skipped) == 1
        assert skipped[0]["artifact_id"] == "att:1"
        assert skipped[0]["method"] == "llm_omitted"
        assert skipped[0]["filename"] == "report.pdf"
        assert assignments["summary"]["llm_omitted_count"] == 1

    def test_skipped_links_are_condensed(self):
        """Skipped links should be in condensed format (5 fields)."""
        candidates = self._make_candidates()
        raw_result = {
            "email_classification": {
                "email_type": "Other",
                "from_third_party": False,
                "source_priority": "medium",
                "reasoning": "test",
            },
            "artifacts": [
                {
                    "artifact_id": "link:1",
                    "artifact_type": "link",
                    "is_hedge_fund_related": False,
                    "reason_code": "non_fund_marketing",
                    "assigned_firm_name": "",
                    "assigned_fund_name": "",
                    "confidence": 0.7,
                    "method": "link_context",
                    "evidence": "Marketing page",
                },
                {
                    "artifact_id": "link:2",
                    "artifact_type": "link",
                    "is_hedge_fund_related": False,
                    "reason_code": "other",
                    "assigned_firm_name": "",
                    "assigned_fund_name": "",
                    "confidence": 0.5,
                    "method": "link_context",
                    "evidence": "Generic link",
                },
            ],
        }
        result = _finalize_artifact_classification(raw_result, candidates)
        skipped = result["artifact_assignments"]["skipped_links"]
        assert len(skipped) == 2
        for entry in skipped:
            assert set(entry.keys()) == {"artifact_id", "reason", "method", "evidence", "url"}


# =============================================================================
# Tests for _condense_skipped_artifact()
# =============================================================================

class TestCondenseSkippedArtifact:
    """Tests for _condense_skipped_artifact function."""

    def test_link_condensed_format(self):
        """A full link artifact should condense to 5 fields with url."""
        full = _blank_link_artifact()
        full.update({
            "artifact_id": "link:3",
            "url": "https://example.com/report",
            "reason_code": "non_fund_marketing",
            "method": "link_context",
            "evidence": "Not fund related",
        })
        condensed = _condense_skipped_artifact(full)
        assert condensed == {
            "artifact_id": "link:3",
            "reason": "non_fund_marketing",
            "method": "link_context",
            "evidence": "Not fund related",
            "url": "https://example.com/report",
        }

    def test_attachment_condensed_format(self):
        """A full attachment artifact should condense to 5 fields with filename."""
        full = _blank_attachment_artifact()
        full.update({
            "artifact_id": "att:1",
            "filename": "report.pdf",
            "reason_code": "other",
            "method": "llm_omitted",
            "evidence": "LLM did not return this artifact",
        })
        condensed = _condense_skipped_artifact(full)
        assert condensed == {
            "artifact_id": "att:1",
            "reason": "other",
            "method": "llm_omitted",
            "evidence": "LLM did not return this artifact",
            "filename": "report.pdf",
        }

    def test_does_not_include_extra_fields(self):
        """Condensed output should not contain fields like assigned_firm_name."""
        full = _blank_link_artifact()
        full.update({
            "artifact_id": "link:1",
            "assigned_firm_name": "ACME",
            "assigned_fund_name": "FUND X",
            "confidence": 0.9,
        })
        condensed = _condense_skipped_artifact(full)
        assert "assigned_firm_name" not in condensed
        assert "assigned_fund_name" not in condensed
        assert "confidence" not in condensed
