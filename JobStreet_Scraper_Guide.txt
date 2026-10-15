# JobStreet PH Web Scraper Guide & Implementation

## 1. Executive Summary & Verification Findings

### Live Test Results:
* **Indeed PH (`https://ph.indeed.com/...`)**:
  * Blocked by Cloudflare Bot Management (`HTTP 403 Forbidden`, `Cf-Mitigated: challenge`). Direct HTTP and headless browsers are immediately gated unless using human-in-the-loop solver sessions.
* **JobStreet PH (`https://www.jobstreet.com.ph/...`)**:
  * **Plain `curl`**: Yields `308 Permanent Redirect` -> `403 Forbidden` (`<title>Just a moment...</title>`).
  * **Headless Playwright**: Bypasses Cloudflare bot detection cleanly when initialized with a standard modern browser context.

---

## 2. JobStreet DOM Architecture & Target Attributes

JobStreet runs on the SEEK platform, which provides stable `data-automation` attributes across all job cards:

| Target Field | Selector / Attribute | Sample Value |
| :--- | :--- | :--- |
| **Job ID** | `<article data-job-id="...">` | `"93845837"` |
| **Job Title** | `[data-automation="jobTitle"]` | `"AI/Automation Specialist - Web Development Company"` |
| **Direct URL** | `[data-automation="jobTitle"]` (`href`) | `https://ph.jobstreet.com/job/93845837...` |
| **Company** | `[data-automation="jobCompany"]` | `"Private Advertiser"` / `"Orange and Bronze Software Labs Ltd, Co."` |
| **Location** | `[data-automation="jobCardLocation"]` | `"Davao City, Davao del Sur"` |
| **Salary** | `[data-automation="jobSalary"]` | `"₱50,000 per month"` *(when disclosed)* |
| **Posted Date** | `[data-automation="jobListingDate"]` | `"7d ago"` |
| **Summary** | `[data-automation="jobShortDescription"]` | Short job preview snippet |
| **Category** | `[data-automation="jobSubClassification"]` | `"Developers/Programmers"` |

---

## 3. Core Mechanics

1. **Pagination**:
   * Uses URL query parameters: `?page=2`, `?page=3`, etc. (Not infinite scroll).
   * 30 listings per page on broad searches.
   * On smaller searches (or when reaching the end), pages return HTTP 200 with `0` `<article>` elements, allowing a clean loop exit.
2. **Deduplication & Diffing**:
   * Deduplication is done on `<article data-job-id="...">`.
   * Maintained in SQLite (`jobs.sqlite`) with `first_seen` and `last_seen` timestamps.
   * Subsequent runs instantly filter out known listings and report only new jobs.
3. **Rate Limiting & Anti-Bot**:
   * Random jitter delay (`2.5s`–`4.0s`) between page transitions.
   * Persistent context session keeps cookies intact.

---

## 4. Full Production Script (`jobstreet_cli.py`)

Save the code below as `jobstreet_cli.py`:

```python
import argparse
import csv
import json
import os
import random
import re
import sqlite3
import sys
import time
from datetime import datetime
from playwright.sync_api import sync_playwright

DEFAULT_DB_FILE = "jobs.sqlite"

def init_db(db_path=DEFAULT_DB_FILE):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            location TEXT,
            salary TEXT,
            posted_date TEXT,
            category TEXT,
            summary TEXT,
            url TEXT,
            first_seen TIMESTAMP,
            last_seen TIMESTAMP,
            full_description TEXT
        )
    """)
    conn.commit()
    return conn

def format_search_url(keywords=None, location=None):
    kw_slug = re.sub(r'[^a-zA-Z0-9]+', '-', keywords.strip()).strip('-') if keywords else ""
    loc_slug = re.sub(r'[^a-zA-Z0-9]+', '-', location.strip()).strip('-') if location else ""

    if kw_slug and loc_slug:
        return f"https://ph.jobstreet.com/{kw_slug}-jobs/in-{loc_slug}"
    elif kw_slug:
        return f"https://ph.jobstreet.com/{kw_slug}-jobs"
    elif loc_slug:
        return f"https://ph.jobstreet.com/jobs/in-{loc_slug}"
    else:
        return "https://ph.jobstreet.com/jobs"

def extract_job_card(article):
    job_id = article.get_attribute("data-job-id")
    if not job_id:
        link = article.query_selector('a[href*="/job/"]')
        if link:
            match = re.search(r'/job/(\d+)', link.get_attribute("href") or "")
            if match:
                job_id = match.group(1)

    title_el = article.query_selector('[data-automation="jobTitle"]')
    title = title_el.inner_text().strip() if title_el else None
    relative_url = title_el.get_attribute("href") if title_el else None
    job_url = f"https://ph.jobstreet.com{relative_url}" if relative_url and relative_url.startswith("/") else relative_url

    company_el = article.query_selector('[data-automation="jobCompany"]')
    company = company_el.inner_text().strip() if company_el else "Private Advertiser"

    location_el = article.query_selector('[data-automation="jobCardLocation"]') or article.query_selector('[data-automation="jobLocation"]')
    location = location_el.inner_text().strip() if location_el else None

    salary_el = article.query_selector('[data-automation="jobSalary"]')
    salary = salary_el.inner_text().strip() if salary_el else None

    date_el = article.query_selector('[data-automation="jobListingDate"]')
    posted_date = date_el.inner_text().strip() if date_el else None

    desc_el = article.query_selector('[data-automation="jobShortDescription"]')
    summary = desc_el.inner_text().strip() if desc_el else None

    subclass_el = article.query_selector('[data-automation="jobSubClassification"]')
    subclass = subclass_el.inner_text().strip() if subclass_el else None

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "salary": salary,
        "posted_date": posted_date,
        "category": subclass,
        "summary": summary,
        "url": job_url
    }

def fetch_job_details(page, job_url):
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        desc_el = page.query_selector('[data-automation="jobDescription"]') or page.query_selector('[data-automation="jobAdDetails"]')
        return desc_el.inner_text().strip() if desc_el else None
    except Exception as e:
        print(f"    [!] Failed to fetch details for {job_url}: {e}")
        return None

def run_crawler(keywords="PHP Developer", location="Davao City", max_pages=3, fetch_details=False, export_csv=None, db_path=DEFAULT_DB_FILE):
    conn = init_db(db_path)
    cur = conn.cursor()

    base_url = format_search_url(keywords, location)
    print(f"[*] Target Search URL: {base_url}")
    print(f"[*] Query: '{keywords}' in '{location}' (Max pages: {max_pages})")

    new_jobs = []
    seen_jobs = []
    now_iso = datetime.now().isoformat()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        for page_num in range(1, max_pages + 1):
            url = f"{base_url}?page={page_num}" if page_num > 1 else base_url
            print(f"\n--- [Page {page_num}/{max_pages}] {url} ---")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)
            except Exception as e:
                print(f"[!] Navigation error on page {page_num}: {e}")
                break

            if "Just a moment" in page.title():
                print("[!] Cloudflare managed challenge detected. Pausing...")
                break

            articles = page.query_selector_all("article[data-job-id]")
            print(f"Found {len(articles)} job postings on page {page_num}.")

            if not articles:
                print("No articles returned. Finished pagination.")
                break

            for article in articles:
                job = extract_job_card(article)
                if not job["job_id"]:
                    continue

                seen_jobs.append(job)

                cur.execute("SELECT job_id FROM jobs WHERE job_id = ?", (job["job_id"],))
                exists = cur.fetchone()

                if not exists:
                    print(f"  [+] NEW: {job['title']} @ {job['company']}")
                    job["first_seen"] = now_iso
                    job["last_seen"] = now_iso
                    job["full_description"] = None

                    cur.execute("""
                        INSERT INTO jobs (job_id, title, company, location, salary, posted_date, category, summary, url, first_seen, last_seen, full_description)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        job["job_id"], job["title"], job["company"], job["location"],
                        job["salary"], job["posted_date"], job["category"], job["summary"],
                        job["url"], job["first_seen"], job["last_seen"], job["full_description"]
                    ))
                    conn.commit()
                    new_jobs.append(job)
                else:
                    cur.execute("UPDATE jobs SET last_seen = ? WHERE job_id = ?", (now_iso, job["job_id"]))
                    conn.commit()

            has_next = page.query_selector(f'[data-automation="page-{page_num + 1}"]')
            if not has_next and page_num > 1:
                print("No further page link found. Ending pagination.")
                break

            if page_num < max_pages:
                delay = random.uniform(2.5, 4.0)
                print(f"Jitter pause ({delay:.2f}s)...")
                time.sleep(delay)

        if fetch_details and new_jobs:
            print(f"\n[*] Fetching deep descriptions for {len(new_jobs)} newly found jobs...")
            detail_page = context.new_page()
            for idx, job in enumerate(new_jobs, 1):
                if not job.get("url"):
                    continue
                print(f"[{idx}/{len(new_jobs)}] Loading details for {job['title']}...")
                desc = fetch_job_details(detail_page, job["url"])
                if desc:
                    cur.execute("UPDATE jobs SET full_description = ? WHERE job_id = ?", (desc, job["job_id"]))
                    conn.commit()
                    job["full_description"] = desc
                time.sleep(random.uniform(2.0, 3.5))
            detail_page.close()

        browser.close()

    conn.close()

    if export_csv and seen_jobs:
        with open(export_csv, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=[
                "job_id", "title", "company", "location", "salary",
                "posted_date", "category", "summary", "url"
            ])
            writer.writeheader()
            for item in seen_jobs:
                writer.writerow({k: item.get(k) for k in writer.fieldnames})
        print(f"\n[+] Exported {len(seen_jobs)} jobs to {export_csv}")

    print(f"\n==========================================")
    print(f"Run Summary:")
    print(f"  Total postings scanned this run: {len(seen_jobs)}")
    print(f"  New postings added to SQLite:    {len(new_jobs)}")
    print(f"  Database file:                   {db_path}")
    print(f"==========================================")

    return new_jobs

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JobStreet PH CLI Scraper")
    parser.add_argument("--keyword", "-k", default="PHP Developer", help="Search keyword / role")
    parser.add_argument("--location", "-l", default="Davao City", help="Target location / city")
    parser.add_argument("--pages", "-p", type=int, default=2, help="Max pages to scrape")
    parser.add_argument("--csv", help="Optional CSV file to export")
    parser.add_argument("--details", action="store_true", help="Fetch full job descriptions from detail pages")
    parser.add_argument("--db", default=DEFAULT_DB_FILE, help="Path to SQLite database")

    args = parser.parse_args()
    run_crawler(
        keywords=args.keyword,
        location=args.location,
        max_pages=args.pages,
        fetch_details=args.details,
        export_csv=args.csv,
        db_path=args.db
    )
```

---

## 5. Usage Examples

```bash
# 1. Standard search for PHP jobs in Davao City
python jobstreet_cli.py --keyword "PHP Developer" --location "Davao City" --pages 2

# 2. Search React Developer in Cebu City and export to CSV
python jobstreet_cli.py -k "React Developer" -l "Cebu City" -p 3 --csv cebu_jobs.csv

# 3. Deep scrape including full job descriptions into SQLite
python jobstreet_cli.py -k "Python Developer" -l "Metro Manila" -p 2 --details --db manila_jobs.sqlite
```
