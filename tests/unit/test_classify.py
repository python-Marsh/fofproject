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
    generate_firm_id,
    generate_fund_id,
    add_fund_to_firm,
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
        mappings_file = output_dir / "firm_name_mappings.json"
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

        # Pre-populate cache with old-format classification (will be auto-migrated)
        cache = {
            "cached-email-123": {
                "is_hedge_fund_related": True,
                "confidence": 0.95,
                "is_third_party": False,
                "firm_name": "CACHED FIRM",
                "firm_name_source": "cache",
                "source_priority": "highest",
                "reasoning": "From cache",
                "email_type": "other",
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

        # Assert: Email was copied using cached firm name (flat structure)
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

class TestGenerateFirmId:
    """Tests for generate_firm_id function."""

    def test_basic_conversion(self):
        assert generate_firm_id("WMC CAPITAL") == "firm_wmc_capital"

    def test_strips_special_characters(self):
        assert generate_firm_id("Firm (HK) Ltd.") == "firm_firm_hk_ltd"

    def test_empty_input(self):
        assert generate_firm_id("") == "firm_unknown"


class TestGenerateFundId:
    """Tests for generate_fund_id function."""

    def test_basic_conversion(self):
        assert generate_fund_id("Albemarle Shipping Fund") == "fund_albemarle_shipping"

    def test_strips_fund_suffix(self):
        assert generate_fund_id("China Alpha Fund") == "fund_china_alpha"

    def test_strips_strategy_suffix(self):
        assert generate_fund_id("Global Macro Strategy") == "fund_global_macro"

    def test_empty_input(self):
        assert generate_fund_id("") == "fund_unknown"


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
