"""
Combined monitor: classify new emails, process performance updates,
reconcile misplaced artifacts, and sync manually moved artifacts
in a single polling loop.

Lives at the root level to avoid circular imports between
classify.py and performance.py.
"""

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fofproject.log import log, set_verbose, RECONCILE, SYNC, MONITOR, NOTION, EMAIL, GRAPHS
from fofproject.paths import DEFAULT_EMAIL_INPUT_DIR, DEFAULT_OUTPUT_DIR, EMAIL_STORAGE_DIR
from fofproject.notion import watch_folder
from fofproject.connection import monitor_emails, download_all_emails, create_token_provider
from fofproject.classify import (
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
    find_funds_missing_graphs,
)


# ── Helpers ──────────────────────────────────────────────

def _banner(email_input_dir, output_dir, poll_interval, run_once):
    w = 60
    print()
    print("=" * w)
    print("  FOF MONITOR")
    print("=" * w)
    print(f"  Email DL:  {EMAIL_STORAGE_DIR}")
    print(f"  Emails:    {email_input_dir}")
    print(f"  Output:    {output_dir}")
    print(f"  Interval:  {poll_interval}s" + ("  (single run)" if run_once else ""))
    if not run_once:
        print("  Ctrl+C to stop")
    print("=" * w)
    print()


def _cycle_header(cycle_num):
    ts = datetime.now().strftime("%H:%M:%S")
    label = f" Cycle #{cycle_num} "
    w = 60
    side = (w - len(label) - len(ts) - 4) // 2
    print(f"\n{'─' * side}{label}{'─' * side}  {ts}")


def _cycle_idle():
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"  {ts}   No changes detected.", end="\r")


def _section(phase, summary):
    """Print a phase summary header only when there's something to report."""
    log.info(summary, phase=phase)


_MAX_RETRIES = 10
_BASE_BACKOFF = 5  # seconds


def _retry_loop(name, phase, func):
    """Run *func* in a loop, restarting on crash with exponential backoff."""
    retries = 0
    while True:
        try:
            log.info(f"Starting {name}...", phase=phase)
            func()
            # If the function returns normally, it means it finished cleanly
            break
        except Exception as e:
            retries += 1
            if retries > _MAX_RETRIES:
                log.error(
                    f"{name} crashed {retries} times, giving up: {e}",
                    phase=phase,
                )
                break
            delay = min(_BASE_BACKOFF * (2 ** (retries - 1)), 300)
            log.error(
                f"{name} crashed (attempt {retries}/{_MAX_RETRIES}): {e}  "
                f"— retrying in {delay}s",
                phase=phase,
            )
            time.sleep(delay)


def _run_notion_watcher(output_dir: Path):
    """Run Notion folder watcher in a background thread with auto-retry."""
    _retry_loop("Notion watcher", NOTION, lambda: watch_folder(output_dir))


def _run_email_monitor(poll_interval: int):
    """Download all undownloaded emails, then monitor for new ones with auto-retry."""
    def _inner():
        token_func = create_token_provider()

        log.info("Downloading all undownloaded emails...", phase=EMAIL)
        token = token_func()
        count = download_all_emails(token, base_dir=EMAIL_STORAGE_DIR, skip_existing=True)
        log.info(f"Initial download complete: {count} email(s) downloaded.", phase=EMAIL)

        log.info("Starting email monitor...", phase=EMAIL)
        monitor_emails(token_func, base_dir=EMAIL_STORAGE_DIR, poll_interval=poll_interval)

    _retry_loop("Email monitor", EMAIL, _inner)


# ── Main loop ────────────────────────────────────────────

def monitoring(
    email_input_dir: Path = None,
    output_dir: Path = None,
    poll_interval: int = 30,
    run_once: bool = False,
    interactive: bool = False,
    verbose: bool = False,
):
    """
    Combined monitor: classify new emails, process performance updates,
    reconcile misplaced artifacts, and sync manually moved artifacts
    in a single polling loop.

    Args:
        interactive: If True, show the interactive menu before starting.
        verbose:     If True, show detailed sub-function output (DEBUG level).
    """
    if interactive:
        return _interactive_menu()

    if verbose:
        set_verbose(True)

    email_input_dir = email_input_dir or DEFAULT_EMAIL_INPUT_DIR
    output_dir = output_dir or DEFAULT_OUTPUT_DIR

    _banner(email_input_dir, output_dir, poll_interval, run_once)

    # ── Start Notion folder watcher in background ─────────
    notion_thread = threading.Thread(
        target=_run_notion_watcher, args=(output_dir,), daemon=True
    )
    notion_thread.start()

    # # ── Start email monitor in background ─────────────────
    email_thread = threading.Thread(
        target=_run_email_monitor, args=(poll_interval,), daemon=True
    )
    email_thread.start()

    classify_result = None
    cycle = 0
    consecutive_errors = 0
    try:
        while True:
            cycle += 1
            had_activity = False

            if not run_once or cycle == 1:
                _cycle_header(cycle)

            try:
                # Load mappings once per iteration; all sub-functions share
                # the same object and the single save happens at the end.
                mappings = load_firm_mappings(output_dir)

                # ── 1. Classify new emails ───────────────────────
                classify_result = classify_new_emails(
                    email_input_dir, output_dir, mappings=mappings
                )

                if classify_result["new_folders_found"] > 0:
                    had_activity = True

                # ── 2. Process performance updates ───────────────
                perf_results = process_performance_updates(output_dir, mappings=mappings)

                if perf_results:
                    had_activity = True
                    affected_ids = {r["identifier"] for r in perf_results if r.get("identifier")}
                    generate_fund_graphs(output_dir, identifiers=affected_ids or None)
                    backfill_computed_metrics(output_dir, identifiers=affected_ids or None)

                # ── 2b. Generate graphs for resolved conflicts ──
                missing_ids = find_funds_missing_graphs(output_dir)
                if missing_ids:
                    had_activity = True
                    _section(GRAPHS, f"Generating graphs for {len(missing_ids)} fund(s) missing graphs")
                    generate_fund_graphs(output_dir, identifiers=missing_ids)
                    backfill_computed_metrics(output_dir, identifiers=missing_ids)

                # ── 3. Reconcile misplaced artifacts ─────────────
                reconcile_result = reconcile_misplaced_artifacts(
                    output_dir, mappings=mappings
                )

                if reconcile_result["relocated"]:
                    had_activity = True
                    _section(RECONCILE, f"Relocated {len(reconcile_result['relocated'])} misplaced artifact(s)")
                    for r in reconcile_result["relocated"]:
                        log.detail(
                            f"  >> {r['file_name']} [{r['artifact_id']}]"
                            f"  {r['from']} -> {r['to']}",
                            phase=RECONCILE,
                        )
                for err in reconcile_result.get("errors", []):
                    log.error(f"  {err}", phase=RECONCILE)

                # ── 4. Sync manually moved artifacts ─────────────
                move_result = sync_moved_artifacts(output_dir, mappings=mappings)

                # Single save for all mutations this iteration
                save_firm_mappings(mappings, output_dir)

                if move_result["moved"]:
                    had_activity = True
                    _section(SYNC, f"Synced {len(move_result['moved'])} moved artifact(s)")
                    for m in move_result["moved"]:
                        log.detail(
                            f"  ~ {m.get('old_file', '?')} [{m.get('artifact_id', '')}]"
                            f"  {m.get('from', '?')} -> {m.get('to', '?')}",
                            phase=SYNC,
                        )

                if move_result.get("new_folders"):
                    had_activity = True
                    _section(SYNC, f"Registered {len(move_result['new_folders'])} new folder(s)")
                    for nf in move_result["new_folders"]:
                        log.detail(f"  + {nf['folder']}  ->  {nf['firm']}", phase=SYNC)

                if move_result.get("new_artifacts"):
                    had_activity = True
                    _section(SYNC, f"Tagged {len(move_result['new_artifacts'])} new artifact(s)")
                    for na in move_result["new_artifacts"]:
                        loc = na["firm"] + (f"/{na['fund']}" if na.get("fund") else "")
                        log.detail(f"  + {na['file_name']} [{na['artifact_id']}]  ->  {loc}", phase=SYNC)

                if move_result.get("removed_folders"):
                    had_activity = True
                    _section(SYNC, f"Soft-deleted {len(move_result['removed_folders'])} folder(s)")
                    for rf in move_result["removed_folders"]:
                        log.detail(f"  - {rf['folder']} ({rf['type']} under {rf['firm']})", phase=SYNC)

                if move_result.get("deleted_artifacts"):
                    had_activity = True
                _section(SYNC, f"Soft-deleted {len(move_result['deleted_artifacts'])} artifact(s)")
                for da in move_result["deleted_artifacts"]:
                    log.detail(f"  - {da['file_name']} [{da['artifact_id']}]", phase=SYNC)

                for err in move_result.get("errors", []):
                    log.error(f"  {err}", phase=SYNC)

                consecutive_errors = 0  # reset on successful cycle

            except Exception as e:
                consecutive_errors += 1
                log.error(
                    f"Cycle {cycle} failed (consecutive errors: "
                    f"{consecutive_errors}): {e}",
                    phase=MONITOR,
                )
                if consecutive_errors >= _MAX_RETRIES:
                    log.error(
                        f"Too many consecutive failures ({consecutive_errors}), "
                        f"stopping main loop.",
                        phase=MONITOR,
                    )
                    break

            if not had_activity:
                _cycle_idle()

            if run_once:
                log.info("Single check completed.", phase=MONITOR)
                break

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        print("\n\n  Monitoring stopped.\n")

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
        verbose = input("Verbose output? (y/N): ").strip().lower() == "y"
        monitoring(poll_interval=interval, run_once=False, verbose=verbose)
    elif mode == "9":
        manage_aliases()
    else:
        print("Invalid mode.")


if __name__ == "__main__":
    verbose_flag = "--verbose" in sys.argv or "-v" in sys.argv
    monitoring(interactive=False, verbose=verbose_flag)
