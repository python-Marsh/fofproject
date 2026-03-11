"""
Tests for sync_moved_artifacts registry-based monitoring.
No OpenAI or external APIs required.
"""

import json
import shutil
from pathlib import Path

import pytest

from fofproject.classify import (
    sync_moved_artifacts,
    _embed_artifact_id_in_filename,
    _parse_artifact_id_from_filename,
    _strip_artifact_id_from_filename,
    SYSTEM_FILES,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def output_dir(tmp_path):
    """Provide a temporary output directory."""
    return tmp_path


def _write_mappings(output_dir: Path, mappings: dict):
    with open(output_dir / "firm_fund_mappings.json", "w", encoding="utf-8") as f:
        json.dump(mappings, f, indent=2)


def _load_mappings(output_dir: Path) -> dict:
    with open(output_dir / "firm_fund_mappings.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _make_tagged_file(folder: Path, original_name: str, artifact_id: str, size: int = 100) -> Path:
    """Create a file with artifact_id embedded in the filename."""
    tagged_name = _embed_artifact_id_in_filename(original_name, artifact_id)
    path = folder / tagged_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _make_default_state(output_dir: Path):
    """Create default state: one firm with one fund, each with one artifact."""
    firm_dir = output_dir / "FIRM_A"
    fund_dir = firm_dir / "FUND_X"
    fund_dir.mkdir(parents=True, exist_ok=True)

    _make_tagged_file(firm_dir, "report.pdf", "art001")
    _make_tagged_file(fund_dir, "factsheet.pdf", "art002", size=200)

    _write_mappings(output_dir, {
        "canonical_names": {
            "FIRM_A": {
                "aliases": ["FIRM_A"],
                "identifier": None,
                "funds": {
                    "FUND_X": {
                        "aliases": [],
                        "artifacts": {
                            "art002": {
                                "file_name": "factsheet.pdf",
                                "identifier": None,
                                "contains_monthly_net_performance_update": True,
                                "processed": False,
                            }
                        },
                    }
                },
                "artifacts": {
                    "art001": {
                        "file_name": "report.pdf",
                        "identifier": None,
                        "contains_monthly_net_performance_update": False,
                        "processed": False,
                    }
                },
            }
        },
        "_metadata": {"last_updated": "2025-01-01"},
    })


@pytest.fixture
def default_state(output_dir):
    """Create default state and return output_dir."""
    _make_default_state(output_dir)
    return output_dir


# ---------------------------------------------------------------------------
# Helper tests: filename embedding / parsing
# ---------------------------------------------------------------------------

class TestFilenameEmbedding:
    def test_embed_simple(self):
        assert _embed_artifact_id_in_filename("report.pdf", "abc123") == "report - abc123.pdf"

    def test_embed_link_json(self):
        result = _embed_artifact_id_in_filename("slug.link.json", "abc123")
        assert result == "slug - abc123.link.json"

    def test_embed_no_id(self):
        assert _embed_artifact_id_in_filename("report.pdf", "") == "report.pdf"

    def test_parse_simple(self):
        assert _parse_artifact_id_from_filename("report - abc123.pdf") == "abc123"

    def test_parse_link_json(self):
        assert _parse_artifact_id_from_filename("slug - abc123.link.json") == "abc123"

    def test_parse_no_id(self):
        assert _parse_artifact_id_from_filename("report.pdf") == ""

    def test_roundtrip(self):
        original = "my_factsheet.pdf"
        art_id = "xyz789"
        embedded = _embed_artifact_id_in_filename(original, art_id)
        parsed = _parse_artifact_id_from_filename(embedded)
        assert parsed == art_id


# ---------------------------------------------------------------------------
# Test 1: No changes — should be a no-op
# ---------------------------------------------------------------------------

class TestNoChanges:
    def test_no_changes(self, default_state):
        od = default_state
        result = sync_moved_artifacts(output_dir=od)

        assert result["moved"] == []
        assert result["new_folders"] == []
        assert result["removed_folders"] == []
        assert result["new_artifacts"] == []
        assert result["deleted_artifacts"] == []


# ---------------------------------------------------------------------------
# Test 2: Move artifact to different firm
# ---------------------------------------------------------------------------

class TestMoveArtifact:
    def test_move_to_different_firm(self, default_state):
        od = default_state
        src = od / "FIRM_A" / "report - art001.pdf"
        dst_dir = od / "FIRM_B"
        dst_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst_dir / "report - art001.pdf"))

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["moved"]) == 1
        m = result["moved"][0]
        assert m["artifact_id"] == "art001"
        assert m["firm"] == "FIRM_B"
        assert "FIRM_A" in m["from"]

    def test_move_to_fund_subfolder(self, default_state):
        od = default_state
        src = od / "FIRM_A" / "report - art001.pdf"
        dst = od / "FIRM_A" / "FUND_X" / "report - art001.pdf"
        shutil.move(str(src), str(dst))

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["moved"]) == 1
        m = result["moved"][0]
        assert m["artifact_id"] == "art001"
        assert m["firm"] == "FIRM_A"
        assert m["fund"] == "FUND_X"


# ---------------------------------------------------------------------------
# Test 3: Artifact deleted from disk
# ---------------------------------------------------------------------------

class TestDeletion:
    def test_artifact_deleted(self, default_state):
        od = default_state
        (od / "FIRM_A" / "report - art001.pdf").unlink()

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["deleted_artifacts"]) == 1
        da = result["deleted_artifacts"][0]
        assert da["artifact_id"] == "art001"
        assert da["firm"] == "FIRM_A"

        # Verify soft-delete in mappings
        mappings = _load_mappings(od)
        art = mappings["canonical_names"]["FIRM_A"]["artifacts"]["art001"]
        assert "_deleted_at" in art

    def test_fund_folder_deleted(self, default_state):
        od = default_state
        shutil.rmtree(str(od / "FIRM_A" / "FUND_X"))

        result = sync_moved_artifacts(output_dir=od)

        assert any(
            rf["type"] == "fund" and rf["folder"] == "FUND_X"
            for rf in result["removed_folders"]
        )
        assert any(
            da["artifact_id"] == "art002"
            for da in result["deleted_artifacts"]
        )

        mappings = _load_mappings(od)
        fund = mappings["canonical_names"]["FIRM_A"]["funds"]["FUND_X"]
        assert "_deleted_at" in fund


# ---------------------------------------------------------------------------
# Test 4: New untagged file discovered — gets tagged with random artifact_id
# ---------------------------------------------------------------------------

class TestNewUntaggedFile:
    def test_new_file_gets_tagged(self, default_state):
        od = default_state
        new_file = od / "FIRM_A" / "new_report.pdf"
        new_file.write_bytes(b"new content")

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["new_artifacts"]) == 1
        na = result["new_artifacts"][0]
        assert na["firm"] == "FIRM_A"
        assert na["fund"] is None

        # Original file should be renamed with artifact_id
        assert not new_file.exists()
        # New tagged file should exist
        tagged_files = [
            f for f in (od / "FIRM_A").iterdir()
            if f.is_file() and "new_report" in f.name and f.name != "report - art001.pdf"
        ]
        assert len(tagged_files) == 1
        assert _parse_artifact_id_from_filename(tagged_files[0].name) != ""

    def test_new_file_in_fund(self, default_state):
        od = default_state
        new_file = od / "FIRM_A" / "FUND_X" / "new_factsheet.pdf"
        new_file.write_bytes(b"new content")

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["new_artifacts"]) == 1
        na = result["new_artifacts"][0]
        assert na["firm"] == "FIRM_A"
        assert na["fund"] == "FUND_X"


# ---------------------------------------------------------------------------
# Test 5: New firm folder on disk
# ---------------------------------------------------------------------------

class TestNewFirmFolder:
    def test_new_firm_registered(self, default_state):
        od = default_state
        new_firm = od / "FIRM_B"
        new_firm.mkdir()

        result = sync_moved_artifacts(output_dir=od)

        assert any(
            nf["firm"] == "FIRM_B" and nf["type"] == "firm"
            for nf in result["new_folders"]
        )

        mappings = _load_mappings(od)
        assert "FIRM_B" in mappings["canonical_names"]


# ---------------------------------------------------------------------------
# Test 6: New fund folder under existing firm
# ---------------------------------------------------------------------------

class TestNewFundFolder:
    def test_new_fund_registered(self, default_state):
        od = default_state
        new_fund = od / "FIRM_A" / "FUND_Y"
        new_fund.mkdir()

        result = sync_moved_artifacts(output_dir=od)

        assert any(
            nf["type"] == "fund" and "FUND_Y" in nf["folder"]
            for nf in result["new_folders"]
        )

        mappings = _load_mappings(od)
        assert "FUND_Y" in mappings["canonical_names"]["FIRM_A"]["funds"]


# ---------------------------------------------------------------------------
# Test 7: Link proxy file (compound extension)
# ---------------------------------------------------------------------------

class TestLinkProxy:
    def test_link_proxy_tracked(self, output_dir):
        od = output_dir
        firm_dir = od / "FIRM_A"
        firm_dir.mkdir(parents=True)

        link_data = {"url": "https://example.com/report", "proxy_version": "1.0"}
        link_filename = _embed_artifact_id_in_filename("link_report.link.json", "link001")
        with open(firm_dir / link_filename, "w") as f:
            json.dump(link_data, f)

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {},
                    "artifacts": {
                        "link001": {
                            "file_name": "link_report.link.json",
                            "identifier": None,
                            "contains_monthly_net_performance_update": False,
                            "processed": False,
                        }
                    },
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        result = sync_moved_artifacts(output_dir=od)

        # No changes expected
        assert result["moved"] == []
        assert result["deleted_artifacts"] == []

    def test_link_proxy_moved(self, output_dir):
        od = output_dir
        firm_a = od / "FIRM_A"
        firm_b = od / "FIRM_B"
        firm_a.mkdir(parents=True)
        firm_b.mkdir(parents=True)

        link_filename = _embed_artifact_id_in_filename("link_report.link.json", "link001")
        link_data = {"url": "https://example.com/report"}
        with open(firm_a / link_filename, "w") as f:
            json.dump(link_data, f)

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {},
                    "artifacts": {
                        "link001": {
                            "file_name": "link_report.link.json",
                            "identifier": None,
                            "contains_monthly_net_performance_update": False,
                            "processed": False,
                        }
                    },
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        # Move to FIRM_B
        shutil.move(str(firm_a / link_filename), str(firm_b / link_filename))

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["moved"]) == 1
        m = result["moved"][0]
        assert m["artifact_id"] == "link001"
        assert m["firm"] == "FIRM_B"


# ---------------------------------------------------------------------------
# Test 8: Mixed operations
# ---------------------------------------------------------------------------

class TestMixedOperations:
    def test_move_delete_and_new(self, default_state):
        od = default_state

        # 1. Move art001 to new firm
        firm_b = od / "FIRM_B"
        firm_b.mkdir()
        shutil.move(
            str(od / "FIRM_A" / "report - art001.pdf"),
            str(firm_b / "report - art001.pdf"),
        )

        # 2. Delete art002
        (od / "FIRM_A" / "FUND_X" / "factsheet - art002.pdf").unlink()

        # 3. Add new untagged file
        (firm_b / "new_doc.pdf").write_bytes(b"new")

        result = sync_moved_artifacts(output_dir=od)

        # art001 moved
        moved = [m for m in result["moved"] if m["artifact_id"] == "art001"]
        assert len(moved) == 1
        assert moved[0]["firm"] == "FIRM_B"

        # art002 soft-deleted
        deleted = [d for d in result["deleted_artifacts"] if d["artifact_id"] == "art002"]
        assert len(deleted) == 1

        # new file tagged
        new = [n for n in result["new_artifacts"] if "new_doc" in n.get("file_name", "")]
        assert len(new) == 1
        assert new[0]["firm"] == "FIRM_B"


# ---------------------------------------------------------------------------
# Test 9: Fund folder identifier-based matching
# ---------------------------------------------------------------------------

class TestFundIdentifier:
    """Test that fund folders with identifiers (e.g. 'FundName - 1234567890')
    use the identifier as the primary matching key, with name fallback."""

    def test_new_fund_with_identifier_stores_it(self, default_state):
        """When a new fund folder has an identifier, it gets stored in mappings."""
        od = default_state
        fund_dir = od / "FIRM_A" / "FUND_Z - 9876543210"
        fund_dir.mkdir(parents=True)

        result = sync_moved_artifacts(output_dir=od)

        assert any(
            nf["type"] == "fund" and "FUND_Z" in nf["folder"]
            for nf in result["new_folders"]
        )

        mappings = _load_mappings(od)
        fund = mappings["canonical_names"]["FIRM_A"]["funds"]["FUND_Z"]
        assert fund["identifier"] == "9876543210"

    def test_fund_rename_matched_by_identifier(self, output_dir):
        """Renaming a fund folder (changing name but keeping identifier)
        should match by identifier, not create a new fund."""
        od = output_dir

        # Set up initial state with a fund that has an identifier
        fund_dir = od / "FIRM_A" / "OldFund - 1111111111"
        fund_dir.mkdir(parents=True)
        _make_tagged_file(fund_dir, "factsheet.pdf", "art100")

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {
                        "OldFund": {
                            "aliases": [],
                            "identifier": "1111111111",
                            "artifacts": {
                                "art100": {
                                    "file_name": "factsheet.pdf",
                                    "identifier": None,
                                    "contains_monthly_net_performance_update": False,
                                    "processed": True,
                                }
                            },
                        }
                    },
                    "artifacts": {},
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        # Simulate rename: change the fund name but keep the identifier
        renamed_dir = od / "FIRM_A" / "NewFund - 1111111111"
        fund_dir.rename(renamed_dir)

        result = sync_moved_artifacts(output_dir=od)

        # Should NOT create a new fund folder — matched by identifier
        assert not any(
            nf["type"] == "fund" for nf in result.get("new_folders", [])
        )

        # The fund should now be keyed under 'NewFund' with 'OldFund' as alias
        mappings = _load_mappings(od)
        funds = mappings["canonical_names"]["FIRM_A"]["funds"]
        assert "NewFund" in funds
        assert "OldFund" not in funds or "OldFund" in funds.get("NewFund", {}).get("aliases", [])

        # The artifact should still be tracked
        assert "art100" in funds["NewFund"]["artifacts"]
        # Identifier should be preserved
        assert funds["NewFund"]["identifier"] == "1111111111"

    def test_fund_no_identifier_falls_back_to_name(self, default_state):
        """Without an identifier, fund matching uses name-based lookup (existing behavior)."""
        od = default_state

        # FUND_X already exists in default_state without an identifier
        result = sync_moved_artifacts(output_dir=od)

        # Should be a no-op
        assert result["moved"] == []
        assert result["new_folders"] == []

    def test_existing_fund_gets_identifier_from_folder_rename(self, output_dir):
        """An existing fund without identifier gets one when the folder is renamed
        to include an identifier suffix."""
        od = output_dir

        # Start with a fund with no identifier
        fund_dir = od / "FIRM_A" / "MyFund"
        fund_dir.mkdir(parents=True)
        _make_tagged_file(fund_dir, "doc.pdf", "art200")

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {
                        "MyFund": {
                            "aliases": [],
                            "artifacts": {
                                "art200": {
                                    "file_name": "doc.pdf",
                                    "identifier": None,
                                    "contains_monthly_net_performance_update": False,
                                    "processed": False,
                                }
                            },
                        }
                    },
                    "artifacts": {},
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        # Rename folder to add identifier
        renamed_dir = od / "FIRM_A" / "MyFund - 5555555555"
        fund_dir.rename(renamed_dir)

        result = sync_moved_artifacts(output_dir=od)

        # Should match by name (fallback) since no identifier existed before
        assert not any(nf["type"] == "fund" for nf in result.get("new_folders", []))

        mappings = _load_mappings(od)
        fund = mappings["canonical_names"]["FIRM_A"]["funds"]["MyFund"]
        assert fund["identifier"] == "5555555555"

    def test_identifier_match_across_complete_name_change(self, output_dir):
        """Even if the fund name is completely different, identifier match works."""
        od = output_dir

        fund_dir = od / "FIRM_A" / "CompletelyDifferentName - 7777777777"
        fund_dir.mkdir(parents=True)
        _make_tagged_file(fund_dir, "report.pdf", "art300")

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {
                        "OriginalName": {
                            "aliases": [],
                            "identifier": "7777777777",
                            "artifacts": {
                                "art300": {
                                    "file_name": "report.pdf",
                                    "identifier": None,
                                    "contains_monthly_net_performance_update": False,
                                    "processed": False,
                                }
                            },
                        }
                    },
                    "artifacts": {},
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        result = sync_moved_artifacts(output_dir=od)

        # Matched by identifier — no new fund
        assert not any(nf["type"] == "fund" for nf in result.get("new_folders", []))

        mappings = _load_mappings(od)
        funds = mappings["canonical_names"]["FIRM_A"]["funds"]
        # Re-keyed under new name
        assert "CompletelyDifferentName" in funds
        assert funds["CompletelyDifferentName"]["identifier"] == "7777777777"
        assert "art300" in funds["CompletelyDifferentName"]["artifacts"]


# ---------------------------------------------------------------------------
# Test 10: Artifact file_name stripping and filename-based fallback
# ---------------------------------------------------------------------------

class TestArtifactFileNameStripping:
    """Test that file_name in mappings stores the base name without artifact_id,
    and that filename-based fallback works when artifact_id is lost."""

    def test_strip_simple(self):
        assert _strip_artifact_id_from_filename("report - abc123.pdf") == "report.pdf"

    def test_strip_link_json(self):
        assert _strip_artifact_id_from_filename("slug - abc123.link.json") == "slug.link.json"

    def test_strip_no_id(self):
        assert _strip_artifact_id_from_filename("report.pdf") == "report.pdf"

    def test_strip_roundtrip(self):
        original = "my_factsheet.pdf"
        art_id = "xyz789"
        embedded = _embed_artifact_id_in_filename(original, art_id)
        stripped = _strip_artifact_id_from_filename(embedded)
        assert stripped == original

    def test_mapping_stores_stripped_filename(self, default_state):
        """After sync, file_name in mappings should not contain the artifact_id."""
        od = default_state
        result = sync_moved_artifacts(output_dir=od)

        mappings = _load_mappings(od)
        firm = mappings["canonical_names"]["FIRM_A"]
        # Firm-level artifact
        assert firm["artifacts"]["art001"]["file_name"] == "report.pdf"
        # Fund-level artifact
        assert firm["funds"]["FUND_X"]["artifacts"]["art002"]["file_name"] == "factsheet.pdf"

    def test_new_tagged_artifact_stores_stripped_name(self, output_dir):
        """A newly discovered tagged file should have its file_name stored without artifact_id."""
        od = output_dir
        firm_dir = od / "FIRM_A"
        firm_dir.mkdir(parents=True)
        _make_tagged_file(firm_dir, "newsletter.pdf", "art500")

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {},
                    "artifacts": {},
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        result = sync_moved_artifacts(output_dir=od)

        assert len(result["new_artifacts"]) == 1
        assert result["new_artifacts"][0]["file_name"] == "newsletter.pdf"

        mappings = _load_mappings(od)
        assert mappings["canonical_names"]["FIRM_A"]["artifacts"]["art500"]["file_name"] == "newsletter.pdf"

    def test_file_renamed_detected_by_artifact_id(self, output_dir):
        """When a file is renamed on disk but keeps the same artifact_id,
        the mapping should update the file_name."""
        od = output_dir
        firm_dir = od / "FIRM_A"
        firm_dir.mkdir(parents=True)

        # Create initial file
        _make_tagged_file(firm_dir, "old_name.pdf", "art600")

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {},
                    "artifacts": {
                        "art600": {
                            "file_name": "old_name.pdf",
                            "identifier": None,
                            "contains_monthly_net_performance_update": False,
                            "processed": True,
                        }
                    },
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        # Rename the file on disk (keep artifact_id)
        old_path = firm_dir / "old_name - art600.pdf"
        new_path = firm_dir / "new_name - art600.pdf"
        old_path.rename(new_path)

        result = sync_moved_artifacts(output_dir=od)

        # Should not be treated as moved or new — just a name update
        assert result["moved"] == []
        assert result["new_artifacts"] == []

        mappings = _load_mappings(od)
        assert mappings["canonical_names"]["FIRM_A"]["artifacts"]["art600"]["file_name"] == "new_name.pdf"

    def test_filename_fallback_when_artifact_id_lost(self, output_dir):
        """When a file loses its artifact_id suffix (user strips it),
        it should be re-matched by filename and re-tagged with the same id."""
        od = output_dir
        firm_dir = od / "FIRM_A"
        firm_dir.mkdir(parents=True)

        _write_mappings(od, {
            "canonical_names": {
                "FIRM_A": {
                    "aliases": ["FIRM_A"],
                    "identifier": None,
                    "funds": {},
                    "artifacts": {
                        "art700": {
                            "file_name": "report.pdf",
                            "identifier": None,
                            "contains_monthly_net_performance_update": False,
                            "processed": True,
                        }
                    },
                }
            },
            "_metadata": {"last_updated": "2025-01-01"},
        })

        # Create the file WITHOUT the artifact_id suffix (as if user stripped it)
        (firm_dir / "report.pdf").write_bytes(b"content")

        result = sync_moved_artifacts(output_dir=od)

        # Should re-tag with the same artifact_id
        assert len(result["new_artifacts"]) == 1
        assert result["new_artifacts"][0]["artifact_id"] == "art700"

        # File on disk should be renamed back with artifact_id
        assert (firm_dir / "report - art700.pdf").exists()
        assert not (firm_dir / "report.pdf").exists()
