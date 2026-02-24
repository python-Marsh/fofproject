"""
Shared pytest fixtures for fofproject tests.
"""
import json
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def empty_mappings():
    """Empty firm mappings for testing normalization without existing data."""
    return {
        "canonical_names": {},
        "email_overrides": {},
        "domain_overrides": {},
        "folder_reassignments": {},
    }


@pytest.fixture
def sample_mappings():
    """Sample mappings with firms, aliases, funds, and overrides for testing."""
    return {
        "canonical_names": {
            "SPRINGS CAPITAL": {
                "aliases": ["springs-capital", "Springs Capital HK", "Springs Capital (Hong Kong) Limited"],
                "description": "China-focused hedge fund",
                "funds": {
                    "fund_springs_china_alpha": {
                        "display_name": "Springs China Alpha Fund",
                        "aliases": ["China Alpha"],
                        "auto_added": "2026-02-24T00:00:00",
                    }
                },
            },
            "E20 CAPITAL": {
                "aliases": ["e20capital", "E20 Asset Management"],
                "description": "",
                "funds": {},
            },
            "ACME CORP": {
                "aliases": ["Acme Corporation", "ACME"],
                "description": "",
                "funds": {},
            },
        },
        "email_overrides": {
            "john@specific.com": "ACME CORP",
            "jane.doe@example.com": "SPRINGS CAPITAL",
        },
        "domain_overrides": {
            "springscap.com": "SPRINGS CAPITAL",
            "e20capital.com": "E20 CAPITAL",
        },
        "folder_reassignments": {
            "OLD FIRM": "NEW FIRM",
            "LEGACY CAPITAL": "SPRINGS CAPITAL",
        },
    }


@pytest.fixture
def sample_email_metadata():
    """Sample email metadata structure for testing classification."""
    return {
        "id": "AAMkAGFi123456789",
        "subject": "Monthly Performance Update - January 2025",
        "from": {
            "emailAddress": {
                "name": "John Smith",
                "address": "john.smith@springscap.com",
            }
        },
        "toRecipients": [
            {
                "emailAddress": {
                    "name": "Investor Relations",
                    "address": "ir@investor.com",
                }
            }
        ],
        "receivedDateTime": "2025-01-15T09:30:00Z",
        "bodyPreview": "Please find attached the monthly performance update for Springs Capital...",
        "hasAttachments": True,
    }


# =============================================================================
# Fixtures for Complex Function Tests (classify_and_organize_emails)
# =============================================================================


@pytest.fixture
def email_input_dir(tmp_path):
    """Create a temporary directory for email input folders."""
    email_dir = tmp_path / "emails"
    email_dir.mkdir()
    return email_dir


@pytest.fixture
def output_dir(tmp_path):
    """Create a temporary output directory for classified emails."""
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    return out_dir


@pytest.fixture
def mock_gpt_hedge_fund_response():
    """Mock GPT response for a hedge fund email (new nested format)."""
    return {
        "email_classification": {
            "is_hedge_fund_related": True,
            "confidence": 0.95,
            "email_type": "Monthly performance update",
            "from_third_party": False,
            "source_priority": "highest",
            "reasoning": "Identified from email signature",
        },
        "firm_name": "TEST CAPITAL",
        "firm_name_source": "email_content",
        "attachments": [],
        "fund_related_links": [],
    }


@pytest.fixture
def mock_gpt_third_party_response():
    """Mock GPT response for a third-party intermediary email (new nested format)."""
    return {
        "email_classification": {
            "is_hedge_fund_related": True,
            "confidence": 0.90,
            "email_type": "Cap intro",
            "from_third_party": "Goldman Sachs Client Services",
            "source_priority": "highest",
            "reasoning": "Cap intro from prime broker",
        },
        "firm_name": "GOLDMAN SACHS",
        "firm_name_source": "email_content",
        "attachments": [],
        "fund_related_links": [],
    }


@pytest.fixture
def mock_gpt_not_hedge_fund_response():
    """Mock GPT response for a non-hedge-fund email (new nested format)."""
    return {
        "email_classification": {
            "is_hedge_fund_related": False,
            "confidence": 0.85,
            "email_type": "Other",
            "from_third_party": False,
            "source_priority": "lowest",
            "reasoning": "Generic marketing email, not fund related",
        },
        "firm_name": "",
        "firm_name_source": "unknown",
        "attachments": [],
        "fund_related_links": [],
    }


def create_email_folder(parent_dir, folder_name: str, metadata: dict):
    """
    Helper function to create an email folder with metadata.json.

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


@pytest.fixture
def mock_openai_client():
    """
    Fixture that patches get_openai_client to return a mock.
    Use this when you want to control what classify_email_with_gpt returns.
    """
    with patch("fofproject.classify.get_openai_client") as mock_get_client:
        mock_client = Mock()
        mock_get_client.return_value = mock_client
        yield mock_client


@pytest.fixture
def patch_classify_email_with_gpt():
    """
    Fixture that patches classify_email_with_gpt directly.
    Easier to use than mocking the full OpenAI response structure.

    Usage:
        def test_something(patch_classify_email_with_gpt, mock_gpt_hedge_fund_response):
            patch_classify_email_with_gpt.return_value = mock_gpt_hedge_fund_response
            # ... run test
    """
    with patch("fofproject.classify.classify_email_with_gpt") as mock_classify:
        yield mock_classify
