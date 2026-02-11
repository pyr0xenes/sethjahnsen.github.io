"""
SCOTUS Tracker Auto-Updater

Runs daily via GitHub Actions. Uses the Anthropic API to research current
SCOTUS case statuses and updates cases.json with any changes.
"""

import json
import os
import sys
from datetime import date
from pathlib import Path

import anthropic

CASES_PATH = Path(__file__).resolve().parent.parent / "agents" / "scotus-tracker" / "cases.json"

SYSTEM_PROMPT = """You are a Supreme Court research analyst. Your job is to review
the current state of tracked SCOTUS cases and return updated JSON data reflecting
any changes in status, new developments, or new cases worth tracking.

Focus on cases involving:
- Agency independence and presidential removal power
- Executive authority and emergency powers
- Separation of powers between branches

You must return ONLY valid JSON matching the exact schema provided. No markdown,
no commentary, no code fences — just the raw JSON object."""

def build_user_prompt(current_data: dict) -> str:
    today = date.today().isoformat()
    return f"""Today is {today}. Below is the current cases.json for the SCOTUS Tracker.
Review each case and update it based on your knowledge of the 2025-26 Supreme Court term.

For each existing case:
1. Update "status" if it has changed (e.g., from "Argued" to "Decided", or "Pending" to "Argued")
2. Update "statusDetail" with the latest information
3. Update "background" if there are significant new developments
4. Update "firstOrder" and "secondOrder" effects if the case has been decided or if new analysis is warranted
5. Update "urgency" if circumstances have changed
6. Add new sources if relevant

Also: if there are NEW major cases this term involving agency independence, executive
authority, or separation of powers that are NOT already tracked, add them with a new
unique id (starting from {max(c['id'] for c in current_data['cases']) + 1 if current_data['cases'] else 1}).

Valid statuses: "Argued", "Argument Scheduled", "Pending", "Re-argument Scheduled", "Decided"
Valid urgency levels: "high", "medium", "low"
Valid categories: "Agency Independence", "Executive Authority", "Separation of Powers"

Return the complete updated JSON object with this exact structure:
{{
  "lastUpdated": "{today}",
  "term": "{current_data.get('term', 'OCT 2025')}",
  "cases": [ ... all cases with the same fields ... ]
}}

Current data:
{json.dumps(current_data, indent=2)}

Return ONLY the updated JSON. If nothing has changed, return the data as-is with
today's date as lastUpdated."""


def update_cases() -> bool:
    """Call Claude to update cases. Returns True if cases.json was modified."""
    client = anthropic.Anthropic()

    current_data = json.loads(CASES_PATH.read_text())

    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_prompt(current_data)}],
    )

    raw = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
    if raw.endswith("```"):
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()

    try:
        updated_data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: Claude returned invalid JSON: {e}", file=sys.stderr)
        print(f"Raw response:\n{raw[:500]}", file=sys.stderr)
        return False

    # Validate structure
    if "cases" not in updated_data or not isinstance(updated_data["cases"], list):
        print("ERROR: Response missing 'cases' array", file=sys.stderr)
        return False

    for case in updated_data["cases"]:
        required = ["id", "name", "docket", "status", "statusDetail", "category",
                     "urgency", "question", "background", "firstOrder", "secondOrder", "sources"]
        missing = [f for f in required if f not in case]
        if missing:
            print(f"ERROR: Case '{case.get('name', '?')}' missing fields: {missing}", file=sys.stderr)
            return False

    # Check if anything actually changed (ignore lastUpdated for comparison)
    old_compare = {k: v for k, v in current_data.items() if k != "lastUpdated"}
    new_compare = {k: v for k, v in updated_data.items() if k != "lastUpdated"}

    if json.dumps(old_compare, sort_keys=True) == json.dumps(new_compare, sort_keys=True):
        print("No changes detected.")
        # Still update the lastUpdated timestamp
        current_data["lastUpdated"] = date.today().isoformat()
        CASES_PATH.write_text(json.dumps(current_data, indent=2, ensure_ascii=False) + "\n")
        return True

    # Write updated data
    CASES_PATH.write_text(json.dumps(updated_data, indent=2, ensure_ascii=False) + "\n")
    print(f"Updated {len(updated_data['cases'])} cases.")

    # Log what changed
    old_ids = {c["id"]: c for c in current_data.get("cases", [])}
    for case in updated_data["cases"]:
        old = old_ids.get(case["id"])
        if old is None:
            print(f"  NEW: {case['name']}")
        elif old.get("status") != case.get("status"):
            print(f"  STATUS CHANGE: {case['name']}: {old['status']} -> {case['status']}")
        elif old != case:
            print(f"  UPDATED: {case['name']}")

    return True


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    success = update_cases()
    sys.exit(0 if success else 1)
