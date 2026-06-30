#!/usr/bin/env python3
"""
Confluence REST API helper script.

Usage:
  python3 confluence.py get   <page_id>
  python3 confluence.py get   <page_id> --raw        # print raw storage HTML
  python3 confluence.py put   <page_id> <html_file>  # update page body from file
  python3 confluence.py put   <page_id> --stdin      # update page body from stdin
  python3 confluence.py find  <title_search>         # search pages by title
  python3 confluence.py ver   <page_id>              # print current version number only

Credentials are read from env vars or atlassian_env.csh:
  CONFLUENCE_URL        e.g. https://amd.atlassian.net/wiki
  CONFLUENCE_USERNAME   e.g. azman.binbabah@amd.com
  CONFLUENCE_API_TOKEN
"""

import sys
import os
import json
import re
import subprocess
import urllib.request
import urllib.parse
import urllib.error
import base64

# ── credentials ────────────────────────────────────────────────────────────────

def load_credentials():
    url   = os.environ.get("CONFLUENCE_URL")
    user  = os.environ.get("CONFLUENCE_USERNAME")
    token = os.environ.get("CONFLUENCE_API_TOKEN")

    if not all([url, user, token]):
        # Try sourcing atlassian_env.csh
        env_file = os.path.join(
            os.path.dirname(__file__),
            "../../../../../../abinbaba/rosenhorn_agent_flow/main_agent/script/atlassian_env.csh"
        )
        env_file = os.path.normpath(env_file)
        if os.path.exists(env_file):
            result = subprocess.run(
                ["bash", "-c", f"source {env_file} 2>/dev/null && "
                               "echo $CONFLUENCE_URL && echo $CONFLUENCE_USERNAME && echo $CONFLUENCE_API_TOKEN"],
                capture_output=True, text=True
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 3:
                url   = url   or lines[0].strip()
                user  = user  or lines[1].strip()
                token = token or lines[2].strip()

    if not all([url, user, token]):
        sys.exit("ERROR: Missing Confluence credentials. Set CONFLUENCE_URL, CONFLUENCE_USERNAME, CONFLUENCE_API_TOKEN.")

    return url.rstrip("/"), user, token


def auth_header(user, token):
    cred = base64.b64encode(f"{user}:{token}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Accept": "application/json", "Content-Type": "application/json"}


# ── API helpers ────────────────────────────────────────────────────────────────

def api_get(url, user, token, path):
    req = urllib.request.Request(f"{url}/rest/api/{path}", headers=auth_header(user, token))
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")


def api_put(url, user, token, page_id, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/rest/api/content/{page_id}",
        data=data, method="PUT",
        headers=auth_header(user, token)
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()}")


# ── commands ───────────────────────────────────────────────────────────────────

def cmd_get(page_id, raw=False):
    url, user, token = load_credentials()
    data = api_get(url, user, token, f"content/{page_id}?expand=body.storage,version")
    title   = data["title"]
    version = data["version"]["number"]
    body    = data["body"]["storage"]["value"]
    by      = data["version"]["by"]["displayName"]
    when    = data["version"].get("when", "")

    print(f"Title   : {title}")
    print(f"Version : {version}  (by {by}  at {when})")
    print(f"Page ID : {page_id}")
    print()

    if raw:
        print(body)
    else:
        text = re.sub(r"<[^>]+>", " ", body)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&#[0-9]+;", "", text)
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        print(text.strip())


def cmd_ver(page_id):
    url, user, token = load_credentials()
    data = api_get(url, user, token, f"content/{page_id}?expand=version")
    print(data["version"]["number"])


def cmd_put(page_id, html_body):
    url, user, token = load_credentials()
    data = api_get(url, user, token, f"content/{page_id}?expand=version")
    title   = data["title"]
    version = data["version"]["number"]

    payload = {
        "version": {"number": version + 1},
        "title":   title,
        "type":    "page",
        "body":    {"storage": {"value": html_body, "representation": "storage"}}
    }
    result = api_put(url, user, token, page_id, payload)
    print(f"SUCCESS — '{result['title']}' updated to version {result['version']['number']}")


def cmd_find(query):
    url, user, token = load_credentials()
    q = urllib.parse.quote(f'title ~ "{query}" AND type = page')
    data = api_get(url, user, token, f"content/search?cql={q}&limit=10")
    results = data.get("results", [])
    if not results:
        print("No pages found.")
        return
    print(f"{'ID':<15} {'Title'}")
    print("-" * 60)
    for r in results:
        print(f"{r['id']:<15} {r['title']}")


# ── main ───────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0].lower()

    if cmd == "get" and len(args) >= 2:
        raw = "--raw" in args
        cmd_get(args[1], raw=raw)

    elif cmd == "ver" and len(args) >= 2:
        cmd_ver(args[1])

    elif cmd == "put" and len(args) >= 2:
        page_id = args[1]
        if "--stdin" in args:
            html_body = sys.stdin.read()
        elif len(args) >= 3:
            with open(args[2]) as f:
                html_body = f.read()
        else:
            sys.exit("ERROR: provide --stdin or a file path")
        cmd_put(page_id, html_body)

    elif cmd == "find" and len(args) >= 2:
        cmd_find(" ".join(args[1:]))

    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
