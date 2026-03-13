"""
Performance Update Processor

Monitors the same output directory as classify.py, finds artifacts flagged as
containing monthly net performance updates that haven't been processed yet,
and runs load.py's process_single_pdf on them.
"""

from pathlib import Path
from fofproject.classify import (
    load_firm_mappings,
    save_firm_mappings,
    DEFAULT_OUTPUT_DIR,
)
from fofproject.load import process_single_pdf


def _find_unprocessed_performance_artifacts(mappings: dict, output_dir: Path) -> list:
    """Walk firm_fund_mappings and return artifacts needing performance processing.

    Returns a list of dicts:
        {
            "firm": str,
            "fund": str or None,
            "artifact_id": str,
            "file_name": str,
            "file_path": Path,
        }
    """
    results = []
    canonical_names = mappings.get("canonical_names", {})

    for firm_name, firm_data in canonical_names.items():
        if firm_data.get("_deleted_at"):
            continue

        # Check firm-level artifacts
        for art_id, art_info in firm_data.get("artifacts", {}).items():
            if (
                art_info.get("contains_monthly_net_performance_update")
                and not art_info.get("processed")
            ):
                file_name = art_info.get("file_name", "")
                if not file_name.lower().endswith(".pdf"):
                    continue
                # Build file path: output_dir / firm_folder / filename
                # The firm folder on disk may use the canonical name directly
                file_path = _resolve_artifact_path(
                    output_dir, firm_name, None, file_name, art_id
                )
                if file_path and file_path.exists():
                    results.append({
                        "firm": firm_name,
                        "fund": None,
                        "artifact_id": art_id,
                        "file_name": file_name,
                        "file_path": file_path,
                    })

        # Check fund-level artifacts
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            for art_id, art_info in fund_data.get("artifacts", {}).items():
                if (
                    art_info.get("contains_monthly_net_performance_update")
                    and not art_info.get("processed")
                ):
                    file_name = art_info.get("file_name", "")
                    if not file_name.lower().endswith(".pdf"):
                        continue
                    file_path = _resolve_artifact_path(
                        output_dir, firm_name, fund_name, file_name, art_id
                    )
                    if file_path and file_path.exists():
                        results.append({
                            "firm": firm_name,
                            "fund": fund_name,
                            "artifact_id": art_id,
                            "file_name": file_name,
                            "file_path": file_path,
                        })

    return results


def _resolve_artifact_path(
    output_dir: Path,
    firm_name: str,
    fund_name: str | None,
    file_name: str,
    artifact_id: str,
) -> Path | None:
    """Resolve the actual file path for an artifact on disk.

    Artifacts are stored with the artifact_id embedded in the filename.
    The folder on disk matches the firm name (or an alias).
    """
    # The artifact filename on disk includes the artifact_id suffix
    # e.g. "factsheet [abc123].pdf"
    # But file_name in the registry is the base name without the id.
    # We need to find the actual file by scanning the directory.

    firm_dir = output_dir / firm_name
    if not firm_dir.exists():
        # Try case-insensitive match
        for d in output_dir.iterdir():
            if d.is_dir() and d.name.upper() == firm_name.upper():
                firm_dir = d
                break
        else:
            return None

    if fund_name:
        # Look inside fund subfolders
        search_dirs = []
        for subfolder in firm_dir.iterdir():
            if subfolder.is_dir() and fund_name.lower() in subfolder.name.lower():
                search_dirs.append(subfolder)
        if not search_dirs:
            # Try all subfolders
            search_dirs = [d for d in firm_dir.iterdir() if d.is_dir()]
        search_dirs.append(firm_dir)  # fallback to firm root
    else:
        search_dirs = [firm_dir]

    # Search for the file with the artifact_id in the filename
    for search_dir in search_dirs:
        for f in search_dir.rglob("*"):
            if f.is_file() and artifact_id in f.name and f.suffix.lower() == ".pdf":
                return f

    return None


def _mark_artifact_processed(
    mappings: dict, firm_name: str, fund_name: str | None, artifact_id: str
):
    """Mark an artifact as processed in the mappings."""
    canonical = mappings.get("canonical_names", {}).get(firm_name, {})
    if fund_name:
        artifacts = canonical.get("funds", {}).get(fund_name, {}).get("artifacts", {})
    else:
        artifacts = canonical.get("artifacts", {})

    if artifact_id in artifacts:
        artifacts[artifact_id]["processed"] = True


def process_performance_updates(
    output_dir: Path = None,
    save: bool = True,
) -> list:
    """Find and process all unprocessed performance PDF artifacts.

    Reads firm_fund_mappings.json, finds artifacts where
    contains_monthly_net_performance_update == true and processed == false,
    runs process_single_pdf on each, and marks them as processed.

    Args:
        output_dir: The output directory (same one classify.py monitors).
        save: Whether to save JSON results from process_single_pdf.

    Returns:
        List of processed result dicts from process_single_pdf.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    mappings = load_firm_mappings(output_dir)
    unprocessed = _find_unprocessed_performance_artifacts(mappings, output_dir)

    if not unprocessed:
        print("No unprocessed performance updates found.")
        return []

    print(f"Found {len(unprocessed)} unprocessed performance artifact(s).")

    results = []
    for item in unprocessed:
        file_path = str(item["file_path"])
        firm = item["firm"]
        fund = item["fund"] or "(firm-level)"
        art_id = item["artifact_id"]

        print(f"\nProcessing: {item['file_name']} [{firm} / {fund}]")
        try:
            result = process_single_pdf(file_path, save=save)
            results.append(result)
            _mark_artifact_processed(mappings, firm, item["fund"], art_id)
            print(f"  -> {result.get('fund_name', 'UNKNOWN')} processed successfully.")
        except Exception as e:
            print(f"  ERROR processing {item['file_name']}: {e}")

    # Save updated mappings with processed flags
    save_firm_mappings(mappings, output_dir)
    print(f"\nDone. Processed {len(results)} of {len(unprocessed)} artifact(s).")

    return results
