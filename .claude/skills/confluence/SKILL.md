# Confluence Skill

Read, update, or search Confluence pages using the REST API.

## Trigger
`/confluence`

## Usage
```
/confluence get   <page_id>                    # read and display page content
/confluence get   <page_id> --raw              # show raw storage HTML
/confluence ver   <page_id>                    # show current version number
/confluence put   <page_id> "<html>"           # update page body with inline HTML
/confluence put   <page_id> --file <path>      # update page body from file
/confluence find  <title keywords>             # search pages by title
```

Page IDs are the numeric part of Confluence URLs:
  https://amd.atlassian.net/wiki/spaces/MSIP/pages/**1767896436**/...

---

## Credentials

Read automatically from environment or:
  `/proj/rtg_oss_feint1/FEINT_AI_AGENT/abinbaba/rosenhorn_agent_flow/main_agent/script/atlassian_env.csh`

Variables used: `CONFLUENCE_URL`, `CONFLUENCE_USERNAME`, `CONFLUENCE_API_TOKEN`

---

## Execution Model

Always use the Python helper script directly via Bash — do NOT call the REST API manually with curl in the main session.

Script path (relative to users/abinbaba):
  `script/confluence.py`

### Read a page
```bash
python3 script/confluence.py get <page_id>
python3 script/confluence.py get <page_id> --raw   # raw HTML for editing
```

### Get version number only
```bash
python3 script/confluence.py ver <page_id>
```

### Update a page
For updates, always:
1. First run `get --raw` to fetch the current body
2. Make the targeted change (string replace or inject new HTML)
3. Write modified HTML to a temp file
4. Run `put` with the temp file

```bash
# Step 1 — get raw body
python3 script/confluence.py get <page_id> --raw > /tmp/page_body.html

# Step 2 — edit /tmp/page_body.html (Python/sed/etc.)

# Step 3 — push update
python3 script/confluence.py put <page_id> --file /tmp/page_body.html
```

Or for small inline changes, pipe directly:
```bash
python3 script/confluence.py get <page_id> --raw \
  | sed 's/old text/new text/g' \
  | python3 script/confluence.py put <page_id> --stdin
```

### Search pages
```bash
python3 script/confluence.py find "Godavari status July"
```

---

## Common Patterns

### Append a new section to a page
```python
# In Python — fetch body, append, push
import subprocess, re

raw = subprocess.check_output(['python3', 'script/confluence.py', 'get', PAGE_ID, '--raw']).decode()
new_section = '<p><strong>New content</strong></p>'
new_body = raw + new_section
with open('/tmp/new_body.html', 'w') as f:
    f.write(new_body)
subprocess.run(['python3', 'script/confluence.py', 'put', PAGE_ID, '--file', '/tmp/new_body.html'])
```

### Update a specific cell by local-id
```bash
python3 script/confluence.py get <page_id> --raw | python3 -c "
import sys, re
body = sys.stdin.read()
body = re.sub(r'(<p local-id=\"TARGET_ID\">).*?(</p>)', r'\g<1>NEW VALUE\g<2>', body)
# handle self-closing: <p local-id=\"ID\" />
body = body.replace('<p local-id=\"TARGET_ID\" />', '<p local-id=\"TARGET_ID\">NEW VALUE</p>')
print(body)
" | python3 script/confluence.py put <page_id> --stdin
```

---

## Known Page IDs

| Page | ID |
|------|----|
| Godavari status June 2026 | `1718619227` |
| Godavari status July 2026 | `1767896436` |
