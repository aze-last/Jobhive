import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime

import docx
from vault_manager import create_or_update_job_note, parse_frontmatter

BASE_RESUME_REL = os.path.join("Resumes", "Base", "resume_base.docx")
TAILORED_RESUME_DIR_REL = os.path.join("Resumes", "Tailored")

def extract_base_resume_text(base_docx_path):
    """
    Extract text from the base resume docx using pandoc (primary)
    or python-docx (fallback). Never modifies the base file.
    """
    if not os.path.exists(base_docx_path):
        return None, f"Base resume not found at: {base_docx_path}"

    # 1. Try Pandoc (best markdown conversion)
    try:
        proc = subprocess.run(
            ["pandoc", "-f", "docx", "-t", "markdown", base_docx_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            check=True
        )
        if proc.stdout.strip():
            return proc.stdout.strip(), None
    except Exception as e:
        # Fallback to python-docx if pandoc fails
        pass

    # 2. Fallback to python-docx
    try:
        doc = docx.Document(base_docx_path)
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    paragraphs.append(row_text)
        return "\n\n".join(paragraphs), None
    except Exception as e:
        return None, f"Failed to extract text with docx reader: {e}"

def load_llm_config():
    """Load LLM configuration from llm_config.json or environment variables."""
    cfg = {
        "base_url": os.environ.get("OPENAI_BASE_URL") or os.environ.get("XKIRO_BASE_URL") or "https://api.xkiro.com/v1",
        "api_key": os.environ.get("OPENAI_API_KEY") or os.environ.get("XKIRO_API_KEY") or "",
        "model": os.environ.get("LLM_MODEL") or "qwen/qwen3.7-flash:free"
    }
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm_config.json")
    if os.path.exists(config_file):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    cfg.update({k: v for k, v in loaded.items() if v})
        except Exception as e:
            print(f"[!] Warning reading llm_config.json: {e}")
    return cfg

def call_llm_tailor(base_resume_text, job_title, company, job_description):
    """
    Calls LLM using configured xkiro / OpenAI / Anthropic endpoint.
    Returns structured JSON containing tailored summary, ordered bullets, and match notes.
    """
    llm_cfg = load_llm_config()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    api_key = llm_cfg.get("api_key")
    base_url = llm_cfg.get("base_url", "https://api.xkiro.com/v1").rstrip("/")
    model = llm_cfg.get("model", "qwen/qwen3.7-flash:free")

    if not api_key and not anthropic_key:
        return None, "No API key found. Please set api_key in llm_config.json or set ANTHROPIC_API_KEY / OPENAI_API_KEY."

    system_prompt = (
        "You are an expert ATS-compliant resume strategist and technical recruiter.\n"
        "Your objective is to deeply tailor the candidate's base resume for the target job posting.\n\n"
        "STRICT GUARDRAILS:\n"
        "1. NEVER invent, fabricate, or exaggerate experience, companies, job titles, degrees, or skills not present in the candidate's base resume.\n"
        "2. If the posting mentions requirements that the candidate does not possess, LEAVE THEM OUT. Do not make claims about them. List them as gaps in match_notes.\n"
        "3. Provide a tailored target headline (subtitle) for the candidate (e.g. 'Junior C# .NET Developer | Desktop & Windows Applications', 'PHP & Laravel Developer | Full-Stack Web', 'Full-Stack Developer | Laravel • C# • REST APIs') using only factual qualifications.\n"
        "4. Rewrite the Professional Summary (1 concise paragraph) connecting candidate's exact production systems and qualifications directly to the target role's priorities.\n"
        "5. Prioritize and reorder the Technical Skills categories and items to put the target role's core stack first.\n"
        "6. Reorder the candidate's 4 freelance project bullet points (eKalinga+, Car-Showroom, eKalingaWeb, Additional projects) so the project most aligned with this target job is listed first. Keep all facts completely true to the base resume.\n"
        "7. Output strictly valid JSON with no markdown backticks, conforming to the exact schema requested."
    )

    user_prompt = f"""Target Job Title: {job_title}
Target Company: {company}

Target Job Details:
{job_description}

Candidate Base Resume:
{base_resume_text}

Provide your response in strictly valid JSON with this exact schema:
{{
  "tailored_headline": "Professional title/headline tailored for this role (e.g. 'C# .NET Developer | Desktop Systems' or 'PHP / Laravel Developer | Web Applications')",
  "tailored_summary": "1 focused paragraph connecting candidate's exact experience to target role",
  "match_notes": [
    "Bullet 1: Primary overlap emphasized",
    "Bullet 2: Specific systems/accomplishments foregrounded",
    "Bullet 3: Notable requirement gap identified (what the role seeks that the candidate lacks)",
    "Bullet 4: Strategic angle taken"
  ],
  "tailored_skills": {{
    "backend": "Backend skills reordered with target job technologies first",
    "frontend": "Frontend skills reordered with target job technologies first",
    "data": "Data skills reordered with target job technologies first",
    "other": "Other skills reordered with target job technologies first"
  }},
  "featured_project_name": "Name of the project from base resume featured first",
  "reordered_experience_bullets": [
    "Most relevant project bullet from base resume",
    "Second most relevant project bullet",
    "Third project bullet",
    "Fourth project bullet"
  ]
}}"""

    raw_text = None

    # Option A: Anthropic direct if ANTHROPIC_API_KEY is explicitly set
    if anthropic_key and not api_key:
        url = "https://api.anthropic.com/v1/messages"
        payload = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 2500,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt}
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_text = data["content"][0]["text"]
        except Exception as e:
            return None, f"Anthropic API call failed: {e}"

    # Option B: xkiro / OpenAI-compatible endpoint
    else:
        url = f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"}
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                raw_text = data["choices"][0]["message"]["content"]
        except Exception as e:
            return None, f"LLM API call ({base_url}) failed: {e}"

    # Clean potential markdown wrapping
    cleaned = re.sub(r'^```json\s*', '', raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned.strip())

    try:
        parsed = json.loads(cleaned)
        return parsed, None
    except json.JSONDecodeError as err:
        return None, f"Failed to parse LLM response as JSON: {err}. Raw: {raw_text[:200]}"

def rebuild_tailored_docx(base_docx_path, output_docx_path, tailored_data):
    """
    Clones base_resume.docx layout/formatting and injects:
    1. Tailored headline/subtitle (paragraph 1)
    2. Tailored summary (paragraph 5)
    3. Tailored technical skills (paragraphs 7-10)
    4. Reordered project bullets under Experience (paragraphs 14-17)
    NEVER modifies base_resume.docx.
    """
    os.makedirs(os.path.dirname(output_docx_path), exist_ok=True)
    doc = docx.Document(base_docx_path)

    # 1. Headline (Subtitle)
    tailored_headline = tailored_data.get("tailored_headline")
    if tailored_headline and len(doc.paragraphs) > 1:
        p1 = doc.paragraphs[1]
        p1.text = tailored_headline
        for r in p1.runs:
            r.font.size = docx.shared.Pt(11)

    # 2. Professional Summary
    tailored_summary = tailored_data.get("tailored_summary")
    summary_found = False
    for i, p in enumerate(doc.paragraphs):
        text_lower = p.text.strip().lower()
        if any(h == text_lower for h in ["summary", "professional summary", "profile", "about me"]):
            if i + 1 < len(doc.paragraphs) and tailored_summary:
                doc.paragraphs[i + 1].text = tailored_summary
                for r in doc.paragraphs[i + 1].runs:
                    r.font.size = docx.shared.Pt(10)
                summary_found = True
                break

    if not summary_found and tailored_summary and len(doc.paragraphs) > 5:
        doc.paragraphs[5].text = tailored_summary
        for r in doc.paragraphs[5].runs:
            r.font.size = docx.shared.Pt(10)

    # 3. Technical Skills (Paragraphs 7-10)
    skills = tailored_data.get("tailored_skills", {})
    if isinstance(skills, dict):
        cat_map = [
            (7, "Backend", skills.get("backend")),
            (8, "Frontend", skills.get("frontend")),
            (9, "Data", skills.get("data")),
            (10, "Other", skills.get("other"))
        ]
        for p_idx, cat_label, cat_val in cat_map:
            if cat_val and len(doc.paragraphs) > p_idx:
                doc.paragraphs[p_idx].text = f"{cat_label}: {cat_val}"
                for r in doc.paragraphs[p_idx].runs:
                    r.font.size = docx.shared.Pt(10)

    # 4. Experience Bullets (Paragraphs 14-17)
    exp_bullets = tailored_data.get("reordered_experience_bullets", [])
    if isinstance(exp_bullets, list) and len(exp_bullets) >= 4 and len(doc.paragraphs) > 17:
        for idx, bullet_text in enumerate(exp_bullets[:4]):
            if bullet_text and isinstance(bullet_text, str):
                p = doc.paragraphs[14 + idx]
                p.text = bullet_text
                for r in p.runs:
                    r.font.size = docx.shared.Pt(10)

    try:
        doc.save(output_docx_path)
    except PermissionError:
        print(f"  [!] Warning: {output_docx_path} is currently open in Microsoft Word.")
        alt_docx = output_docx_path.replace(".docx", "_new.docx")
        try:
            doc.save(alt_docx)
            print(f"  [+] Saved updated document to: {alt_docx}")
        except Exception:
            pass

    return True

def tailor_job(vault_dir, job_data):
    """
    Tailors a single job:
    1. Reads base resume.
    2. Calls LLM with strict no-fabrication instructions.
    3. Generates tailored docx in Resumes/Tailored/<job_id>_resume.docx.
    4. Updates the Obsidian job note with match notes and tailored link.
    """
    job_id = str(job_data.get("job_id"))
    title = job_data.get("title", "Untitled Job")
    company = job_data.get("company", "Company")
    desc = job_data.get("full_description") or job_data.get("summary") or ""

    base_docx = os.path.join(vault_dir, BASE_RESUME_REL)
    tailored_docx_rel = f"Resumes/Tailored/{job_id}_resume.docx"
    tailored_docx_full = os.path.join(vault_dir, tailored_docx_rel)

    print(f"\n[Tailor Subagent] Processing Job {job_id}: {title} @ {company}")

    # Check base resume
    base_text, err = extract_base_resume_text(base_docx)
    if err:
        print(f"  [!] {err}")
        match_bullets = [
            f"- **Tailoring Skipped:** {err}",
            "- Place your resume at `JobHiveVault/Resumes/Base/resume_base.docx` and re-run to generate tailored version."
        ]
        create_or_update_job_note(
            vault_dir=vault_dir,
            job=job_data,
            match_notes="\n".join(match_bullets),
            status="pending-base-resume",
            tailored_resume_rel=tailored_docx_rel
        )
        return False, err

    # Call LLM
    llm_result, api_err = call_llm_tailor(base_text, title, company, desc)
    if api_err:
        print(f"  [!] Tailor failed: {api_err}")
        match_bullets = [
            f"- **LLM Tailoring Failed:** {api_err}",
            "- Ensure `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is properly configured.",
            "- Job note preserved for manual retry."
        ]
        create_or_update_job_note(
            vault_dir=vault_dir,
            job=job_data,
            match_notes="\n".join(match_bullets),
            status="tailor-failed",
            tailored_resume_rel=tailored_docx_rel
        )
        return False, api_err

    # Rebuild docx
    try:
        rebuild_tailored_docx(base_docx, tailored_docx_full, llm_result)
        print(f"  [+] Tailored docx generated at: {tailored_docx_full}")
    except Exception as docx_err:
        print(f"  [!] Docx rebuild error: {docx_err}")
        match_bullets = [f"- **Docx Generation Error:** {docx_err}"]
        create_or_update_job_note(
            vault_dir=vault_dir,
            job=job_data,
            match_notes="\n".join(match_bullets),
            status="tailor-failed",
            tailored_resume_rel=tailored_docx_rel
        )
        return False, str(docx_err)

    # Format match notes
    notes_list = llm_result.get("match_notes", [])
    bullets_formatted = "\n".join(f"- {n.lstrip('- ')}" for n in notes_list)

    # Update note to status "tailored"
    create_or_update_job_note(
        vault_dir=vault_dir,
        job=job_data,
        match_notes=bullets_formatted,
        status="tailored",
        tailored_resume_rel=tailored_docx_rel
    )
    print(f"  [+] Job note updated with match notes and status: tailored")
    return True, None

if __name__ == "__main__":
    default_v = "." if (os.path.isdir("Jobs") or os.path.exists("00-Dashboard.md")) else "JobHiveVault"
    parser = argparse.ArgumentParser(description="Resume Tailor Subagent")
    parser.add_argument("--job-id", help="Target job ID")
    parser.add_argument("--all", action="store_true", help="Re-tailor all jobs in vault")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of jobs to re-tailor")
    parser.add_argument("--vault", default=default_v, help=f"Path to JobHiveVault (default: {default_v})")
    args = parser.parse_args()

    vault_dir = args.vault
    if vault_dir == "JobHiveVault" and not os.path.isdir("JobHiveVault") and (os.path.isdir("Jobs") or os.path.exists("00-Dashboard.md")):
        vault_dir = "."

    jobs_dir = os.path.join(vault_dir, "Jobs")
    if not os.path.exists(jobs_dir):
        print(f"[!] Jobs directory not found at: {jobs_dir}")
        sys.exit(1)

    if args.all:
        note_files = [f for f in os.listdir(jobs_dir) if f.endswith(".md")]
        if args.limit:
            note_files = note_files[:args.limit]
        print(f"[*] Re-tailoring {len(note_files)} jobs in {jobs_dir}...")
        success_count = 0
        for idx, f in enumerate(note_files, start=1):
            target_note = os.path.join(jobs_dir, f)
            with open(target_note, "r", encoding="utf-8") as file:
                meta, body = parse_frontmatter(file.read())
            meta["full_description"] = body
            print(f"\n--- [{idx}/{len(note_files)}] {f} ---")
            ok, _ = tailor_job(vault_dir, meta)
            if ok:
                success_count += 1
        print(f"\n[+] Re-tailoring complete: {success_count}/{len(note_files)} succeeded.")
        # Regenerate dashboard & excel
        try:
            import vault_manager
            vault_manager.regenerate_dashboard(vault_dir)
        except Exception as e:
            print(f"[!] Warning updating dashboard: {e}")
        sys.exit(0)

    elif args.job_id:
        target_note = None
        for f in os.listdir(jobs_dir):
            if f.startswith(f"{args.job_id}-") and f.endswith(".md"):
                target_note = os.path.join(jobs_dir, f)
                break

        if not target_note:
            print(f"[!] No note found for job id {args.job_id} in {jobs_dir}")
            sys.exit(1)

        with open(target_note, "r", encoding="utf-8") as file:
            meta, body = parse_frontmatter(file.read())

        meta["full_description"] = body
        success, error = tailor_job(vault_dir, meta)
        if not success:
            sys.exit(1)
    else:
        parser.print_help()
        sys.exit(1)
