"""
Extended tests for fofproject.classify — covers functions and edge cases
that the original test_classify.py does not exercise.

Areas covered:
1. ensure_classification_schema  — v1→v2 migration, empty input, idempotency
2. normalize_url                 — tracking param stripping, protocol-relative, edge cases
3. add_firm_to_mappings          — new firm, alias dedup, existing firm update
4. organize_artifacts_to_folders — attachment copy, link proxy, _NEEDS_REVIEW routing
5. Integration edge case: hedge-fund email with NO artifacts (no folder created)
6. sanitize_folder_name          — pipe character, combined special chars
"""
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from fofproject.classify import (
    add_firm_to_mappings,
    ensure_classification_schema,
    normalize_url,
    organize_artifacts_to_folders,
    classify_and_organize_emails,
    sanitize_folder_name,
)
from tests.conftest import create_email_folder


# =============================================================================
# Tests for ensure_classification_schema()
# =============================================================================


class TestEnsureClassificationSchema:
    """Tests for v1→v2 cache migration and schema enforcement."""

    def test_migrates_flat_v1_to_nested_v2(self):
        """Old flat cache entries (no 'email_classification' key) should be upgraded."""
        v1 = {
            "is_hedge_fund_related": True,
            "confidence": 0.90,
            "email_type": "Monthly performance update",
            "is_third_party": False,
            "source_priority": "highest",
            "reasoning": "Obvious fund email",
            "firm_name": "TEST FIRM",
            "firm_name_source": "email_content",
        }
        result = ensure_classification_schema(v1)
        assert result["schema_version"] == "2.0"
        assert result["email_classification"]["is_hedge_fund_related"] is True
        assert result["email_classification"]["confidence"] == 0.90
        assert result["email_classification"]["from_third_party"] is False
        assert result["firm_name"] == "TEST FIRM"
        assert "artifact_assignments" in result

    def test_empty_input_returns_default(self):
        """Empty or None input should return a safe default classification."""
        result = ensure_classification_schema({})
        assert result["schema_version"] == "2.0"
        assert result["email_classification"]["is_hedge_fund_related"] is False

    def test_idempotent_on_v2(self):
        """Calling on an already-v2 classification should not alter it."""
        v2 = {
            "schema_version": "2.0",
            "email_classification": {
                "is_hedge_fund_related": True,
                "confidence": 0.95,
                "email_type": "Factsheet",
                "from_third_party": False,
                "source_priority": "highest",
                "reasoning": "Already v2",
            },
            "firm_name": "EXISTING",
            "firm_name_source": "attachment_or_link",
            "artifact_assignments": {
                "included_attachments": [{"artifact_id": "att-1"}],
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
            "attachments": [],
            "fund_related_links": [],
        }
        result = ensure_classification_schema(v2)
        assert result["artifact_assignments"]["included_attachments"] == [{"artifact_id": "att-1"}]

    def test_v1_with_attachments_migrates_to_included(self):
        """V1 entries that have 'attachments' should migrate to included_attachments."""
        v1 = {
            "is_hedge_fund_related": True,
            "confidence": 0.8,
            "firm_name": "MIGRATED FIRM",
            "firm_name_source": "email",
            "attachments": [
                {
                    "attachment_id": "att-1",
                    "filename": "report.pdf",
                    "mime_type": "application/pdf",
                    "assigned_firm_id": "firm_migrated_firm",
                    "assigned_fund_id": "",
                }
            ],
            "fund_related_links": [],
        }
        result = ensure_classification_schema(v1)
        assignments = result["artifact_assignments"]
        assert len(assignments["included_attachments"]) == 1
        assert assignments["included_attachments"][0]["filename"] == "report.pdf"
        assert assignments["summary"]["included_count"] == 1

    def test_v1_with_links_migrates_to_included(self):
        """V1 entries with 'fund_related_links' should migrate to included_links."""
        v1 = {
            "is_hedge_fund_related": True,
            "confidence": 0.85,
            "firm_name": "LINK FIRM",
            "firm_name_source": "gpt",
            "attachments": [],
            "fund_related_links": [
                {
                    "url": "https://example.com/factsheet.pdf",
                    "description": "factsheet",
                    "link_type": "factsheet",
                    "assigned_firm_id": "firm_link_firm",
                    "assigned_fund_id": "",
                }
            ],
        }
        result = ensure_classification_schema(v1)
        assignments = result["artifact_assignments"]
        assert len(assignments["included_links"]) == 1
        assert assignments["included_links"][0]["url"] == "https://example.com/factsheet.pdf"
        assert assignments["summary"]["included_count"] == 1


# =============================================================================
# Tests for normalize_url()
# =============================================================================


class TestNormalizeUrl:
    """Tests for URL normalization used in link deduplication."""

    def test_strips_tracking_params(self):
        url = "https://example.com/report?page=1&utm_source=email&utm_medium=cpc"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "page=1" in result

    def test_removes_www_prefix(self):
        assert "www." not in normalize_url("https://www.example.com/path")

    def test_protocol_relative_url(self):
        result = normalize_url("//cdn.example.com/file.pdf")
        assert result.startswith("https://")
        assert "cdn.example.com" in result

    def test_removes_trailing_slash(self):
        result = normalize_url("https://example.com/path/")
        assert result == "https://example.com/path"

    def test_empty_input(self):
        assert normalize_url("") == ""
        assert normalize_url("   ") == ""

    def test_non_http_scheme_filtered(self):
        assert normalize_url("mailto:test@example.com") == ""
        assert normalize_url("javascript:void(0)") == ""

    def test_preserves_non_tracking_query_params(self):
        url = "https://example.com/doc?id=123&token=abc"
        result = normalize_url(url)
        assert "id=123" in result
        assert "token=abc" in result

    def test_removes_fragment(self):
        result = normalize_url("https://example.com/page#section")
        assert "#section" not in result


# =============================================================================
# Tests for add_firm_to_mappings()
# =============================================================================


class TestAddFirmToMappings:
    """Tests for adding/updating firms in the canonical mappings."""

    def test_adds_new_firm(self, empty_mappings):
        canonical = add_firm_to_mappings("New Alpha Capital", ["NAC"], empty_mappings)
        assert canonical == "NEW ALPHA CAPITAL"
        assert "NEW ALPHA CAPITAL" in empty_mappings["canonical_names"]
        assert "NAC" in empty_mappings["canonical_names"]["NEW ALPHA CAPITAL"]["aliases"]

    def test_updates_existing_firm_aliases(self, sample_mappings):
        add_firm_to_mappings("SPRINGS CAPITAL", ["new-alias"], sample_mappings)
        aliases = sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["aliases"]
        assert "new-alias" in aliases

    def test_deduplicates_aliases(self, sample_mappings):
        existing_count = len(sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["aliases"])
        # Add alias that already exists (case-insensitive)
        add_firm_to_mappings("SPRINGS CAPITAL", ["springs-capital"], sample_mappings)
        new_count = len(sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["aliases"])
        assert new_count == existing_count

    def test_resolves_alias_then_adds(self, sample_mappings):
        """Adding via alias should resolve to canonical name and add to correct firm."""
        canonical = add_firm_to_mappings("springs-capital", ["yet-another-alias"], sample_mappings)
        assert canonical == "SPRINGS CAPITAL"
        aliases = sample_mappings["canonical_names"]["SPRINGS CAPITAL"]["aliases"]
        assert "yet-another-alias" in aliases


# =============================================================================
# Tests for organize_artifacts_to_folders()
# =============================================================================


class TestOrganizeArtifactsToFolders:
    """Tests for the artifact-to-folder routing function."""

    def test_link_creates_proxy_file_in_firm_folder(self, tmp_path):
        """An included link should create a .link.json proxy in the firm folder."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        email_folder = tmp_path / "email_1"
        email_folder.mkdir()

        classification = {
            "artifact_assignments": {
                "included_attachments": [],
                "included_links": [
                    {
                        "artifact_id": "link:0",
                        "artifact_type": "link",
                        "url": "https://fund.example.com/factsheet.pdf",
                        "description": "factsheet",
                        "link_type": "factsheet",
                        "assigned_firm_name": "ALPHA FUND",
                        "assigned_firm_id": "firm_alpha_fund",
                        "assigned_fund_name": "",
                        "assigned_fund_id": "",
                    }
                ],
            },
        }
        metadata = {"receivedDateTime": "2025-06-15T10:00:00Z"}
        mappings = {
            "canonical_names": {},
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }

        result = organize_artifacts_to_folders(
            classification=classification,
            email_folder=email_folder,
            email_metadata=metadata,
            output_dir=output_dir,
            firm_mappings=mappings,
        )
        assert result["organized_count"] == 1
        assert result["needs_review_count"] == 0
        firm_dir = output_dir / "ALPHA FUND"
        assert firm_dir.exists()

    def test_attachment_copied_to_firm_folder(self, tmp_path):
        """An included attachment with a matching file should be copied."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        email_folder = tmp_path / "email_1"
        att_dir = email_folder / "attachments"
        att_dir.mkdir(parents=True)
        (att_dir / "report.pdf").write_bytes(b"%PDF-fake")

        classification = {
            "artifact_assignments": {
                "included_attachments": [
                    {
                        "artifact_id": "att-1",
                        "artifact_type": "attachment",
                        "filename": "report.pdf",
                        "assigned_firm_name": "BETA CAPITAL",
                        "assigned_firm_id": "firm_beta_capital",
                        "assigned_fund_name": "",
                        "assigned_fund_id": "",
                    }
                ],
                "included_links": [],
            },
        }
        metadata = {"receivedDateTime": "2025-03-10T08:00:00Z"}
        mappings = {
            "canonical_names": {},
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }

        result = organize_artifacts_to_folders(
            classification=classification,
            email_folder=email_folder,
            email_metadata=metadata,
            output_dir=output_dir,
            firm_mappings=mappings,
        )
        assert result["organized_count"] == 1
        firm_dir = output_dir / "BETA CAPITAL"
        assert firm_dir.exists()
        copied_files = list(firm_dir.glob("*.pdf"))
        assert len(copied_files) == 1

    def test_empty_artifacts_creates_nothing(self, tmp_path):
        """No artifacts means no folders created, organized_count=0."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        email_folder = tmp_path / "email_1"
        email_folder.mkdir()

        classification = {
            "artifact_assignments": {
                "included_attachments": [],
                "included_links": [],
            },
        }
        metadata = {"receivedDateTime": "2025-01-01T00:00:00Z"}
        mappings = {
            "canonical_names": {},
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }

        result = organize_artifacts_to_folders(
            classification=classification,
            email_folder=email_folder,
            email_metadata=metadata,
            output_dir=output_dir,
            firm_mappings=mappings,
        )
        assert result["organized_count"] == 0
        assert result["needs_review_count"] == 0
        # No firm folders should exist
        dirs = [f for f in output_dir.iterdir() if f.is_dir()]
        assert len(dirs) == 0

    def test_artifact_without_firm_routed_to_needs_review(self, tmp_path):
        """Artifact with empty firm_name should go to _NEEDS_REVIEW folder."""
        output_dir = tmp_path / "output"
        output_dir.mkdir()
        email_folder = tmp_path / "email_1"
        email_folder.mkdir()

        classification = {
            "artifact_assignments": {
                "included_attachments": [],
                "included_links": [
                    {
                        "artifact_id": "link:0",
                        "artifact_type": "link",
                        "url": "https://unknown.com/data.pdf",
                        "description": "unknown source",
                        "link_type": "other",
                        "assigned_firm_name": "",
                        "assigned_firm_id": "",
                        "assigned_fund_name": "",
                        "assigned_fund_id": "",
                    }
                ],
            },
        }
        metadata = {"receivedDateTime": "2025-04-01T12:00:00Z"}
        mappings = {
            "canonical_names": {},
            "email_overrides": {},
            "domain_overrides": {},
            "folder_reassignments": {},
        }

        result = organize_artifacts_to_folders(
            classification=classification,
            email_folder=email_folder,
            email_metadata=metadata,
            output_dir=output_dir,
            firm_mappings=mappings,
        )
        assert result["needs_review_count"] == 1
        assert result["organized_count"] == 0
        assert (output_dir / "_NEEDS_REVIEW").exists()


# =============================================================================
# Integration: hedge-fund classification with NO artifacts
# =============================================================================


class TestHedgeFundNoArtifacts:
    """Edge case: email classified as hedge-fund but with no organizable artifacts."""

    def test_no_folder_created_when_no_artifacts(self, tmp_path):
        """
        When GPT says is_hedge_fund_related=True but returns no artifacts,
        no firm folder should be created (this was a regression in the old tests).
        """
        email_input_dir = tmp_path / "emails"
        output_dir = tmp_path / "output"
        email_input_dir.mkdir()
        output_dir.mkdir()

        metadata = {
            "id": "no-artifact-001",
            "subject": "Fund Update",
            "from": {"emailAddress": {"name": "PM", "address": "pm@nofirm.com"}},
        }
        create_email_folder(email_input_dir, "email_no_artifacts", metadata)

        mock_response = {
            "schema_version": "2.0",
            "email_classification": {
                "is_hedge_fund_related": True,
                "confidence": 0.88,
                "email_type": "Monthly performance update",
                "from_third_party": False,
                "source_priority": "highest",
                "reasoning": "Fund update but no attachments or links",
            },
            "firm_name": "GHOST CAPITAL",
            "firm_name_source": "email_content",
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
            "attachments": [],
            "fund_related_links": [],
        }

        with patch("fofproject.classify.get_openai_client"):
            with patch("fofproject.classify.classify_email_with_gpt", return_value=mock_response):
                report = classify_and_organize_emails(
                    email_input_dir=email_input_dir,
                    output_dir=output_dir,
                )

        # Firm identified in report, but no folder created
        assert "GHOST CAPITAL" in report["firms_found"]
        firm_folder = output_dir / "GHOST CAPITAL"
        assert not firm_folder.exists()


# =============================================================================
# Extra edge cases for sanitize_folder_name()
# =============================================================================


class TestSanitizeFolderNameExtended:
    """Additional edge cases not covered in the original test file."""

    def test_removes_pipe_character(self):
        assert sanitize_folder_name("Firm|Name") == "FirmName"

    def test_removes_double_quotes(self):
        assert sanitize_folder_name('"Firm Name"') == "Firm Name"

    def test_combined_special_characters(self):
        result = sanitize_folder_name('Firm<>|:"Name*?')
        assert result == "FirmName"

    def test_only_special_characters(self):
        assert sanitize_folder_name('<>:"|?*') == "UNKNOWN"
