import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# Import crawler and vault modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobstreet_cli import run_crawler, DEFAULT_DB_FILE
import vault_manager
import resume_tailor
import cover_letter_generator

DEFAULT_CONFIG_FILE = "searches.json" if os.path.exists("searches.json") else (os.path.join("..", "searches.json") if os.path.exists(os.path.join("..", "searches.json")) else "searches.json")
DEFAULT_VAULT_DIR = "." if (os.path.isdir("Jobs") or os.path.exists("00-Dashboard.md")) else "JobHiveVault"
SUMMARY_FILE = "run_summary.json"

DEFAULT_SEARCH_PAIRS = [
    {"keyword": "PHP Developer", "location": "Philippines"},
    {"keyword": "Laravel Developer", "location": "Philippines"},
    {"keyword": "Full Stack Developer", "location": "Philippines"},
    {"keyword": ".NET Developer", "location": "Philippines"},
    {"keyword": "C# Developer", "location": "Philippines"}
]

def load_searches_config(config_path=DEFAULT_CONFIG_FILE):
    """Load search pairs from JSON config file or fall back to defaults."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                pairs = json.load(f)
                if isinstance(pairs, list) and len(pairs) > 0:
                    return pairs
        except Exception as e:
            print(f"[!] Warning reading {config_path}: {e}. Falling back to default list.")
    else:
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_SEARCH_PAIRS, f, indent=2)
            print(f"[+] Created default search config at {config_path}")
        except Exception as e:
            print(f"[!] Could not create {config_path}: {e}")
    return DEFAULT_SEARCH_PAIRS

def backfill_is_remote(db_path=DEFAULT_DB_FILE):
    """Ensure is_remote column exists and backfill rows matching Remote or Work From Home."""
    if not os.path.exists(db_path):
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    try:
        cur.execute("ALTER TABLE jobs ADD COLUMN is_remote BOOLEAN DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # Flag rows where location contains Remote or Work From Home (case-insensitive)
    cur.execute("""
        UPDATE jobs
        SET is_remote = 1
        WHERE (
            LOWER(location) LIKE '%remote%' 
            OR LOWER(location) LIKE '%work from home%'
            OR LOWER(location) LIKE '%wfh%'
        )
    """)
    cur.execute("UPDATE jobs SET is_remote = 0 WHERE is_remote IS NULL")
    conn.commit()
    conn.close()

def get_job_remote_status(db_path, job_ids):
    """Return how many of the given job_ids are flagged as is_remote = 1."""
    if not job_ids or not os.path.exists(db_path):
        return 0
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in job_ids)
    cur.execute(f"SELECT COUNT(*) FROM jobs WHERE job_id IN ({placeholders}) AND is_remote = 1", job_ids)
    count = cur.fetchone()[0]
    conn.close()
    return count

def run_worker_cycle(config_path, db_path, vault_dir, max_pages_per_pair, fetch_details=False):
    """
    Execute one full pass:
    1. Scrape configured search pairs.
    2. Filter remote/keyword matches in SQLite.
    3. For each NEW matching job:
       - Create Obsidian note in JobHiveVault/Jobs/.
       - Invoke Resume Tailor subagent (sequential, read-only on base resume).
    4. Regenerate JobHiveVault/00-Dashboard.md.
    5. Write run_summary.json.
    """
    # Normalize vault_dir if already inside the vault
    if vault_dir == "JobHiveVault" and not os.path.isdir("JobHiveVault") and (os.path.isdir("Jobs") or os.path.exists("00-Dashboard.md")):
        vault_dir = "."

    os.makedirs(os.path.join(vault_dir, "Jobs"), exist_ok=True)
    os.makedirs(os.path.join(vault_dir, "Resumes", "Base"), exist_ok=True)
    os.makedirs(os.path.join(vault_dir, "Resumes", "Tailored"), exist_ok=True)

    searches = load_searches_config(config_path)
    cycle_start = time.time()
    start_iso = datetime.now().isoformat()

    print(f"\n========================================================")
    print(f"[*] Starting 2-Agent Worker Cycle at {start_iso}")
    print(f"[*] Loaded {len(searches)} search pairs from: {config_path}")
    print(f"[*] Obsidian Vault: {vault_dir}")
    print(f"========================================================")

    total_scanned = 0
    all_new_jobs = []

    for idx, item in enumerate(searches, 1):
        kw = item.get("keyword") or item.get("keywords", "")
        loc = item.get("location") or item.get("where", "Philippines")

        print(f"\n>>> [{idx}/{len(searches)}] Searching: '{kw}' in '{loc}'")
        res = run_crawler(
            keywords=kw,
            location=loc,
            max_pages=max_pages_per_pair,
            fetch_details=fetch_details,
            db_path=db_path
        )

        if isinstance(res, dict):
            new_jobs = res.get("new_jobs", [])
            scanned = res.get("total_scanned", len(new_jobs))
        else:
            new_jobs = res
            scanned = len(res)

        total_scanned += scanned

        # Apply post-filter & backfill in SQLite
        backfill_is_remote(db_path)

        # Process newly discovered jobs for this pair
        for job in new_jobs:
            all_new_jobs.append(job)
            
            # Check if job is remote or keyword match
            # Query updated is_remote status from DB
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()
            cur.execute("SELECT is_remote, full_description FROM jobs WHERE job_id = ?", (job["job_id"],))
            row = cur.fetchone()
            conn.close()

            if row:
                job["is_remote"] = bool(row[0])
                if row[1]:
                    job["full_description"] = row[1]

            # Write initial job note in Vault
            note_path = vault_manager.create_or_update_job_note(
                vault_dir=vault_dir,
                job=job,
                status="new"
            )
            print(f"  [Vault] Note created: {os.path.basename(note_path)}")

            # Invoke Resume Tailor Subagent sequentially
            resume_tailor.tailor_job(vault_dir, job)

            # Invoke Cover Letter Subagent sequentially
            cover_letter_generator.create_cover_letter_for_job(vault_dir, job)

        # Inter-pair jitter delay (10-20 seconds) between different queries
        if idx < len(searches):
            pair_delay = random.uniform(10.0, 20.0)
            print(f"[*] Pair complete. Inter-pair delay: {pair_delay:.2f}s before next search...")
            time.sleep(pair_delay)

    # Regenerate 00-Dashboard.md after all pairs complete
    dashboard_file = vault_manager.regenerate_dashboard(vault_dir)
    print(f"\n[+] Dashboard regenerated: {dashboard_file}")

    # Compute stats for summary
    new_job_ids = [j["job_id"] for j in all_new_jobs if j.get("job_id")]
    new_remote_count = get_job_remote_status(db_path, new_job_ids)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM jobs")
    total_in_db = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM jobs WHERE is_remote = 1")
    total_remote_in_db = cur.fetchone()[0]
    conn.close()

    duration_sec = round(time.time() - cycle_start, 2)
    end_iso = datetime.now().isoformat()

    summary_data = {
        "timestamp": end_iso,
        "duration_seconds": duration_sec,
        "searches_configured": len(searches),
        "total_scanned": total_scanned,
        "new_postings_found": len(all_new_jobs),
        "new_remote_postings_found": new_remote_count,
        "total_db_records": total_in_db,
        "total_db_remote_records": total_remote_in_db,
        "vault_directory": vault_dir
    }

    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2)

    print(f"\n========================================================")
    print(f"Cycle Complete Summary:")
    print(f"  Duration:                   {duration_sec}s")
    print(f"  Search pairs processed:     {len(searches)}")
    print(f"  Total postings scanned:     {total_scanned}")
    print(f"  New postings found:         {len(all_new_jobs)}")
    print(f"  New REMOTE postings found:  {new_remote_count}")
    print(f"  Total in SQLite Database:   {total_in_db} (Remote: {total_remote_in_db})")
    print(f"  Summary saved to:           {SUMMARY_FILE}")
    print(f"  Obsidian Dashboard:         {dashboard_file}")
    print(f"========================================================")

    return summary_data

def main():
    parser = argparse.ArgumentParser(description="JobStreet Multi-Search 2-Agent Worker Wrapper")
    parser.add_argument("--once", action="store_true", help="Run all search pairs one time and exit")
    parser.add_argument("--loop", type=float, metavar="HOURS", help="Repeat the full pair list every N hours")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to search pairs config (default: searches.json)")
    parser.add_argument("--vault", default=DEFAULT_VAULT_DIR, help="Path to JobHive Obsidian vault (default: JobHiveVault)")
    parser.add_argument("--pages", type=int, default=2, help="Max pages per search pair (default: 2)")
    parser.add_argument("--db", default=DEFAULT_DB_FILE, help="Path to SQLite database (default: jobs.sqlite)")
    parser.add_argument("--details", action="store_true", help="Also fetch full job descriptions from detail pages")

    args = parser.parse_args()

    if args.loop is not None and args.loop <= 0:
        print("[!] Error: --loop must be a positive number of hours.")
        sys.exit(1)

    if args.loop:
        interval_seconds = args.loop * 3600
        print(f"[*] Worker configured to loop every {args.loop} hour(s) ({interval_seconds}s).")
        print("[*] Press Ctrl+C at any time to stop.")
        cycle_count = 1
        try:
            while True:
                print(f"\n--- [Cycle #{cycle_count}] ---")
                run_worker_cycle(args.config, args.db, args.vault, args.pages, args.details)
                print(f"\n[*] Cycle #{cycle_count} finished. Sleeping for {args.loop} hour(s)...")
                cycle_count += 1
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[*] Worker loop stopped by user (Ctrl+C). Exiting cleanly.")
            sys.exit(0)
    else:
        run_worker_cycle(args.config, args.db, args.vault, args.pages, args.details)

if __name__ == "__main__":
    main()
