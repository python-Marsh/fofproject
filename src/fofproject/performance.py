"""
Performance Update Processor

Monitors the same output directory as classify.py, finds artifacts flagged as
containing monthly net performance updates that haven't been processed yet,
and runs load.py's process_single_pdf on them.
"""

import json
import shutil
from pathlib import Path
from fofproject.log import log, PERF, GRAPHS, METRICS, RECONCILE
from fofproject.paths import DEFAULT_OUTPUT_DIR
from fofproject.classify import (
    load_firm_mappings,
    save_firm_mappings,
    _parse_folder_identifier,
    CONFLICT_IDENTIFIER_PREFIX,
)
from fofproject.load import process_single_pdf, init_funds
from fofproject.fund import load_benchmarks
import fofproject.fund as fund_module


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
            if art_info.get(
                "contains_monthly_net_performance_update"
            ) and not art_info.get("processed"):
                file_name = art_info.get("file_name", "")
                if not file_name.lower().endswith(".pdf"):
                    continue
                # Build file path: output_dir / firm_folder / filename
                # The firm folder on disk may use the canonical name directly
                file_path = _resolve_artifact_path(output_dir, firm_name, None, art_id)
                if file_path and file_path.exists():
                    results.append(
                        {
                            "firm": firm_name,
                            "fund": None,
                            "artifact_id": art_id,
                            "file_name": file_name,
                            "file_path": file_path,
                        }
                    )

        # Check fund-level artifacts
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            for art_id, art_info in fund_data.get("artifacts", {}).items():
                if art_info.get(
                    "contains_monthly_net_performance_update"
                ) and not art_info.get("processed"):
                    file_name = art_info.get("file_name", "")
                    if not file_name.lower().endswith(".pdf"):
                        continue
                    file_path = _resolve_artifact_path(
                        output_dir, firm_name, fund_name, art_id
                    )
                    if file_path and file_path.exists():
                        results.append(
                            {
                                "firm": firm_name,
                                "fund": fund_name,
                                "artifact_id": art_id,
                                "file_name": file_name,
                                "file_path": file_path,
                            }
                        )

    return results


def _resolve_artifact_path(
    output_dir: Path,
    firm_name: str,
    fund_name: str | None,
    artifact_id: str,
) -> Path | None:
    """Find the PDF file on disk by scanning firm/fund folders for a filename containing the artifact_id."""
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
    mappings: dict,
    firm_name: str,
    fund_name: str | None,
    artifact_id: str,
    identifier: str = "",
    artifact_path: Path | None = None,
):
    """Mark an artifact as processed and write back its computed identifier.

    The identifier is stored on the artifact record immediately.  Promotion
    to the fund level is deferred until *all* performance artifacts in the
    fund have been processed (see ``_try_promote_fund_identifier``).
    """
    canonical = mappings.get("canonical_names", {}).get(firm_name, {})
    if fund_name:
        fund_data = canonical.get("funds", {}).get(fund_name, {})
        artifacts = fund_data.get("artifacts", {})
    else:
        fund_data = None
        artifacts = canonical.get("artifacts", {})

    if artifact_id not in artifacts:
        return None, None

    artifacts[artifact_id]["processed"] = True

    # Write identifier back to the artifact record
    if identifier:
        artifacts[artifact_id]["identifier"] = identifier

    # Only attempt promotion for fund-level artifacts
    if fund_data is None:
        return None, None

    # Skip if the fund already has an identifier
    if fund_data.get("identifier"):
        return None, None

    return _try_promote_fund_identifier(fund_data, artifacts, artifact_path)


def _try_promote_fund_identifier(
    fund_data: dict,
    artifacts: dict,
    artifact_path: Path | None,
) -> tuple[Path | None, Path | None]:
    """Promote an identifier to the fund level once all performance artifacts are processed.

    Waits until every artifact flagged with ``contains_monthly_net_performance_update``
    has been processed.  Then picks the most common identifier among them:

    - Single winner  → promoted as the fund identifier, folder renamed.
    - Tie (≥2 identifiers share the highest count) → folder renamed to
      ``404 multiple identifier_{fund_name}`` so downstream consumers
      (Notion upload, sync) skip it.
    """
    # Check if any performance PDF artifact is still unprocessed
    # (skip non-PDF artifacts like .link.json — they can't be processed by
    # process_single_pdf so they should not block promotion)
    for art_info in artifacts.values():
        if not art_info.get("contains_monthly_net_performance_update"):
            continue
        file_name = art_info.get("file_name", "")
        if not file_name.lower().endswith(".pdf"):
            continue
        if not art_info.get("processed"):
            return None, None  # still waiting

    # Collect identifiers only from performance artifacts
    id_counts: dict[str, int] = {}
    for art_info in artifacts.values():
        if not art_info.get("contains_monthly_net_performance_update"):
            continue
        aid = art_info.get("identifier")
        if aid:
            id_counts[aid] = id_counts.get(aid, 0) + 1

    if not id_counts:
        return None, None

    # Find the most common identifier
    max_count = max(id_counts.values())
    winners = [ident for ident, cnt in id_counts.items() if cnt == max_count]

    if artifact_path is None:
        return None, None
    fund_dir = artifact_path.parent
    if not fund_dir.exists():
        return None, None
    base_name, _ = _parse_folder_identifier(fund_dir.name)

    if len(winners) == 1:
        # Clear winner — promote to fund level
        chosen = winners[0]
        fund_data["identifier"] = chosen
        new_folder_name = f"{base_name} - {chosen}"
    else:
        # Tie — flag as conflict
        fund_data["identifier"] = None
        new_folder_name = f"{CONFLICT_IDENTIFIER_PREFIX}_{base_name}"

    new_dir = fund_dir.parent / new_folder_name
    if fund_dir != new_dir:
        fund_dir.rename(new_dir)
        return fund_dir, new_dir
    return None, None


def relocate_misplaced_artifacts(output_dir: Path = None) -> list:
    """Find and move mislocated artifacts/JSON files to matching fund folders.

    Walks the firm_fund_mappings registry to find artifacts whose identifier
    does not match the fund folder hosting them. Then scans all fund folders
    to find a folder whose identifier matches, and moves the file there.

    Also scans JSON files in each fund's json/ subfolder — if a JSON filename
    (which is the identifier) doesn't match the hosting fund folder's
    identifier, it is moved to the correct fund folder's json/ subfolder.

    A null identifier on either side does not constitute a match or mismatch.

    Returns a list of dicts describing each relocation performed.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not output_dir.exists():
        return []

    mappings = load_firm_mappings(output_dir)
    canonical_names = mappings.get("canonical_names", {})

    # Build a lookup: identifier -> (firm_name, fund_name, fund_folder_path)
    # by scanning all fund folders on disk
    identifier_to_fund_folder = {}
    for firm_folder in sorted(output_dir.iterdir()):
        if not firm_folder.is_dir() or firm_folder.name.startswith("."):
            continue
        for subfolder in sorted(firm_folder.iterdir()):
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue
            fund_name, folder_identifier = _parse_folder_identifier(subfolder.name)
            if folder_identifier:
                identifier_to_fund_folder[folder_identifier] = {
                    "firm_folder": firm_folder,
                    "fund_folder": subfolder,
                    "fund_name": fund_name,
                    "folder_identifier": folder_identifier,
                }

    relocated = []

    # --- Pass 1: Registry-based artifact relocation ---
    for firm_name, firm_data in canonical_names.items():
        if firm_data.get("_deleted_at"):
            continue
        for fund_name, fund_data in firm_data.get("funds", {}).items():
            if fund_data.get("_deleted_at"):
                continue
            fund_identifier = fund_data.get("identifier")
            for art_id, art_info in list(fund_data.get("artifacts", {}).items()):
                art_identifier = art_info.get("identifier")
                if not art_identifier:
                    continue
                # Skip if artifact identifier matches the fund it's in
                if art_identifier == fund_identifier:
                    continue
                # Find the target fund folder by artifact identifier
                target = identifier_to_fund_folder.get(art_identifier)
                if not target:
                    continue

                # Find the actual file on disk in the current location
                source_path = _resolve_artifact_path(
                    output_dir, firm_name, fund_name, art_id
                )
                if not source_path or not source_path.exists():
                    continue

                dest_path = target["fund_folder"] / source_path.name
                if dest_path.exists():
                    continue

                shutil.move(str(source_path), str(dest_path))
                relocated.append(
                    {
                        "type": "artifact",
                        "artifact_id": art_id,
                        "file_name": source_path.name,
                        "from_fund": fund_name,
                        "to_fund": target["fund_name"],
                        "identifier": art_identifier,
                        "source": str(source_path),
                        "destination": str(dest_path),
                    }
                )
                log.detail(
                    f"Moved artifact '{source_path.name}' "
                    f"from '{fund_name}' -> '{target['fund_name']}' "
                    f"(identifier: {art_identifier})", phase=RECONCILE
                )

    # --- Pass 2: JSON file relocation ---
    for firm_folder in sorted(output_dir.iterdir()):
        if not firm_folder.is_dir() or firm_folder.name.startswith("."):
            continue
        for subfolder in sorted(firm_folder.iterdir()):
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue
            _, folder_identifier = _parse_folder_identifier(subfolder.name)
            json_dir = subfolder / "json"
            if not json_dir.is_dir():
                continue
            for json_file in sorted(json_dir.iterdir()):
                if not json_file.is_file() or json_file.suffix.lower() != ".json":
                    continue
                json_identifier = json_file.stem
                if not json_identifier:
                    continue
                # Skip if it matches the current folder
                if json_identifier == folder_identifier:
                    continue
                # Find a matching fund folder
                target = identifier_to_fund_folder.get(json_identifier)
                if not target:
                    continue
                # Don't move to the same folder
                if target["fund_folder"] == subfolder:
                    continue

                dest_json_dir = target["fund_folder"] / "json"
                dest_json_dir.mkdir(exist_ok=True)
                dest_path = dest_json_dir / json_file.name
                if dest_path.exists():
                    continue

                shutil.move(str(json_file), str(dest_path))
                relocated.append(
                    {
                        "type": "json",
                        "file_name": json_file.name,
                        "from_folder": subfolder.name,
                        "to_folder": target["fund_folder"].name,
                        "identifier": json_identifier,
                        "source": str(json_file),
                        "destination": str(dest_path),
                    }
                )
                log.detail(
                    f"Moved JSON '{json_file.name}' "
                    f"from '{subfolder.name}' -> '{target['fund_folder'].name}' "
                    f"(identifier: {json_identifier})", phase=RECONCILE
                )

    if relocated:
        log.info(f"Relocated {len(relocated)} misplaced file(s).", phase=RECONCILE)
    else:
        log.detail("No misplaced artifacts or JSON files found.", phase=RECONCILE)

    return relocated


def process_performance_updates(
    output_dir: Path = None,
    save: bool = True,
    *,
    mappings: dict = None,
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

    _owns_mappings = mappings is None
    mappings = mappings if mappings is not None else load_firm_mappings(output_dir)
    unprocessed = _find_unprocessed_performance_artifacts(mappings, output_dir)

    if not unprocessed:
        log.detail("No unprocessed performance updates found.", phase=PERF)
        return []

    log.info(f"Found {len(unprocessed)} unprocessed performance artifact(s).", phase=PERF)

    results = []
    for item in unprocessed:
        file_path = str(item["file_path"])
        firm = item["firm"]
        fund = item["fund"] or "(firm-level)"
        art_id = item["artifact_id"]

        log.detail(f"Processing: {item['file_name']} [{firm} / {fund}]", phase=PERF)
        try:
            result = process_single_pdf(file_path, save=save)
            results.append(result)
            old_dir, new_dir = _mark_artifact_processed(
                mappings,
                firm,
                item["fund"],
                art_id,
                identifier=result.get("identifier", ""),
                artifact_path=item["file_path"],
            )
            # If the fund folder was renamed, update paths for remaining items
            if old_dir is not None and new_dir is not None:
                for other in unprocessed:
                    if other["file_path"].parent == old_dir:
                        other["file_path"] = new_dir / other["file_path"].name
            log.detail(f"  {result.get('fund_name', 'UNKNOWN')} processed successfully.", phase=PERF)
        except Exception as e:
            log.error(f"  Failed: {item['file_name']}: {e}", phase=PERF)

    # Save updated mappings with processed flags (skip if caller owns the mappings object)
    if _owns_mappings:
        save_firm_mappings(mappings, output_dir)
    log.info(f"Processed {len(results)}/{len(unprocessed)} performance artifact(s).", phase=PERF)

    return results


def find_funds_missing_graphs(output_dir: Path = None) -> set:
    """Return identifiers of funds that have JSON data but no exported graphs.

    This catches resolved 404-conflict folders (renamed from
    ``404 multiple identifier_X`` to ``X - ID``) and any other funds
    whose graphs haven't been generated yet.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not output_dir.exists():
        return set()

    missing = set()
    for firm_folder in output_dir.iterdir():
        if not firm_folder.is_dir() or firm_folder.name.startswith("."):
            continue
        for subfolder in firm_folder.iterdir():
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue
            _, identifier = _parse_folder_identifier(subfolder.name)
            if not identifier:
                continue
            json_file = subfolder / "json" / f"{identifier}.json"
            if not json_file.is_file():
                continue
            graph_dir = subfolder / "graph"
            if not graph_dir.is_dir() or not any(graph_dir.iterdir()):
                missing.add(identifier)
    return missing


def generate_fund_graphs(output_dir: Path = None, benchmark_fund=None, language="en", identifiers: set = None):
    """Load JSON from each fund's json/ folder and export summary graphs.

    For each fund subfolder, parses the folder identifier (e.g.
    "FundName - ABC123" -> identifier "ABC123"), then loads only the
    matching JSON file (json/ABC123.json). Creates a Fund object and
    calls summary_of_a_fund(save=True, show=False), exporting all
    graphs into a graph/ folder next to the json/ folder.

    Args:
        output_dir: Root output directory (same as classify.py).
        benchmark_fund: Optional Fund used as benchmark for metrics.
        language: Language for chart labels ("en" or "cn").

    Returns:
        List of dicts describing each fund processed.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not output_dir.exists():
        log.warn("Output directory does not exist.", phase=GRAPHS)
        return []

    original_save_dir = fund_module.save_dir
    results = []

    # Pre-load benchmarks once (instead of per-fund) when no explicit benchmark
    bm_dict_preloaded = None
    if benchmark_fund is None:
        try:
            bm_dict_preloaded = load_benchmarks()
        except Exception:
            pass  # BENCHMARK.csv not available, skip

    for firm_folder in sorted(output_dir.iterdir()):
        if not firm_folder.is_dir() or firm_folder.name.startswith("."):
            continue

        for subfolder in sorted(firm_folder.iterdir()):
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue

            _, identifier = _parse_folder_identifier(subfolder.name)
            if not identifier:
                continue

            if identifiers is not None and identifier not in identifiers:
                continue

            json_dir = subfolder / "json"
            if not json_dir.is_dir():
                continue

            json_file = json_dir / f"{identifier}.json"
            if not json_file.is_file():
                continue

            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                log.warn(f"Could not load {json_file.name}: {e}", phase=GRAPHS)
                continue

            funds = init_funds([data], benchmarks=bm_dict_preloaded)
            if not funds:
                continue

            # Create graph/ folder next to json/
            graph_dir = subfolder / "graph"
            graph_dir.mkdir(exist_ok=True)

            # Temporarily redirect save_dir to the graph folder
            fund_module.save_dir = graph_dir

            for fund_name, fund in funds.items():
                try:
                    fund.summary_of_a_fund(
                        benchmark_fund=benchmark_fund,
                        language=language,
                        save=True,
                        show=False,
                    )
                    results.append(
                        {
                            "firm_folder": firm_folder.name,
                            "fund_folder": subfolder.name,
                            "fund_name": fund_name,
                            "graph_dir": str(graph_dir),
                        }
                    )
                    log.detail(f"  Exported graphs for {fund_name}", phase=GRAPHS)
                except Exception as e:
                    log.error(f"  Failed graphs for {fund_name}: {e}", phase=GRAPHS)

    # Restore original save_dir
    fund_module.save_dir = original_save_dir

    log.info(f"Generated graphs for {len(results)} fund(s).", phase=GRAPHS)
    return results


def backfill_computed_metrics(output_dir: Path = None, identifiers: set = None):
    """Update each fund's JSON file with computed return_pa and volatility_pa.

    Walks the same folder structure as generate_fund_graphs(), loads each
    fund's JSON, creates a Fund object via init_funds, then writes the
    Fund's total_ann_rtn and total_vol back into the JSON as return_pa
    and volatility_pa.

    Args:
        output_dir: Root output directory (same as classify.py).

    Returns:
        List of dicts describing each fund updated.
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    if not output_dir.exists():
        log.warn("Output directory does not exist.", phase=METRICS)
        return []

    updated = []

    for firm_folder in sorted(output_dir.iterdir()):
        if not firm_folder.is_dir() or firm_folder.name.startswith("."):
            continue

        for subfolder in sorted(firm_folder.iterdir()):
            if not subfolder.is_dir() or subfolder.name.startswith("."):
                continue
            if subfolder.name.startswith(CONFLICT_IDENTIFIER_PREFIX):
                continue

            _, identifier = _parse_folder_identifier(subfolder.name)
            if not identifier:
                continue

            if identifiers is not None and identifier not in identifiers:
                continue

            json_dir = subfolder / "json"
            if not json_dir.is_dir():
                continue

            json_file = json_dir / f"{identifier}.json"
            if not json_file.is_file():
                continue

            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                log.warn(f"Could not load {json_file.name}: {e}", phase=METRICS)
                continue

            funds = init_funds([data])
            if not funds:
                continue

            fund = next(iter(funds.values()))
            if fund.total_ann_rtn is None and fund.total_vol is None:
                continue

            changed = False
            if fund.total_ann_rtn is not None:
                data["return_pa"] = round(fund.total_ann_rtn, 6)
                changed = True
            if fund.total_vol is not None:
                data["volatility_pa"] = round(fund.total_vol, 6)
                changed = True

            if changed:
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                updated.append(
                    {
                        "fund_name": fund.name,
                        "identifier": identifier,
                        "return_pa": data.get("return_pa"),
                        "volatility_pa": data.get("volatility_pa"),
                        "json_file": str(json_file),
                    }
                )
                log.detail(
                    f"  {fund.name}: return_pa={data.get('return_pa')}, "
                    f"volatility_pa={data.get('volatility_pa')}",
                    phase=METRICS,
                )

    log.info(f"Updated metrics for {len(updated)} fund(s).", phase=METRICS)
    return updated


if __name__ == "__main__":
    process_performance_updates()
    generate_fund_graphs()
    backfill_computed_metrics()
