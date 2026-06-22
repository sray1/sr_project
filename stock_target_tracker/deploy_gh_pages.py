"""Publish the latest HTML report to GitHub Pages (gh-pages branch).

Setup (one-time, already done for this repo): an orphan `gh-pages` branch on the
origin remote containing only `index.html`, with GitHub Pages enabled to serve it.
The published URL is https://<owner>.github.io/<repo>/ .

Usage (after regenerating the report):
    python stock_target_tracker/deploy_gh_pages.py

What it does:
  1. Copies output/latest.html to a throwaway git repo (temp dir) as index.html.
  2. Commits it on an orphan `gh-pages` branch and force-pushes to origin.
     Force-push is safe here: gh-pages is an orphan branch with a single file and
     no shared history with the code branches.
  3. GitHub auto-rebuilds the Pages site from the push (takes ~30-60s).

The main working tree / current branch is never touched.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)  # sr_project root (parent of stock_target_tracker)
REPORT = os.path.join(HERE, "output", "latest.html")
BRANCH = "gh-pages"


def run(cmd, cwd=None, check=True):
    print(f"  $ {' '.join(cmd)}")
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def git_config(key):
    try:
        r = subprocess.run(["git", "config", key], cwd=REPO_ROOT,
                           capture_output=True, text=True)
        return r.stdout.strip() or None
    except Exception:
        return None


def origin_remote():
    r = subprocess.run(["git", "remote", "get-url", "origin"], cwd=REPO_ROOT,
                       capture_output=True, text=True)
    url = r.stdout.strip()
    if not url:
        sys.exit("ERROR: no 'origin' remote on this repo. Set one first:\n"
                 "  git remote add origin https://github.com/<owner>/<repo>.git")
    # https://github.com/<owner>/<repo>(.git)  -> owner, repo
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"ERROR: could not parse owner/repo from origin URL: {url}")
    return url, m.group(1), m.group(2)


def main():
    if not os.path.exists(REPORT):
        sys.exit(f"ERROR: report not found at {REPORT}\n"
                 f"Run `python {os.path.join(HERE, 'report.py')}` first.")

    url, owner, repo = origin_remote()
    name = git_config("user.name") or "Report Deploy"
    email = git_config("user.email") or f"{owner}@users.noreply.github.com"

    print(f"Publishing {REPORT} -> gh-pages on {owner}/{repo}")
    tmp = tempfile.mkdtemp(prefix="ghpages_")
    try:
        run(["git", "init", "-q"], cwd=tmp)
        run(["git", "remote", "add", "origin", url], cwd=tmp)
        run(["git", "checkout", "-q", "--orphan", BRANCH], cwd=tmp)
        shutil.copyfile(REPORT, os.path.join(tmp, "index.html"))
        run(["git", "add", "index.html"], cwd=tmp)
        run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}",
             "commit", "-q", "-m", "Update stock target report"], cwd=tmp)
        # Force-push: gh-pages is a standalone orphan branch (no shared history).
        run(["git", "push", "-f", "-u", "origin", BRANCH], cwd=tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nDone. GitHub will rebuild the site in ~30-60s.")
    print(f"Live URL: https://{owner}.github.io/{repo}/")


if __name__ == "__main__":
    main()