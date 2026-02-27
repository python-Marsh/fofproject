"""
Tests for PDF factsheet extraction (fofproject.load).

Each test case is a PDF in the extraction fixture folder paired with an
expected JSON output. The tests enforce:

  - STRICT match on ``performance`` (list of {date, value} dicts) and
    ``identifier`` (string derived from first 5 months).
  - SOFT match (flagged as warnings, never fails the test) on every other
    property so regressions in non-critical fields are visible but don't
    block CI.
"""
import json
import os
import warnings
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EXTRACTION_DIR = Path(
    os.environ.get(
        "EXTRACTION_TEST_DIR",
        Path(__file__).resolve().parents[1] / "fixtures" / "extraction",
    )
)
_EXPECTED_DIR = _EXTRACTION_DIR / "expected"

# Fields that MUST match exactly — test fails on mismatch
STRICT_FIELDS = {"performance", "identifier"}

# Build parametrize list: (pdf_path, expected_json_path, fund_label)
_IDENTIFIER = "_parsed from_"


def _discover_cases():
    """Walk the extraction folder and pair each PDF with its expected JSON."""
    cases = []
    if not _EXTRACTION_DIR.exists() or not _EXPECTED_DIR.exists():
        return cases

    for pdf_file in sorted(_EXTRACTION_DIR.glob("*.pdf")):
        fund_name = pdf_file.name.split(_IDENTIFIER)[0]
        expected_json = _EXPECTED_DIR / f"{fund_name}.json"
        if expected_json.exists():
            cases.append(
                pytest.param(
                    str(pdf_file),
                    str(expected_json),
                    fund_name,
                    id=fund_name,
                )
            )
    return cases


_CASES = _discover_cases()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_performance(perf):
    """Sort performance list by date for stable comparison."""
    if not isinstance(perf, list):
        return perf
    from datetime import datetime

    return sorted(perf, key=lambda x: datetime.strptime(x["date"], "%d/%m/%Y"))


def _compare_performance(actual, expected):
    """
    Compare two performance lists entry-by-entry.

    Returns (hard_errors, soft_warnings):
      - hard_errors: missing/extra dates or value mismatches > OCR_TOLERANCE
      - soft_warnings: value mismatches within OCR_TOLERANCE (likely minor OCR
        discrepancies from image-based PDF extraction)
    """
    # Tolerance for minor OCR errors: 5 basis points (0.05%).
    # Image-based PDF tables may have single-digit OCR confusion (e.g. 5 vs 9)
    # causing small value differences that are flagged but not hard failures.
    OCR_TOLERANCE = 0.0005

    actual = _normalise_performance(actual)
    expected = _normalise_performance(expected)

    hard_errors = []
    soft_warnings = []

    if len(actual) != len(expected):
        hard_errors.append(
            f"Length mismatch: got {len(actual)} entries, expected {len(expected)}"
        )

    # Build lookup for detailed diff
    expected_by_date = {e["date"]: e["value"] for e in expected}
    actual_by_date = {a["date"]: a["value"] for a in actual}

    all_dates = sorted(
        set(expected_by_date.keys()) | set(actual_by_date.keys()),
        key=lambda d: __import__("datetime").datetime.strptime(d, "%d/%m/%Y"),
    )

    for date in all_dates:
        exp_val = expected_by_date.get(date)
        act_val = actual_by_date.get(date)
        if exp_val is None:
            hard_errors.append(f"  {date}: unexpected (got {act_val})")
        elif act_val is None:
            hard_errors.append(f"  {date}: missing (expected {exp_val})")
        elif abs(act_val - exp_val) > OCR_TOLERANCE:
            hard_errors.append(
                f"  {date}: value mismatch — got {act_val}, expected {exp_val}"
            )
        elif abs(act_val - exp_val) > 1e-6:
            soft_warnings.append(
                f"  {date}: minor OCR discrepancy — got {act_val}, expected {exp_val} "
                f"(diff={abs(act_val - exp_val):.6f})"
            )

    return hard_errors, soft_warnings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _CASES, reason="No extraction test cases found")
class TestExtraction:
    """Run extraction on each PDF and compare against expected JSON."""

    @pytest.mark.parametrize("pdf_path,expected_path,fund_label", _CASES)
    def test_extraction(self, pdf_path, expected_path, fund_label):
        # Late import so collection doesn't fail if openai isn't installed
        from fofproject.loadcopy import gpt_process_pdf

        with open(expected_path, "r", encoding="utf-8") as f:
            expected = json.load(f)

        actual = gpt_process_pdf(pdf_path)

        # ---- STRICT: performance ----
        hard_errors, ocr_warnings = _compare_performance(
            actual.get("performance", []),
            expected.get("performance", []),
        )
        assert not hard_errors, (
            f"[{fund_label}] Performance mismatch:\n" + "\n".join(hard_errors)
        )

        # Flag minor OCR discrepancies as warnings (not failures)
        if ocr_warnings:
            msg = (
                f"[{fund_label}] Minor OCR discrepancies in performance "
                f"({len(ocr_warnings)}):\n" + "\n".join(ocr_warnings)
            )
            warnings.warn(msg, stacklevel=1)

        # ---- STRICT: identifier ----
        assert actual.get("identifier") == expected.get("identifier"), (
            f"[{fund_label}] Identifier mismatch: "
            f"got {actual.get('identifier')!r}, "
            f"expected {expected.get('identifier')!r}"
        )

        # ---- SOFT: flag all other fields ----
        soft_fields = set(expected.keys()) - STRICT_FIELDS
        mismatches = []
        for field in sorted(soft_fields):
            exp_val = expected.get(field)
            act_val = actual.get(field)
            if act_val != exp_val:
                mismatches.append(
                    f"  {field}: got {act_val!r}, expected {exp_val!r}"
                )

        if mismatches:
            msg = (
                f"[{fund_label}] Non-critical field differences "
                f"({len(mismatches)}):\n" + "\n".join(mismatches)
            )
            warnings.warn(msg, stacklevel=1)
