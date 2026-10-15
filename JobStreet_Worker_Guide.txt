# JobStreet Worker Wrapper — Documentation & Manual Run Guide

## 1. Overview & Setup

The worker wrapper (`jobstreet_worker.py`) automates multi-query crawling on JobStreet PH using the core `jobstreet_cli.py` Playwright scraper engine. It runs multiple keyword/location search pairs sequentially, applies SQLite post-filtering for remote roles, spaces out requests with anti-bot jitter, and logs summary metrics.

**Current State**: **INACTIVE**. Nothing has been auto-started or scheduled.

---

## 2. File Inventory (`C:\Users\ASUS\OneDrive\Desktop\`)

| File | Purpose |
| :--- | :--- |
| `jobstreet_worker.py` | Multi-search worker wrapper with rate limiting, SQLite backfill, and loop controls. |
| `searches.json` | Configurable list of search pairs editable without modifying code. |
| `jobstreet_cli.py` | Core Playwright scraper engine and DOM card extractor. |
| `jobs.sqlite` | Shared SQLite database storing unique jobs and crawl history. |
| `run_summary.json` | Generated after each cycle containing run statistics and remote job counts. |

---

## 3. Search Pairs Configuration (`searches.json`)

To add, remove, or modify job searches, edit `searches.json` directly:

```json
[
  {
    "keyword": "PHP Developer",
    "location": "Philippines"
  },
  {
    "keyword": "Laravel Developer",
    "location": "Philippines"
  },
  {
    "keyword": "Full Stack Developer",
    "location": "Philippines"
  },
  {
    "keyword": ".NET Developer",
    "location": "Philippines"
  },
  {
    "keyword": "C# Developer",
    "location": "Philippines"
  }
]
```

---

## 4. Key Mechanisms Implemented

### A. Automatic `is_remote` Backfill
* After each search pair finishes, the worker checks `jobs.sqlite`.
* If the `is_remote` column does not exist, it runs `ALTER TABLE jobs ADD COLUMN is_remote BOOLEAN DEFAULT 0`.
* It updates rows where `location` contains `"Remote"`, `"Work From Home"`, or `"WFH"` (case-insensitive) to `is_remote = 1`.

### B. Anti-Bot Jitter & Rate Limiting
* **Between pagination requests**: `2.5s – 4.0s` jitter delay.
* **Between different search pairs**: `10.0s – 20.0s` jitter delay so searches do not trigger multi-URL rapid hammering patterns on Cloudflare.

### C. Output Summary (`run_summary.json`)
At the end of each pass, the worker saves a summary file:
```json
{
  "timestamp": "2026-10-13T15:00:00.000000",
  "duration_seconds": 45.2,
  "searches_configured": 5,
  "total_scanned": 150,
  "new_postings_found": 12,
  "new_remote_postings_found": 4,
  "total_db_records": 150,
  "total_db_remote_records": 35
}
```

---

## 5. How to Run Manually

Open PowerShell or Command Prompt, navigate to your Desktop, and run any of the following commands:

```powershell
cd "C:\Users\ASUS\OneDrive\Desktop"
```

### Option A: Run all search pairs once and exit
```powershell
python jobstreet_worker.py --once
```

### Option B: Run in a continuous loop every N hours
```powershell
# Run every 6 hours (press Ctrl+C anytime to stop cleanly)
python jobstreet_worker.py --loop 6
```

### Option C: Deep scrape full job descriptions
```powershell
# Crawl deep detail pages to extract complete job description text
python jobstreet_worker.py --once --details
```

### Option D: Custom parameters
```powershell
# Scrape up to 4 pages per pair into a custom database
python jobstreet_worker.py --once --pages 4 --db "my_jobs.sqlite"
```

---

## 6. Complete Source Code Reference

### `jobstreet_worker.py`
```python
import argparse
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

# Import crawler from jobstreet_cli
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jobstreet_cli import run_crawler, DEFAULT_DB_FILE

DEFAULT_CONFIG_FILE = "searches.json"
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

def run_worker_cycle(config_path, db_path, max_pages_per_pair, fetch_details=False):
    """Execute one full pass through all configured search pairs."""
    searches = load_searches_config(config_path)
    cycle_start = time.time()
    start_iso = datetime.now().isoformat()

    print(f"\n========================================================")
    print(f"[*] Starting Worker Cycle at {start_iso}")
    print(f"[*] Loaded {len(searches)} search pairs from: {config_path}")
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
        all_new_jobs.extend(new_jobs)

        backfill_is_remote(db_path)

        if idx < len(searches):
            pair_delay = random.uniform(10.0, 20.0)
            print(f"[*] Pair complete. Inter-pair delay: {pair_delay:.2f}s before next search...")
            time.sleep(pair_delay)

    backfill_is_remote(db_path)

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
        "total_db_remote_records": total_remote_in_db
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
    print(f"========================================================")

    return summary_data

def main():
    parser = argparse.ArgumentParser(description="JobStreet Multi-Search Worker Wrapper")
    parser.add_argument("--once", action="store_true", help="Run all search pairs one time and exit")
    parser.add_argument("--loop", type=float, metavar="HOURS", help="Repeat the full pair list every N hours")
    parser.add_argument("--config", default=DEFAULT_CONFIG_FILE, help="Path to search pairs config (default: searches.json)")
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
                run_worker_cycle(args.config, args.db, args.pages, args.details)
                print(f"\n[*] Cycle #{cycle_count} finished. Sleeping for {args.loop} hour(s)...")
                cycle_count += 1
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            print("\n[*] Worker loop stopped by user (Ctrl+C). Exiting cleanly.")
            sys.exit(0)
    else:
        run_worker_cycle(args.config, args.db, args.pages, args.details)

if __name__ == "__main__":
    main()
```
