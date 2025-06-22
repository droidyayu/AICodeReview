import os
import requests
import json
import sys

# Debug flag
DEBUG = True

def debug_log(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")

# === CONFIG ===
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
if not GITHUB_TOKEN:
    print("[ERROR] GITHUB_TOKEN is not set!", file=sys.stderr)
    exit(1)
else:
    debug_log(f"GITHUB_TOKEN starts with: {GITHUB_TOKEN[:6]}... (length: {len(GITHUB_TOKEN)})")

REPO = os.getenv("GITHUB_REPOSITORY")  # Format: owner/repo
if not REPO:
    print("[ERROR] GITHUB_REPOSITORY is not set!", file=sys.stderr)
    exit(1)
else:
    debug_log(f"GITHUB_REPOSITORY: {REPO}")

PR_NUMBER = os.getenv("GITHUB_REF").split("/")[2]
if not PR_NUMBER:
    print("[ERROR] GITHUB_REF is not set or not in the correct format!", file=sys.stderr)
    exit(1)
else:
    debug_log(f"GITHUB_REF: {PR_NUMBER}")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY is not set!", file=sys.stderr)
    exit(1)
else:
    debug_log(f"GEMINI_API_KEY starts with: {GEMINI_API_KEY[:6]}... (length: {len(GEMINI_API_KEY)})")

HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}
debug_log(f"HEADERS: {HEADERS}")


def get_changed_files():
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}/files"
    print(f"[INFO] Fetching changed files from: {url}")
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"[ERROR] GitHub API error {response.status_code}: {response.text}")
        return []
    return response.json()


def clean_gemini_comment(comment):
    """
    Clean and format Gemini's response for GitHub inline comments.
    - Trims whitespace
    - Limits length to 650 chars
    - Removes excessive blank lines
    """
    comment = comment.strip()
    comment = '\n'.join([line.rstrip() for line in comment.splitlines() if line.strip() != ''])
    max_length = 650
    if len(comment) > max_length:
        comment = comment[:max_length] + "\u2026"
    return comment

def generate_review_comment(diff_hunk, filename):
    prompt = f"""
You're a senior Android reviewer. Carefully review this code diff from `{filename}`.

- Only return specific, actionable, and concise inline review comments.
- Use clear markdown formatting for code, lists, or warnings.
- Avoid generic feedback. Focus on deprecated APIs, performance, testability, and Android best practices.
- Each comment should be suitable for direct posting as a GitHub inline review.
- If the diff is fine, return a short positive note.

Diff:
{diff_hunk}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    debug_log(f"Gemini API URL: {url}")
    debug_log(f"Request headers: {headers}")
    debug_log(f"Request data: {json.dumps(data)}")
    response = requests.post(url, headers=headers, data=json.dumps(data))
    if response.status_code != 200:
        print(f"[ERROR] Gemini API error {response.status_code}: {response.text}")
        return ""
    result = response.json()
    try:
        comment = result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"[ERROR] Failed to parse Gemini response: {e}")
        return ""
    return clean_gemini_comment(comment)

def get_latest_commit_sha():
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"[ERROR] GitHub API error {response.status_code}: {response.text}")
        return None
    return response.json()["head"]["sha"]

def post_inline_comment(body, path, position):
    commit_id = get_latest_commit_sha()
    url = f"https://api.github.com/repos/{REPO}/pulls/{PR_NUMBER}/comments"
    data = {
        "body": body,
        "commit_id": commit_id,
        "path": path,
        "side": "RIGHT",
        "line": position
    }
    response = requests.post(url, headers=HEADERS, data=json.dumps(data))
    if response.status_code != 201:
        print(f"[ERROR] Failed to post inline comment: {response.status_code} {response.text}")
    else:
        print(f"[INFO] Posted inline comment on {path}:{position}")

def fetch_file_content(repo, path):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"[ERROR] Failed to fetch file content: {response.status_code} {response.text}")
        return None
    content = response.json()["content"]
    import base64
    return base64.b64decode(content).decode("utf-8")

def generate_test_coverage_comment(source_code, test_code, source_filename, test_filename):
    prompt = f"""
You're a senior Android reviewer. Compare the following source file and its test file. Give feedback on:
- Test coverage and missing test cases
- Test quality (clarity, isolation, edge cases)
- Suggestions to improve testability

Source file: {source_filename}
{source_code}

Test file: {test_filename}
{test_code}
"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {"Content-Type": "application/json"}
    data = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }
    response = requests.post(url, headers=headers, data=json.dumps(data))
    if response.status_code != 200:
        print(f"[ERROR] Gemini API error {response.status_code}: {response.text}")
        return ""
    result = response.json()
    try:
        comment = result["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"[ERROR] Failed to parse Gemini response: {e}")
        return ""
    return clean_gemini_comment(comment)

def post_pr_comment(body):
    url = f"https://api.github.com/repos/{REPO}/issues/{PR_NUMBER}/comments"
    data = {"body": body}
    response = requests.post(url, headers=HEADERS, data=json.dumps(data))
    if response.status_code != 201:
        print(f"[ERROR] Failed to post PR comment: {response.status_code} {response.text}")
    else:
        print(f"[INFO] Posted PR comment")

import re


def infer_source_filename(test_filename):
    if test_filename.endswith("Test.java"):
        return test_filename.replace("Test.java", ".java")
    if test_filename.endswith("Test.kt"):
        return test_filename.replace("Test.kt", ".kt")
    if test_filename.endswith("Test.py"):
        return test_filename.replace("Test.py", ".py")
    if test_filename.endswith("_test.py"):
        return test_filename.replace("_test.py", ".py")
    return test_filename

def main():
    changed_files = get_changed_files()
    for file in changed_files:
        filename = file["filename"]
        diff_hunk = file["patch"]
        position = file["changes"]  # Simplification: using changes as line number
        comment = generate_review_comment(diff_hunk, filename)
        if comment:
            post_inline_comment(comment, filename, position)

if __name__ == "__main__":
    main()
