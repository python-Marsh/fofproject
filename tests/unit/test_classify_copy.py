import json
from types import SimpleNamespace
from unittest.mock import patch

import fofproject.classify as classify_copy


def _fake_client_with_payload(payload: dict):
    text = json.dumps(payload)
    response = SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(text=text)])])
    responses = SimpleNamespace(create=lambda **kwargs: response)
    return SimpleNamespace(responses=responses)


def _sample_metadata() -> dict:
    return {
        "id": "email-001",
        "subject": "LIFE Korea Engagement Fund Monthly Factsheet",
        "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
        "bodyPreview": "Please see attached factsheet and click webinar link.",
        "body": {
            "contentType": "html",
            "content": '<p>Review update</p><a href="https://fund.example.com/factsheet.pdf">Factsheet</a>'
                       '<a href="https://marketing.example.com/unsubscribe">unsubscribe</a>'
        },
        "attachments": [
            {
                "id": "att-1",
                "name": "LKEF_MonthlyNet_Sep25.pdf",
                "contentType": "application/pdf",
                "size": 1000,
                "isInline": False,
            }
        ],
        "hasAttachments": True,
    }


def test_filters_non_hedge_fund_artifacts():
    metadata = _sample_metadata()
    candidates = classify_copy.build_artifact_candidates(metadata)
    assert len(candidates["attachments"]) == 1
    assert len(candidates["links"]) == 1
    link_id = candidates["links"][0]["artifact_id"]

    payload = {
        "email_classification": {"email_type": "Factsheet / tear sheet", "reasoning": "artifact based"},
        "artifacts": [
            {
                "artifact_id": "att-1",
                "artifact_type": "attachment",
                "is_hedge_fund_related": True,
                "reason_code": "fund_document",
                "assigned_firm_name": "LIFE Asset Management",
                "assigned_fund_name": "LIFE Korea Engagement Fund",
                "confidence": 0.95,
                "method": "filename_match",
                "evidence": "filename and body match",
                "contains_monthly_net_performance_update": True,
            },
            {
                "artifact_id": link_id,
                "artifact_type": "link",
                "is_hedge_fund_related": False,
                "reason_code": "non_fund_marketing",
                "assigned_firm_name": "",
                "assigned_fund_name": "",
                "confidence": 0.90,
                "method": "link_context",
                "evidence": "marketing only",
                "link_type": "other",
                "description": "marketing page",
            },
        ],
    }

    result = classify_copy.classify_email_with_gpt(_fake_client_with_payload(payload), metadata, [], None)
    assignments = result["artifact_assignments"]
    assert assignments["summary"]["included_count"] == 1
    assert assignments["summary"]["skipped_count"] == 1
    assert len(assignments["included_attachments"]) == 1
    assert len(assignments["skipped_links"]) == 1
    assert result["fund_related_links"] == []


def test_strict_blank_on_uncertainty():
    metadata = _sample_metadata()
    payload = {
        "email_classification": {"email_type": "Factsheet / tear sheet", "reasoning": "unclear"},
        "artifacts": [
            {
                "artifact_id": "att-1",
                "artifact_type": "attachment",
                "is_hedge_fund_related": True,
                "reason_code": "fund_document",
                "assigned_firm_name": "Guess Firm",
                "assigned_fund_name": "Guess Fund",
                "confidence": 0.40,
                "method": "email_context",
                "evidence": "unclear manager",
            },
        ],
    }

    result = classify_copy.classify_email_with_gpt(_fake_client_with_payload(payload), metadata, [], None)
    att = result["artifact_assignments"]["included_attachments"][0]
    assert att["assigned_firm_name"] == ""
    assert att["assigned_firm_id"] == ""
    assert att["assigned_fund_name"] == ""
    assert att["assigned_fund_id"] == ""


def test_artifact_wins_email_gate(tmp_path):
    email_input_dir = tmp_path / "emails"
    output_dir = tmp_path / "output"
    email_input_dir.mkdir()
    output_dir.mkdir()

    folder = email_input_dir / "email_1"
    folder.mkdir()
    (folder / "metadata.json").write_text(json.dumps(_sample_metadata()), encoding="utf-8")

    forced = {
        "schema_version": "2.0",
        "email_classification": {
            "is_hedge_fund_related": False,
            "confidence": 0.2,
            "email_type": "Other",
            "from_third_party": False,
            "source_priority": "lowest",
            "reasoning": "base false",
        },
        "firm_name": "TEST CAPITAL",
        "firm_name_source": "attachment_or_link",
        "artifact_assignments": {
            "included_attachments": [],
            "included_links": [
                {
                    "artifact_id": "link:1",
                    "artifact_type": "link",
                    "url": "https://fund.example.com/factsheet.pdf",
                    "description": "factsheet",
                    "link_type": "factsheet",
                    "assigned_firm_name": "TEST CAPITAL",
                    "assigned_firm_id": "firm_test_capital",
                    "assigned_fund_name": "",
                    "assigned_fund_id": "",
                    "confidence": 0.9,
                    "method": "link_context",
                    "evidence": "explicit fund link",
                    "reason_code": "fund_document",
                }
            ],
            "skipped_attachments": [],
            "skipped_links": [],
            "summary": {"total_attachments": 0, "total_links": 1, "included_count": 1, "skipped_count": 0},
        },
        "attachments": [],
        "fund_related_links": [
            {
                "url": "https://fund.example.com/factsheet.pdf",
                "description": "factsheet",
                "link_type": "factsheet",
                "assigned_firm_id": "firm_test_capital",
                "assigned_fund_id": "",
            }
        ],
    }

    with patch.object(classify_copy, "get_openai_client", return_value=object()):
        with patch.object(classify_copy, "classify_email_with_gpt", return_value=forced):
            report = classify_copy.classify_and_organize_emails(
                email_input_dir=email_input_dir,
                output_dir=output_dir,
            )

    assert report["hedge_fund_related"] == 1
    assert (output_dir / "TEST CAPITAL").exists()


def test_cache_v2_additive_backward_compat():
    metadata = _sample_metadata()
    candidates = classify_copy.build_artifact_candidates(metadata)
    link_id = candidates["links"][0]["artifact_id"]

    payload = {
        "email_classification": {"email_type": "Factsheet / tear sheet", "reasoning": "explicit"},
        "artifacts": [
            {
                "artifact_id": "att-1",
                "artifact_type": "attachment",
                "is_hedge_fund_related": True,
                "reason_code": "fund_document",
                "assigned_firm_name": "LIFE Asset Management",
                "assigned_fund_name": "LIFE Korea Engagement Fund",
                "confidence": 0.91,
                "method": "filename_match",
                "evidence": "explicit in filename",
                "contains_monthly_net_performance_update": True,
            },
            {
                "artifact_id": link_id,
                "artifact_type": "link",
                "is_hedge_fund_related": True,
                "reason_code": "investor_update",
                "assigned_firm_name": "LIFE Asset Management",
                "assigned_fund_name": "",
                "confidence": 0.88,
                "method": "link_context",
                "evidence": "fund factsheet URL",
                "link_type": "factsheet",
                "description": "factsheet url",
            },
        ],
    }

    result = classify_copy.classify_email_with_gpt(_fake_client_with_payload(payload), metadata, [], None)
    assert result["schema_version"] == "2.0"
    assert "artifact_assignments" in result
    assert len(result["artifact_assignments"]["included_attachments"]) == 1
    assert len(result["artifact_assignments"]["included_links"]) == 1
    assert len(result["attachments"]) == 1
    assert len(result["fund_related_links"]) == 1


def test_only_hedge_fund_links_feed_downstream(tmp_path):
    output_dir = tmp_path / "output"
    download_dir = tmp_path / "downloads"
    output_dir.mkdir()
    download_dir.mkdir()

    cache = {
        "email-1": {
            "schema_version": "2.0",
            "email_classification": {
                "is_hedge_fund_related": True,
                "confidence": 0.95,
                "email_type": "Factsheet / tear sheet",
                "from_third_party": False,
                "source_priority": "highest",
                "reasoning": "artifact",
            },
            "firm_name": "TEST FIRM",
            "firm_name_source": "attachment_or_link",
            "artifact_assignments": {
                "included_attachments": [],
                "included_links": [
                    {"artifact_id": "link:1", "artifact_type": "link", "url": "https://fund.example.com/a.pdf"}
                ],
                "skipped_attachments": [],
                "skipped_links": [
                    {"artifact_id": "link:2", "artifact_type": "link", "url": "https://fund.example.com/b.pdf"}
                ],
                "summary": {"total_attachments": 0, "total_links": 2, "included_count": 1, "skipped_count": 1},
            },
            "attachments": [],
            "fund_related_links": [
                {
                    "url": "https://fund.example.com/a.pdf",
                    "description": "included",
                    "link_type": "factsheet",
                    "assigned_firm_id": "firm_test_firm",
                    "assigned_fund_id": "",
                }
            ],
        }
    }
    (output_dir / "classification_cache.json").write_text(json.dumps(cache), encoding="utf-8")

    pending = classify_copy.list_pending_links(output_dir=output_dir, download_dir=download_dir)
    urls = [item["url"] for item in pending]
    assert "https://fund.example.com/a.pdf" in urls
    assert "https://fund.example.com/b.pdf" not in urls
