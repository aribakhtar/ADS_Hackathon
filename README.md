# ADS Hack Prior Authorization Extractor

This project extracts payer prior authorization policy attributes from PDF files and fills a submission CSV with structured results, including an access score.

The pipeline does four main things:

1. Reads `Filename` and `Brand` from `submissions.csv`.
2. Converts each matching PDF into markdown.
3. Extracts policy attributes for the target brand and indication.
4. Calculates an access score and writes the results back to the CSV.

## Project Layout

```text
.
|-- driver.py                         # Main script
|-- submissions.csv                   # Input/output submission file
|-- data/
|   |-- raw_pdfs/                     # Put input PDFs here
|   `-- extracted_pdfs_mds/           # Generated markdown files
|-- outputs/
|   `-- large_doc_extractor/          # Generated JSON outputs and logs
|-- processor/
|   `-- pdf_processor.py              # PDF to markdown extraction
|-- large_doc_extractor/              # Attribute extraction pipeline
|-- param_extractor/
|   |-- rules/                        # Parameter extraction rules
|   `-- rules_loader.py
|-- access_score_finder/
|   |-- access_scorer.py              # Access score calculation
|   `-- access_score_scoring_framework.md
|-- groq_client/
|   `-- groq_client.py                # Groq API client with rate-limit handling
`-- validation/
    `-- output_schema.py              # Pydantic output schema
```

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

Install dependencies:

```powershell
pip install pandas pymupdf pymupdf4llm python-dotenv openai pydantic
```

If a `requirements.txt` file is present, use:

```powershell
pip install -r requirements.txt
```

To save the current environment for others:

```powershell
pip freeze > requirements.txt
```

## Environment Variables

Create a `.env` file in the project root.

```env
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama-3.1-8b-instant

# Helpful for Groq free/basic rate limits
GROQ_REQUEST_DELAY_SECONDS=16
GROQ_MAX_RETRIES=8
GROQ_MIN_REMAINING_TOKENS=5500
GROQ_RATE_LIMIT_BUFFER_SECONDS=3
```

Do not commit `.env` to GitHub.

## Inputs

Put all source PDFs in:

```text
data/raw_pdfs/
```

The submission CSV should be named `submissions.csv` by default and should contain at least these columns:

```csv
Filename,Brand
330109-4880941.pdf,TREMFYA
148593-4960549.pdf,STELARA
```

The `Filename` value must match a PDF in `data/raw_pdfs/`.

The full output CSV columns expected by the driver are:

```text
Filename
Brand
Age
Step Therapy Requirements Documented in Policy
Number of Steps through Brands
Number of Steps through Generic
Step through-Phototherapy
TB Test required
Quantity Limits
Specialist Types
Initial Authorization Duration(in-months)
Reauthorization Duration(in-months)
Reauthorization Required
Reauthorization Requirements Documented in Policy
Access Score
```

## Running The Pipeline

Run the full submission file:

```powershell
python driver.py
```

Run only the first row:

```powershell
python driver.py --limit 1
```

Use a specific indication:

```powershell
python driver.py --indication "Plaque psoriasis (PsO)"
```

The default indication is:

```text
Plaque psoriasis (PsO)
```

Write results to a separate CSV instead of overwriting `submissions.csv`:

```powershell
python driver.py --output outputs/filled_submissions/submissions_filled.csv
```

Re-extract markdown from PDFs even if markdown already exists:

```powershell
python driver.py --refresh-markdown
```

Skip the LLM vocabulary cleanup call and use deterministic vocabulary scanning:

```powershell
python driver.py --no-llm-vocabulary
```

This can help reduce Groq API calls on the free/basic tier.

## Outputs

Generated markdown files are saved to:

```text
data/extracted_pdfs_mds/
```

Extraction JSON files are saved to:

```text
outputs/large_doc_extractor/
```

Extraction logs are saved to:

```text
outputs/large_doc_extractor/log_folder/
```

The completed CSV is written back to `submissions.csv` unless `--output` is provided.

## Rules

Parameter extraction rules are loaded from:

```text
param_extractor/rules/
```

Edit these files to change how individual fields are extracted:

```text
age.md
step_therapy_requirements_documented_in_policy.md
number_of_steps_through_brands.md
number_of_steps_through_generic.md
step_through_phototherapy.md
tb_test_required.md
quantity_limits.md
specialist_types.md
initial_authorization_duration_in_months.md
reauthorization_duration_in_months.md
reauthorization_required.md
reauthorization_requirements_documented_in_policy.md
```

Access score is calculated separately using:

```text
access_score_finder/access_score_scoring_framework.md
```

## Groq Rate Limits

This pipeline sends multiple LLM requests per row. On Groq free/basic, token-per-minute limits can cause `429 Too Many Requests`.

Recommended `.env` settings:

```env
GROQ_REQUEST_DELAY_SECONDS=16
GROQ_MAX_RETRIES=8
GROQ_MIN_REMAINING_TOKENS=5500
GROQ_RATE_LIMIT_BUFFER_SECONDS=3
```

If rate limits still happen, increase:

```env
GROQ_REQUEST_DELAY_SECONDS=30
```

You can also run:

```powershell
python driver.py --no-llm-vocabulary
```

## GitHub Notes

Recommended files and folders to keep out of Git:

```gitignore
.env
.venv/
__pycache__/
*.pyc
outputs/
data/raw_pdfs/
```

Commit code, rules, and documentation. Do not commit API keys, virtual environments, or private policy PDFs.

## Typical Workflow

```powershell
.\.venv\Scripts\Activate.ps1
python driver.py --limit 1 --no-llm-vocabulary
```

Check the generated JSON in:

```text
outputs/large_doc_extractor/
```

Then run the full CSV:

```powershell
python driver.py --no-llm-vocabulary
```
