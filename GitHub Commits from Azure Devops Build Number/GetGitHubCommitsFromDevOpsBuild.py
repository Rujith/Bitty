import sys
import json
import re
import requests
import subprocess
from requests.auth import HTTPBasicAuth
from datetime import datetime
import os

# ========================
# Load configuration from JSON file
# ========================

if len(sys.argv) < 2:
    print("❌ Usage: python script.py <config_file.json> [<build1> <build2>]")
    sys.exit(1)

config_file = sys.argv[1]

try:
    with open(config_file, "r", encoding="utf-8") as f:
        config = json.load(f)
except Exception as e:
    print(f"❌ Failed to load configuration: {e}")
    sys.exit(1)

# Extract config variables
ORG = config.get("ORG")
PROJECT = config.get("PROJECT")
DEFINITION_ID = config.get("DEFINITION_ID")
BRANCH = config.get("BRANCH")
AZURE_PAT = config.get("AZURE_PAT")
GITHUB_REPO = config.get("GITHUB_REPO")
BUILD_NUMBERS = config.get("BUILD_NUMBERS", [])

# ========================
# Functions
# ========================

def get_azure_build_commit(org, project, definition_id, branch, build_number, pat):
    """Fetch commit SHA from Azure DevOps for a succeeded build."""
    url = f"https://dev.azure.com/{org}/{project}/_apis/build/builds"
    params = {
        "definitions": definition_id,
        "branchName": branch,
        "buildNumber": build_number,
        "api-version": "7.2-preview.7"
    }

    print(f"Fetching build {build_number} from Azure DevOps {org}/{project}...")

    try:
        response = requests.get(url, auth=HTTPBasicAuth('', pat), params=params, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching build {build_number}: {e}")
        return None

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        print(f"⚠️ Invalid JSON for build {build_number}: {e}")
        return None

    if data.get("count", 0) == 0:
        print(f"❌ No build found for {build_number}")
        return None

    build = next((b for b in data["value"] if b.get("result") == "succeeded"), None)
    if not build:
        print(f"⚠️ Build {build_number} not succeeded.")
        return None

    commit_sha = build.get("sourceVersion")
    if not commit_sha:
        print(f"❌ No source version found for build {build_number}")
        return None

    print(f"✅ Build {build_number} → Commit: {commit_sha}")
    return commit_sha


def get_github_commits_between(repo, sha1, sha2):
    """Get list of commits between two SHAs using gh CLI and show compare URL."""
    compare_url = f"https://github.com/{repo}/compare/{sha1}...{sha2}"
    print(f"\n🔗 GitHub Compare URL: {compare_url}")
    print(f"Fetching commits between {sha1[:7]} and {sha2[:7]} from GitHub repo {repo}...")

    cmd = ["gh", "api", f"repos/{repo}/compare/{sha1}...{sha2}"]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            print(f"❌ GitHub API error: {result.stderr}")
            return [], compare_url
    except subprocess.TimeoutExpired:
        print("❌ GitHub API request timed out")
        return [], compare_url
    except FileNotFoundError:
        print("❌ GitHub CLI (gh) not found. Please install it first.")
        return [], compare_url

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON response from GitHub API: {e}")
        return [], compare_url

    commits = data.get("commits", [])
    print(f"🔍 Found {len(commits)} commits between {sha1[:7]} and {sha2[:7]}:\n")

    commit_list = []
    for c in commits:
        try:
            commit_info = {
                "sha": c["sha"][:7],
                "message": c["commit"]["message"],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"]
            }
            commit_list.append(commit_info)

            msg_summary = commit_info["message"].split("\n")[0]
            print(f"- {commit_info['sha']} | {commit_info['author']} | {commit_info['date']} | {msg_summary}")
        except KeyError as e:
            print(f"⚠️ Skipping malformed commit: missing field {e}")
            continue

    return commit_list, compare_url


def analyze_commits(commit_list, jira_base_url="https://landmarkinfo.atlassian.net/browse/"):
    """Extract unique ticket references (3-5 letters + 2-6 digits) and return Jira links."""
    pattern = re.compile(r"\b[a-zA-Z]{3,5}-\d{2,6}\b", re.IGNORECASE)
    refs = set()

    for c in commit_list:
        try:
            found = pattern.findall(c["message"])
            refs.update([f.upper() for f in found])
        except KeyError:
            print("⚠️ Skipping commit with missing message field")
            continue

    refs_list = sorted(refs)

    print("\n📝 Unique references with Jira links:")
    if refs_list:
        for ref in refs_list:
            print(f"- {ref}: {jira_base_url}{ref}")
    else:
        print("None found.")

    return [f"{jira_base_url}{ref}" for ref in refs_list]


def export_to_markdown(commit_list, refs_links, repo, compare_url, output_file="commit_report.md"):
    """Export commits, ticket references, and compare URL to Markdown with timestamp."""
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            f.write(f"# Commit Report - {timestamp}\n\n")

            # Add GitHub Compare URL at the top
            f.write(f"**GitHub Compare URL:** [{compare_url}]({compare_url})\n\n")

            # Section 1: GitHub commits
            f.write("## GitHub Commits\n\n")
            if commit_list:
                for c in commit_list:
                    try:
                        sha_link = f"https://github.com/{repo}/commit/{c['sha']}"
                        msg_summary = c["message"].split("\n")[0]
                        pr_pattern = re.compile(r"#(\d+)")
                        msg_summary_with_pr = pr_pattern.sub(
                            lambda m: f"[#{m.group(1)}](https://github.com/{repo}/pull/{m.group(1)})",
                            msg_summary
                        )
                        f.write(f"- [{c['sha']}]({sha_link}) | {c['author']} | {c['date']} | {msg_summary_with_pr}\n")
                    except KeyError as e:
                        print(f"⚠️ Skipping commit with missing field {e}")
                        continue
            else:
                f.write("No commits found.\n")

            # Section 2: Ticket references
            f.write("\n## Ticket References\n\n")
            if refs_links:
                for link in refs_links:
                    f.write(f"- {link}\n")
            else:
                f.write("No ticket references found.\n")

            # Add timestamp as last line
            f.write(f"\n_Report generated on {timestamp}_\n")

        print(f"\n✅ Markdown file created: {output_file}")
    except IOError as e:
        print(f"❌ Error writing to file {output_file}: {e}")


def validate_build_number(build_number):
    """Validate build number format (x.y.z)"""
    pattern = re.compile(r'^\d+\.\d+\.\d+$')
    return bool(pattern.match(build_number))


def main():
    # Determine build numbers to use
    if len(sys.argv) == 4:
        build1, build2 = sys.argv[2], sys.argv[3]
        print(f"🔧 Comparing commits between builds {build1} and {build2} (from command-line args)\n")
    else:
        if len(BUILD_NUMBERS) != 2:
            print("❌ Please provide exactly two build numbers in the config file or via command-line args.")
            sys.exit(1)
        build1, build2 = BUILD_NUMBERS
        print(f"🔧 Comparing commits between builds {build1} and {build2} (from config file)\n")

    # Validate build numbers
    for build_num in [build1, build2]:
        if not validate_build_number(build_num):
            print(f"⚠️ Warning: Build number '{build_num}' doesn't match expected format (x.y.z)")

    commit_shas = []
    for build_num in [build1, build2]:
        sha = get_azure_build_commit(ORG, PROJECT, DEFINITION_ID, BRANCH, build_num, AZURE_PAT)
        if sha:
            commit_shas.append(sha)

    if len(commit_shas) < 2:
        print("⚠️ Could not get two valid commit SHAs. Exiting.")
        sys.exit(1)

    sha1, sha2 = commit_shas
    commit_list, compare_url = get_github_commits_between(GITHUB_REPO, sha1, sha2)
    
    if not commit_list:
        print("⚠️ No commits found between the specified builds.")
        return
    
    refs_links = analyze_commits(commit_list)

    # Generate Markdown filename based on config name + builds
    base_name = os.path.splitext(os.path.basename(config_file))[0]  # remove .json
    output_file = f"{base_name}_{build1}_{build2}_commit_report.md"
    
    export_to_markdown(commit_list, refs_links, GITHUB_REPO, compare_url, output_file=output_file)


if __name__ == "__main__":
    main()
