# 🐝 JobHive — System Instructions & Architecture

> **Notice for Future Agents & CLI Sessions:**  
> This file outlines the project objectives, architecture, agent roles, and activation commands. Read this document before modifying or running any part of this system.

---

## 1. Project Goal & Overview

The goal of this project is an automated, high-precision **Job Aggregation & AI Resume Tailoring Pipeline** focused on software engineering roles in the Philippines (PHP, Laravel, Full-Stack, C#/.NET). 

The pipeline extracts live job postings from JobStreet PH (SEEK platform), flags remote opportunities, organizes them into a structured **Obsidian Vault workspace** (`JobHiveVault/`), and uses an LLM to generate **tailored Word resumes (`.docx`)** aligned with each specific job posting without fabricating any experience.

---

## 2. File Map & Agent Roles

All executable scripts live in the parent directory (`C:\Users\ASUS\OneDrive\Desktop\`), while all persistent output lives inside this vault (`JobHiveVault/`):

```
C:\Users\ASUS\OneDrive\Desktop\
├── jobstreet_worker.py        <- [ORCHESTRATOR] Main entry point. Activate this script.
├── resume_tailor.py           <- [SUBAGENT 2] Resume tailoring engine (LLM + Docx builder).
├── jobstreet_cli.py           <- [SUBAGENT 1] Headless Playwright scraper & DOM extractor.
├── vault_manager.py           <- [VAULT MANAGER] Handles markdown note updates & dashboard.
├── searches.json              <- [CONFIG] Search query pairs (keyword + location).
├── llm_config.json            <- [CONFIG] API base URL, key, and model choice.
├── jobs.sqlite                <- [DATABASE] Deduplication store and run history.
│
└── JobHiveVault/              <- [OBSIDIAN VAULT] Output workspace
    ├── AGENT_INSTRUCTIONS.md  <- This guide.
    ├── 00-Dashboard.md        <- Auto-regenerated status table (remote jobs sorted to top).
    ├── Jobs/                  <- Individual markdown job notes (<job_id>-<slug>.md).
    └── Resumes/
        ├── Base/
        │   └── resume_base.docx      <- Candidate's original master resume (READ-ONLY).
        └── Tailored/
            └── <job_id>_resume.docx  <- Tailored resumes generated per matching job.
```

---

## 3. Which Script Does What?

| Component | File | Responsibilities |
| :--- | :--- | :--- |
| **Worker Orchestrator** | `jobstreet_worker.py` | Loops through `searches.json`, triggers the scraper, applies SQLite remote backfilling, invokes the resume tailor sequentially, and regenerates `00-Dashboard.md`. |
| **Resume Tailor Subagent** | `resume_tailor.py` | Extracts text from `resume_base.docx` using Pandoc, sends structured prompts to the LLM (`api.xkiro.com`), creates `<job_id>_resume.docx`, and writes Match Notes. |
| **Scraper Core** | `jobstreet_cli.py` | Launches headless Playwright Chromium to bypass Cloudflare, parses SEEK `data-automation` attributes, and yields structured job objects. |
| **Vault Manager** | `vault_manager.py` | Handles YAML frontmatter, formats Obsidian links (`[[Jobs/note.md]]`), and rebuilds `00-Dashboard.md`. |

---

## 4. Activation Instructions (How to Achieve the Goal)

### The Command to Activate:
To run one complete pass through all search pairs, open PowerShell in `C:\Users\ASUS\OneDrive\Desktop\` and run:

```powershell
python jobstreet_worker.py --once
```

### To Run Continuously (e.g. Every 6 Hours):
```powershell
python jobstreet_worker.py --loop 6
```

### To Include Full Detail Scraping:
```powershell
python jobstreet_worker.py --once --details
```

---

## 5. Prerequisites & Environment Status

Everything in the local environment is verified and operational:
1. **Python Dependencies**: `playwright`, `python-docx`, `sqlite3` are installed and compiled.
2. **System Tools**: `pandoc 3.9` is present in PATH for docx text extraction.
3. **Master Resume**: Located at `JobHiveVault/Resumes/Base/resume_base.docx`.
4. **LLM Credentials**: Configured in `llm_config.json`:
   * Base URL: `https://api.xkiro.com/v1`
   * Model: `qwen/qwen3.7-flash:free` (can be changed to any supported model)

---

## 6. Critical Guardrails (Must Never Be Violated)

1. **Read-Only Master Resume**: Never write to, modify, or overwrite `JobHiveVault/Resumes/Base/resume_base.docx`. All tailored outputs must go to `JobHiveVault/Resumes/Tailored/<job_id>_resume.docx`.
2. **Zero Fabrication**: The prompt for `resume_tailor.py` strictly forbids inventing or exaggerating skills, tools, or employment not found in the base resume. Unmatched posting requirements must be listed as gaps under `Match Notes`.
3. **Rate Limiting / Anti-Bot**: Maintain jitter delays (`2.5s–4.0s` between pages, `10.0s–20.0s` between search pairs) to avoid triggering Cloudflare blocks.
4. **Draft Only**: The system must never auto-submit applications or contact employers. All outputs are drafts for human review.
5. **Autonomy Directive**: Proceed with tasks directly without prompting for interactive user confirmation unless genuinely blocked by missing dependencies or errors.
