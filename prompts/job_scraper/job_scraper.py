"""
git-hub repo: https://github.com/speedyapply/JobSpy

also
check this git repo for remote and other gobs
repo: https://github.com/speedyapply/2026-AI-College-Jobs
pip install -U python-jobspy
"""

import csv
from jobspy import scrape_jobs

jobs = scrape_jobs(
    # site_name=["indeed", "linkedin", "glassdoor", "google"],  # "zip_recruiter", "glassdoor", "bayt", "naukri", "bdjobs"
    site_name=["indeed", "linkedin"],  # "zip_recruiter", "glassdoor", "bayt", "naukri", "bdjobs"
    search_term="python developer",
    google_search_term="python developer jobs near Pune Division, Maharashtra since last month",
    location="Pune Division, Maharashtra, India",
    job_type="fulltime",
    results_wanted=200,
    hours_old=720,
    country_indeed='India',

    linkedin_fetch_description=True, # gets more info such as description, direct job url (slower)
    # proxies=["208.195.175.46:65095", "208.195.175.45:65095", "localhost"],
)
print(f"Found {len(jobs)} jobs")
print(jobs.head())
jobs.to_csv("jobs_in_200.csv", quoting=csv.QUOTE_NONNUMERIC, escapechar="\\", index=False)  # to_excel