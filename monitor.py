"""
Combined monitor: classify new emails, process performance updates,
reconcile misplaced artifacts, and sync manually moved artifacts
in a single polling loop.

Lives at the root level to avoid circular imports between
classify.py and performance.py.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

from fofproject.classify import (
    DEFAULT_EMAIL_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    classify_and_organize_emails,
    classify_new_emails,
    load_firm_mappings,
    save_firm_mappings,
    reconcile_misplaced_artifacts,
    sync_moved_artifacts,
    list_firms,
    list_overrides,
    add_email_override,
    add_domain_override,
    reassign_firm,
    manage_aliases,
    _interactive_firm_picker,
)
from fofproject.performance import (
    process_performance_updates,
    generate_fund_graphs,
    backfill_computed_metrics,
)


def monitoring(
    email_input_dir: Path = None,
    output_dir: Path = None,
    poll_interval: int = 30,
    run_once: bool = False,
    interactive: bool = False,
):
    """
    Combined monitor: classify new emails, process performance updates,
    reconcile misplaced artifacts, and sync manually moved artifacts
    in a single polling loop.

    Args:
        interactive: If True, show the interactive menu before starting.
    """
    if interactive:
        return _interactive_menu()

    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    print("=" * 60)
    print("EMAIL + ARTIFACT MOVE MONITOR + PERFORMANCE UPDATES + RECONCILIATION")
    print("=" * 60)
    print(f"Emails:     {email_input_dir}")
    print(f"Output:     {output_dir}")
    print(f"Poll interval: {poll_interval} seconds")
    if not run_once:
        print("Press Ctrl+C to stop monitoring")
    print("-" * 60)

    classify_result = None
    try:
        while True:
            # Load mappings once per iteration; all sub-functions share
            # the same object and the single save happens at the end.
            mappings = load_firm_mappings(output_dir)

            # 1. Classify new emails
            classify_result = classify_new_emails(
                email_input_dir, output_dir, mappings=mappings
            )

            if classify_result["new_folders_found"] > 0:
                print(
                    f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Processed {classify_result['new_folders_found']} new email(s)"
                )
                for item in classify_result["classifications"]:
                    firm = item.get("firm")
                    if firm:
                        print(f"  + {item['subject'][:40]}... -> {firm}")
                    else:
                        print(
                            f"  - {item['subject'][:40]}... ({item.get('reason', 'skipped')})"
                        )

            # 2. Process performance updates, generate graphs, backfill metrics
            perf_results = process_performance_updates(output_dir, mappings=mappings)

            if perf_results:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Processed {len(perf_results)} performance update(s)"
                )
                generate_fund_graphs(output_dir)
                backfill_computed_metrics(output_dir)

            # 3. Reconcile misplaced artifacts by identifier matching
            reconcile_result = reconcile_misplaced_artifacts(
                output_dir, mappings=mappings
            )

            if reconcile_result["relocated"]:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Relocated {len(reconcile_result['relocated'])} misplaced artifact(s)"
                )
                for r in reconcile_result["relocated"]:
                    print(
                        f"  >> {r['file_name']} [{r['artifact_id']}]"
                        f" : {r['from']} -> {r['to']}"
                    )
            if reconcile_result["errors"]:
                for err in reconcile_result["errors"]:
                    print(f"  ERROR (reconcile): {err}")

            # 4. Sync manually moved artifacts
            move_result = sync_moved_artifacts(output_dir, mappings=mappings)

            # Single save for all mutations this iteration
            save_firm_mappings(mappings, output_dir)

            if move_result["moved"]:
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Synced {len(move_result['moved'])} moved artifact(s)"
                )
                for m in move_result["moved"]:
                    print(
                        f"  ~ {m.get('old_file', '?')} [{m.get('artifact_id', '')}]"
                        f" : {m.get('from', '?')} -> {m.get('to', '?')}"
                    )

            if move_result.get("new_folders"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Registered {len(move_result['new_folders'])} new folder(s)"
                )
                for nf in move_result["new_folders"]:
                    print(f"  + {nf['folder']} -> {nf['firm']}")

            if move_result.get("new_artifacts"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Tagged {len(move_result['new_artifacts'])} new artifact(s)"
                )
                for na in move_result["new_artifacts"]:
                    loc = na["firm"] + (f"/{na['fund']}" if na.get("fund") else "")
                    print(f"  + {na['file_name']} [{na['artifact_id']}] -> {loc}")

            if move_result.get("removed_folders"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Soft-deleted {len(move_result['removed_folders'])} folder(s)"
                )
                for rf in move_result["removed_folders"]:
                    print(f"  - {rf['folder']} ({rf['type']} under {rf['firm']})")

            if move_result.get("deleted_artifacts"):
                print(
                    f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] "
                    f"Soft-deleted {len(move_result['deleted_artifacts'])} artifact(s)"
                )
                for da in move_result["deleted_artifacts"]:
                    print(f"  - {da['file_name']} [{da['artifact_id']}]")

            if move_result["errors"]:
                for err in move_result["errors"]:
                    print(f"  ERROR: {err}")

            if (
                not classify_result["new_folders_found"]
                and not perf_results
                and not move_result["moved"]
                and not move_result.get("new_folders")
                and not move_result.get("new_artifacts")
                and not move_result.get("removed_folders")
                and not move_result.get("deleted_artifacts")
                and not reconcile_result["relocated"]
            ):
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] No new emails or moves",
                    end="\r",
                )

            if run_once:
                print("\nSingle check completed.")
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\nMonitoring stopped by user.")

    return classify_result if run_once else None


def _interactive_menu():
    """Interactive mode menu — moved from classify.py main()."""
    print("=" * 60)
    print("HEDGE FUND EMAIL CLASSIFIER")
    print("=" * 60)
    print("\nSelect mode:")
    print("  1. Classify and organize all emails")
    print("  2. Force reclassify all (ignore cache)")
    print("  3. List known firms and funds")
    print("  4. List all overrides")
    print("  5. Add email override (specific address -> firm)")
    print("  6. Add domain override (all from domain -> firm)")
    print("  7. Reassign/rename firm (old firm -> new firm, merges if new exists)")
    print("  8. Monitor for new emails + artifact moves (continuous)")
    print("  9. Manage aliases (firm/fund)")
    print()

    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = input("Enter mode (1-9): ").strip()

    if mode == "1":
        classify_and_organize_emails()
    elif mode == "2":
        classify_and_organize_emails(force_reclassify=True)
    elif mode == "3":
        list_firms()
    elif mode == "4":
        list_overrides()
    elif mode == "5":
        email = input("Enter email address: ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if email and firm:
            add_email_override(email, firm)
        else:
            print("Email and firm name are required.")
    elif mode == "6":
        domain = input("Enter domain (without @): ").strip()
        firm = input("Enter firm name to assign: ").strip()
        if domain and firm:
            add_domain_override(domain, firm)
        else:
            print("Domain and firm name are required.")
    elif mode == "7":
        old_firm = _interactive_firm_picker("Select OLD firm to reassign/remove")
        if not old_firm:
            print("No firm selected.")
        else:
            new_firm = _interactive_firm_picker(
                "Select NEW firm (target)",
                allow_new=True,
            )
            if not new_firm:
                print("No target firm provided.")
            else:
                reassign_firm(old_firm, new_firm)
    elif mode == "8":
        interval = input("Poll interval in seconds (default 30): ").strip()
        interval = int(interval) if interval.isdigit() else 30
        monitoring(poll_interval=interval, run_once=False)
    elif mode == "9":
        manage_aliases()
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    monitoring(interactive=False)
