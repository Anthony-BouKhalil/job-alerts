import argparse
import asyncio
import html
import os
import re
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}
    if not isinstance(config, dict):
        raise ValueError("The YAML configuration must contain a mapping at its root.")
    if not config.get("searches"):
        raise ValueError("Add at least one search under 'searches' in the YAML configuration.")
    if not config.get("skills"):
        raise ValueError("Add one or more keywords under 'skills' in the YAML configuration.")
    return config


def score_job(job: dict[str, Any], skills: list[str]) -> int:
    text = f"{job.get('title', '')} {job.get('description', '')}".lower()
    matched = sum(
        1 for skill in skills
        if re.search(rf"(?<!\w){re.escape(skill.lower())}(?!\w)", text)
    )
    return round(100 * matched / len(skills)) if skills else 0


async def fetch_jobs(config: dict[str, Any]) -> list[dict[str, Any]]:
    app_id = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        raise ValueError("Set ADZUNA_APP_ID and ADZUNA_APP_KEY in .env before running the digest.")

    adzuna = config.get("adzuna", {})
    country = adzuna.get("country", "ca")
    result_count = int(adzuna.get("results_per_page", 20))
    pages = int(adzuna.get("pages", 1))
    salary_value = os.getenv("MIN_SALARY", "0").strip()
    min_salary = int(salary_value) if salary_value else None
    skills = [str(skill) for skill in config["skills"]]
    found: dict[str, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=30) as client:
        for search in config["searches"]:
            for page in range(1, pages + 1):
                params = {
                    "app_id": app_id,
                    "app_key": app_key,
                    "results_per_page": result_count,
                    "what": search["keywords"],
                    "where": search.get("location", ""),
                }
                if min_salary is not None:
                    params["salary_min"] = int(min_salary)
                response = await client.get(
                    f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}",
                    params=params,
                )
                response.raise_for_status()
                for item in response.json().get("results", []):
                    url = str(item.get("redirect_url") or "")
                    if not url.startswith(("https://", "http://")):
                        continue
                    company = item.get("company") or {}
                    location = item.get("location") or {}
                    job = {
                        "title": str(item.get("title") or "Untitled listing"),
                        "company": str(company.get("display_name") or "Unknown company"),
                        "location": str(location.get("display_name") or "Location not listed"),
                        "url": url,
                        "description": str(item.get("description") or "").strip(),
                        "salary_min": item.get("salary_min"),
                        "salary_max": item.get("salary_max"),
                        "score": 0,
                    }
                    job["score"] = score_job(job, skills)
                    found.setdefault(str(item.get("id") or url), job)

    return sorted(found.values(), key=lambda job: (job["score"], job.get("salary_min") or 0), reverse=True)


def salary_text(job: dict[str, Any]) -> str:
    lower, upper = job.get("salary_min"), job.get("salary_max")
    if lower and upper:
        return f"${lower:,.0f}–${upper:,.0f} CAD"
    if lower:
        return f"From ${lower:,.0f} CAD"
    if upper:
        return f"Up to ${upper:,.0f} CAD"
    return "Salary not listed"


def build_email(jobs: list[dict[str, Any]], subject_prefix: str) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = f"{subject_prefix} | {date.today().isoformat()} | {len(jobs)} jobs"
    plain_lines = [f"Daily job digest: {len(jobs)} listing(s)", ""]
    html_rows = []
    for job in jobs:
        summary = " ".join(job["description"].split())[:400]
        plain_lines.extend([
            f"{job['title']} at {job['company']} ({job['score']}% match)",
            f"{job['location']} | {salary_text(job)}",
            job["url"],
            summary,
            "",
        ])
        html_rows.append(
            "<article style='padding:16px 0;border-bottom:1px solid #dce5e1'>"
            f"<h2 style='margin:0 0 5px;font-size:18px'><a href='{html.escape(job['url'], quote=True)}' style='color:#176f5d'>{html.escape(job['title'])}</a></h2>"
            f"<p style='margin:0 0 8px;color:#52615c'>{html.escape(job['company'])} · {html.escape(job['location'])}</p>"
            f"<p style='margin:0 0 8px'><strong>{job['score']}% match</strong> · {html.escape(salary_text(job))}</p>"
            f"<p style='margin:0;color:#52615c'>{html.escape(summary)}</p></article>"
        )

    if not jobs:
        plain_lines.append("No listings were returned for today's searches.")
        html_rows.append("<p>No listings were returned for today's searches.</p>")
    message.set_content("\n".join(plain_lines))
    message.add_alternative(
        "<html><body style='font:15px/1.5 Arial,sans-serif;color:#202b2a;max-width:680px;margin:24px auto'>"
        f"<h1 style='font-size:24px'>Daily job digest</h1><p>{len(jobs)} listing(s) found today.</p>"
        f"{''.join(html_rows)}</body></html>",
        subtype="html",
    )
    return message


def send_email(message: EmailMessage, config: dict[str, Any]) -> None:
    email_config = config.get("email", {})
    smtp_config = config.get("smtp", {})
    recipients = [address.strip() for address in os.getenv("EMAIL_TO", "").split(",") if address.strip()]
    if not recipients:
        raise ValueError("Set at least one recipient in EMAIL_TO in .env.")

    host = os.getenv("SMTP_HOST", smtp_config.get("host", ""))
    port = int(os.getenv("SMTP_PORT", smtp_config.get("port", 587)))
    username = os.getenv("SMTP_USERNAME", "")
    password = os.getenv("SMTP_PASSWORD", "")
    sender = os.getenv("EMAIL_FROM", username)
    if not host or not sender:
        raise ValueError("Set SMTP_HOST and EMAIL_FROM (and SMTP credentials if required) in .env.")

    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if smtp_config.get("use_ssl", False):
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as server:
            if username:
                server.login(username, password)
            server.send_message(message)
    else:
        with smtplib.SMTP(host, port) as server:
            server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password)
            server.send_message(message)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch jobs from Adzuna and email a daily digest.")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"), help="YAML configuration file")
    parser.add_argument("--dry-run", action="store_true", help="Print the digest without sending email")
    args = parser.parse_args()

    load_dotenv()
    try:
        config = load_config(args.config)
        jobs = asyncio.run(fetch_jobs(config))
        message = build_email(jobs, config.get("email", {}).get("subject_prefix", "Job alerts"))
        if args.dry_run:
            print(message.get_body(preferencelist=("plain",)).get_content())
            return
        send_email(message, config)
        print(f"Sent daily digest with {len(jobs)} listing(s).")
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError, httpx.HTTPError, smtplib.SMTPException) as error:
        parser.exit(1, f"job digest failed: {error}\n")


if __name__ == "__main__":
    main()