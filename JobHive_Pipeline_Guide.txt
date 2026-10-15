# 🐝 JobHive 2-Agent Pipeline & Obsidian Vault Guide

## 1. System Architecture

```
[Agent 1: Scraper & Vault Indexer]
  jobstreet_cli.py + jobstreet_worker.py
      │
      ├─► Scrapes (keyword, location) search pairs from searches.json
      ├─► Persists & diffs against jobs.sqlite (backfilling `is_remote`)
      ├─► Writes Job Notes: JobHiveVault/Jobs/<job_id>-<slug>.md
      ▼
[Agent 2: Resume Tailor Subagent]
  resume_tailor.py
      │
      ├─► Reads JobHiveVault/Resumes/Base/resume_base.docx (Pandoc / docx, read-only)
      ├─► Calls LLM API (Anthropic or OpenAI) with zero-fabrication guardrails
      ├─► Rebuilds docx: JobHiveVault/Resumes/Tailored/<job_id>_resume.docx
      ├─► Injects "Match Notes" (3-5 bullets) and updates note status
      ▼
[Dashboard Indexer]
  vault_manager.py
      └─► Regenerates JobHiveVault/00-Dashboard.md (sorted with Remote on top)
```

---

## 2. File & Directory Inventory (`C:\Users\ASUS\OneDrive\Desktop\`)

```
JobHiveVault/
  ├── 00-Dashboard.md               <- Auto-regenerated index table of all tracked jobs
  ├── Jobs/                         <- Individual job markdown notes (<job_id>-<slug>.md)
  └── Resumes/
      ├── Base/
      │   └── resume_base.docx      <- PLACE YOUR BASE RESUME HERE (Read-only)
      └── Tailored/
          └── <job_id>_resume.docx  <- Generated tailored resumes per matched job

Pipeline Scripts:
  ├── jobstreet_worker.py           <- Orchestrator running the 2-agent pipeline
  ├── resume_tailor.py              <- Resume tailoring agent (Pandoc + docx + LLM)
  ├── vault_manager.py              <- Note creator and dashboard regenerator
  ├── jobstreet_cli.py              <- Playwright scraper core
  ├── searches.json                 <- Configurable search pairs
  └── jobs.sqlite                   <- Shared database storing crawl history
```

---

## 3. Strict Guardrails Enforced

1. **Read-Only Base Resume**: `resume_base.docx` is only read via Pandoc / python-docx. The script will never overwrite or modify it.
2. **Strict No-Fabrication Prompt**: The LLM prompt explicitly forbids inventing or exaggerating skills, tools, or employment not present in your base resume. Unmatched requirements are identified in the Match Notes as gaps rather than fabricated.
3. **Graceful Failures**: If your base resume is missing or the LLM call fails, the job note is preserved with `status: "pending-base-resume"` or `status: "tailor-failed"` for manual review without halting the scraper.
4. **No Auto-Applying**: The system outputs drafted notes and tailored resumes only. You retain full control over where and when you apply.
5. **No Background Scheduling**: Nothing is wired to Task Scheduler or background daemon processes.

---

## 4. Activation Instructions

### Step 1: Place Your Base Resume
Copy your base resume docx file to:
```
C:\Users\ASUS\OneDrive\Desktop\JobHiveVault\Resumes\Base\resume_base.docx
```

### Step 2: LLM API Configuration (`llm_config.json`)

Your `https://api.xkiro.com/v1` endpoint and API key are configured in `llm_config.json`:
```json
{
  "base_url": "https://api.xkiro.com/v1",
  "api_key": "YOUR_API_KEY_HERE",
  "model": "qwen/qwen3.7-flash:free"
}
```
You can change the `"model"` (e.g. `google/gemini-2.5-flash`, `qwen/qwen3.7-flash:free`, etc.) directly in `llm_config.json` at any time without editing any Python code.

---

### Step 3: Run the Full Pipeline Manually

Open PowerShell in your Desktop folder:

```powershell
cd "C:\Users\ASUS\OneDrive\Desktop"

# Run all search pairs once through the full 2-agent pipeline:
python jobstreet_worker.py --once

# Or run in a continuous loop every 6 hours:
python jobstreet_worker.py --loop 6
```
