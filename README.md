# Daily Job Email Digest

A stateless Python script that searches Adzuna, scores results against your keywords, and emails a daily digest. It does not use a database or scrape LinkedIn.

## Configure

Use Python 3.10 or newer:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add your Adzuna API credentials, SMTP settings, `MIN_SALARY`, and recipient address in `EMAIL_TO` to `.env`. `EMAIL_TO` accepts comma-separated addresses. 
Put search terms, matching skills, and result count in `config.yaml`.

## Run

Run once and send the email:

```bash
python main.py
```

Preview results without sending an email:

```bash
python main.py --dry-run
```

The process exits after one run. Schedule it once daily with cron, a systemd timer, or your hosting provider's scheduler. Example cron entry for 8:00 AM server time:

```cron
0 8 * * * cd /path/to/job-alerts && /path/to/job-alerts/.venv/bin/python main.py >> job-digest.log 2>&1
```

Because there is no persistent storage, the script cannot identify listings already emailed on previous days; Adzuna may return the same job in multiple daily digests. Match scores are keyword estimates, not assessments of job fit.