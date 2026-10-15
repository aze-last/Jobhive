import os
import re
from datetime import datetime

def slugify(text, max_len=50):
    if not text:
        return "untitled"
    slug = re.sub(r'[^a-zA-Z0-9]+', '-', text.strip().lower()).strip('-')
    return slug[:max_len].strip('-') or "job"

def get_job_note_path(vault_dir, job_id, title):
    slug = slugify(title)
    filename = f"{job_id}-{slug}.md"
    return os.path.join(vault_dir, "Jobs", filename)

def parse_frontmatter(content):
    """Simple robust YAML frontmatter parser."""
    meta = {}
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            raw_meta = parts[1]
            body = parts[2].strip()
            for line in raw_meta.strip().splitlines():
                if ":" in line:
                    key, val = line.split(":", 1)
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if val.lower() == "true":
                        meta[key] = True
                    elif val.lower() == "false":
                        meta[key] = False
                    else:
                        meta[key] = val
    return meta, body

def create_or_update_job_note(vault_dir, job, match_notes=None, status="new", tailored_resume_rel=None):
    jobs_dir = os.path.join(vault_dir, "Jobs")
    os.makedirs(jobs_dir, exist_ok=True)

    job_id = str(job.get("job_id"))
    title = job.get("title", "Untitled Job")
    note_path = get_job_note_path(vault_dir, job_id, title)

    existing_meta = {}
    existing_desc = ""
    existing_match = ""

    if os.path.exists(note_path):
        with open(note_path, "r", encoding="utf-8") as f:
            existing_meta, existing_body = parse_frontmatter(f.read())
            # Extract existing sections if any
            if "## Job Description" in existing_body:
                parts = existing_body.split("## Job Description", 1)
                if "## Match Notes" in parts[1]:
                    desc_part, match_part = parts[1].split("## Match Notes", 1)
                    existing_desc = desc_part.strip()
                    existing_match = match_part.strip()
                else:
                    existing_desc = parts[1].strip()

    is_remote_val = bool(job.get("is_remote", False))
    if "is_remote" in existing_meta:
        is_remote_val = bool(existing_meta["is_remote"])

    date_found = existing_meta.get("date_found") or job.get("first_seen") or datetime.now().isoformat()
    final_status = status if status else existing_meta.get("status", "new")
    
    rel_resume = tailored_resume_rel or existing_meta.get("tailored_resume")
    if not rel_resume and job_id:
        rel_resume = f"Resumes/Tailored/{job_id}_resume.docx"

    # Merge description
    desc = job.get("full_description") or job.get("summary") or existing_desc or "No description available."
    final_match = match_notes if match_notes is not None else (existing_match or "- Pending analysis by Resume Tailor subagent.")

    frontmatter = f"""---
job_id: "{job_id}"
title: "{title}"
company: "{job.get('company', 'Private Advertiser')}"
location: "{job.get('location', '')}"
salary: "{job.get('salary') or 'Not disclosed'}"
posted_date: "{job.get('posted_date', '')}"
url: "{job.get('url', '')}"
is_remote: {str(is_remote_val).lower()}
status: "{final_status}"
tailored_resume: "{rel_resume}"
date_found: "{date_found}"
---"""

    body = f"""# {title}

- **Company:** {job.get('company', 'Private Advertiser')}
- **Location:** {job.get('location', '')}
- **Salary:** {job.get('salary') or 'Not disclosed'}
- **URL:** [{job.get('url', 'Job Listing')}]({job.get('url', '')})
- **Status:** `{final_status}`
- **Tailored Resume:** `{rel_resume}`

## Job Description
{desc}

## Match Notes
{final_match}
"""

    full_content = f"{frontmatter}\n\n{body}"
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(full_content)

    return note_path

def regenerate_dashboard(vault_dir):
    jobs_dir = os.path.join(vault_dir, "Jobs")
    dashboard_path = os.path.join(vault_dir, "00-Dashboard.md")

    if not os.path.exists(jobs_dir):
        os.makedirs(jobs_dir, exist_ok=True)

    notes = []
    for f in os.listdir(jobs_dir):
        if f.endswith(".md"):
            p = os.path.join(jobs_dir, f)
            try:
                with open(p, "r", encoding="utf-8") as file:
                    meta, _ = parse_frontmatter(file.read())
                    meta["_filename"] = f
                    meta["_path"] = p
                    notes.append(meta)
            except Exception:
                continue

    # Sort remote jobs to top, then by date_found descending
    def sort_key(n):
        is_rem = 1 if n.get("is_remote") is True else 0
        date_str = n.get("date_found", "")
        return (is_rem, date_str)

    notes.sort(key=sort_key, reverse=True)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_count = len(notes)
    remote_count = sum(1 for n in notes if n.get("is_remote") is True)
    tailored_count = sum(1 for n in notes if n.get("status") == "tailored")

    lines = [
        "# 🐝 JobHive Dashboard",
        f"*Last updated: {now_str}*",
        "",
        f"> **Total Tracked:** {total_count} | **Remote / WFH:** {remote_count} | **Tailored Resumes:** {tailored_count}",
        "",
        "| Title | Company | Remote? | Status | Date Found | Obsidian Link |",
        "| :--- | :--- | :---: | :---: | :---: | :--- |"
    ]

    if not notes:
        lines.append("| *No jobs tracked yet* | - | - | - | - | - |")
    else:
        for n in notes:
            title = (n.get("title") or "Untitled").replace("|", "-")
            company = (n.get("company") or "Unknown").replace("|", "-")
            is_rem = "🌐 Yes" if n.get("is_remote") is True else "🏢 No"
            status = n.get("status", "new")
            date_found = (n.get("date_found") or "")[:10]
            fname = n["_filename"]
            link = f"[[Jobs/{fname}|Open Note]]"
            lines.append(f"| {title} | {company} | {is_rem} | `{status}` | {date_found} | {link} |")

    lines.append("")
    with open(dashboard_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Auto-generate / refresh Excel tracker
    export_to_excel(vault_dir)

    return dashboard_path

def export_to_excel(vault_dir=".", output_filename="JobHive_Jobs.xlsx"):
    """
    Exports all tracked jobs to an Excel file with columns matching user specification:
    Date Found | Job Title | Company | Source Site | Job Link | Resume | Status
    Includes dropdown validation and conditional formatting:
    - Applied (Orange)
    - Interview (Green)
    - Decline (Red)
    Preserves any existing statuses previously set in the workbook.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.worksheet.datavalidation import DataValidation
        from openpyxl.formatting.rule import CellIsRule
    except ImportError:
        return None

    jobs_dir = os.path.join(vault_dir, "Jobs")
    if not os.path.exists(jobs_dir):
        return None

    out_path = os.path.join(vault_dir, output_filename)

    # 1. Read existing statuses if file already exists to preserve user input
    existing_statuses = {}
    if os.path.exists(out_path):
        try:
            old_wb = openpyxl.load_workbook(out_path, data_only=True)
            old_ws = old_wb.active
            for r in range(2, old_ws.max_row + 1):
                link_val = old_ws.cell(row=r, column=5).value
                status_val = old_ws.cell(row=r, column=7).value
                if not status_val or status_val not in ["Applied", "Interview", "Decline"]:
                    status_val = old_ws.cell(row=r, column=8).value
                if link_val and status_val:
                    existing_statuses[str(link_val).strip()] = str(status_val).strip()
        except Exception:
            pass

    notes = []
    for f in os.listdir(jobs_dir):
        if f.endswith(".md"):
            p = os.path.join(jobs_dir, f)
            try:
                with open(p, "r", encoding="utf-8") as file:
                    meta, _ = parse_frontmatter(file.read())
                    notes.append(meta)
            except Exception:
                continue

    def sort_key(n):
        is_rem = 1 if n.get("is_remote") is True else 0
        date_str = n.get("date_found", "")
        return (is_rem, date_str)

    notes.sort(key=sort_key, reverse=True)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Jobs & Resumes"

    header_fill = PatternFill(start_color="1B4E79", end_color="1B4E79", fill_type="solid")
    header_font = Font(name="Segoe UI", size=11, bold=True, color="FFFFFF")
    header_align = Alignment(horizontal="center", vertical="center")

    data_font = Font(name="Segoe UI", size=10, italic=True)
    link_font = Font(name="Segoe UI", size=10, italic=True, color="0563C1", underline="single")
    status_font = Font(name="Segoe UI", size=10, bold=True)

    thin_border_side = Side(style="thin", color="D9D9D9")
    thin_border = Border(left=thin_border_side, right=thin_border_side, top=thin_border_side, bottom=thin_border_side)

    headers = ["Date Found", "Job Title", "Company", "Source Site", "Job Link", "Resume", "Cover Letter", "Status"]
    ws.append(headers)
    ws.row_dimensions[1].height = 26

    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        cell.border = thin_border

    total_rows = len(notes) + 1

    for r_idx, n in enumerate(notes, start=2):
        job_id = str(n.get("job_id", ""))
        title = n.get("title", "Untitled")
        company = n.get("company", "Unknown")
        date_found = (n.get("date_found") or "")[:10]
        url = n.get("url") or ""
        resume_rel = f"Resumes/Tailored/{job_id}_resume.docx"
        cl_rel = f"Resumes/Cover_Letters/{job_id}_cover_letter.docx"
        has_cl = os.path.exists(os.path.join(vault_dir, cl_rel))

        ws.row_dimensions[r_idx].height = 20

        # Col A: Date Found
        c_a = ws.cell(row=r_idx, column=1, value=date_found)
        c_a.font = data_font
        c_a.alignment = Alignment(horizontal="center", vertical="center")
        c_a.border = thin_border

        # Col B: Job Title
        c_b = ws.cell(row=r_idx, column=2, value=title)
        c_b.font = data_font
        c_b.alignment = Alignment(horizontal="left", vertical="center")
        c_b.border = thin_border

        # Col C: Company
        c_c = ws.cell(row=r_idx, column=3, value=company)
        c_c.font = data_font
        c_c.alignment = Alignment(horizontal="left", vertical="center")
        c_c.border = thin_border

        # Col D: Source Site
        c_d = ws.cell(row=r_idx, column=4, value="JobStreet")
        c_d.font = data_font
        c_d.alignment = Alignment(horizontal="center", vertical="center")
        c_d.border = thin_border

        # Col E: Job Link
        c_e = ws.cell(row=r_idx, column=5, value=url)
        c_e.font = link_font
        if url:
            c_e.hyperlink = url
        c_e.alignment = Alignment(horizontal="left", vertical="center")
        c_e.border = thin_border

        # Col F: Resume
        c_f = ws.cell(row=r_idx, column=6, value=f"{job_id}_resume.docx")
        c_f.font = link_font
        c_f.hyperlink = resume_rel
        c_f.alignment = Alignment(horizontal="left", vertical="center")
        c_f.border = thin_border

        # Col G: Cover Letter (inline with job_id and resume)
        c_g = ws.cell(row=r_idx, column=7, value=f"{job_id}_cover_letter.docx")
        c_g.font = link_font
        c_g.hyperlink = cl_rel
        c_g.alignment = Alignment(horizontal="left", vertical="center")
        c_g.border = thin_border

        # Col H: Status (Applied / Interview / Decline)
        prior_status = existing_statuses.get(url, "")
        c_h = ws.cell(row=r_idx, column=8, value=prior_status)
        c_h.font = status_font
        c_h.alignment = Alignment(horizontal="center", vertical="center")
        c_h.border = thin_border

    # Data Validation Dropdown for Column H
    dv = DataValidation(type="list", formula1='"Applied,Interview,Decline"', allow_blank=True)
    dv.error = "Please select a status from the dropdown: Applied, Interview, or Decline."
    dv.errorTitle = "Invalid Status"
    dv.prompt = "Select status: Applied, Interview, or Decline"
    dv.promptTitle = "Application Status"
    ws.add_data_validation(dv)
    dv.add(f"H2:H{total_rows}")

    # Conditional Formatting for Column H:
    # Applied -> Orange
    fill_applied = PatternFill(start_color="FFE0B2", end_color="FFE0B2", fill_type="solid")
    font_applied = Font(name="Segoe UI", size=10, bold=True, color="B74700")
    rule_applied = CellIsRule(operator="equal", formula=['"Applied"'], fill=fill_applied, font=font_applied)

    # Interview -> Green
    fill_interview = PatternFill(start_color="C8E6C9", end_color="C8E6C9", fill_type="solid")
    font_interview = Font(name="Segoe UI", size=10, bold=True, color="1B5E20")
    rule_interview = CellIsRule(operator="equal", formula=['"Interview"'], fill=fill_interview, font=font_interview)

    # Decline -> Red
    fill_decline = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
    font_decline = Font(name="Segoe UI", size=10, bold=True, color="B71C1C")
    rule_decline = CellIsRule(operator="equal", formula=['"Decline"'], fill=fill_decline, font=font_decline)

    cf_range = f"H2:H{total_rows}"
    ws.conditional_formatting.add(cf_range, rule_applied)
    ws.conditional_formatting.add(cf_range, rule_interview)
    ws.conditional_formatting.add(cf_range, rule_decline)

    # Set column widths
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 40
    ws.column_dimensions["C"].width = 35
    ws.column_dimensions["D"].width = 15
    ws.column_dimensions["E"].width = 50
    ws.column_dimensions["F"].width = 28
    ws.column_dimensions["G"].width = 32
    ws.column_dimensions["H"].width = 18

    if ws.views.sheetView:
        ws.views.sheetView[0].showGridLines = True

    try:
        wb.save(out_path)
    except PermissionError:
        print(f"[!] Warning: Cannot save {out_path} because it is currently open in Excel. Close Excel to allow updates.")
        try:
            alt_path = os.path.join(vault_dir, "JobHive_Jobs_updated.xlsx")
            wb.save(alt_path)
            print(f"[+] Saved updated copy to {alt_path}")
        except Exception:
            pass

    # Also update Desktop copy if present
    desktop_copy = os.path.join(os.path.dirname(os.path.abspath(vault_dir)), output_filename)
    if os.path.abspath(vault_dir) != os.path.abspath(os.path.dirname(os.path.abspath(vault_dir))):
        try:
            import shutil
            shutil.copy2(out_path, desktop_copy)
        except Exception:
            pass

    return out_path
