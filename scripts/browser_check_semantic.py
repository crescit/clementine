"""Real-browser acceptance check for the semantic review flow.

Exercises: policy-admin vs read-only policies page, reviewer analysis of the
semantic-only violation (CP-8903), submitter read-only view while the finding is
unacknowledged, reviewer acknowledge -> approval gate opens, and analysis of the
compliant counterexample (CP-8908). Runs against the real model, so model calls
are strictly serial.

uv run --with playwright python scripts/browser_check_semantic.py http://127.0.0.1:8017
Uses installed Chrome; no frontend build tooling is needed.
"""

import sys
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8017"
OUT = Path("docs/screenshots")
OUT.mkdir(exist_ok=True)

# The real model takes ~30-55s per analysis; stay well under the front-end's
# 180000 ms analyze timeout.
ANALYZE_MS = 180000


def shot(page, name):
    page.screenshot(path=str(OUT / name), full_page=True)


with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
    context = browser.new_context(
        viewport={"width": 1440, "height": 1100}, device_scale_factor=1
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    # Fresh disposable seed (in-place reload; networkidle is unreliable here).
    page.goto(BASE)
    page.get_by_role("button", name="Reset demo", exact=True).click()
    page.get_by_role("button", name="Reset workspace", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#queue-body tr")).to_have_count(6)

    # ------------------------------------------------------------------
    # 1. Policy admin: Sarah T. can edit the draft and publish.
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Sarah T.")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/policies.html")
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#publish-btn")).to_be_enabled()
    expect(page.locator("#add-rule")).to_be_visible()
    shot(page, "semantic-policies-admin.png")
    print("PASS: policies page is editable for the policy admin (Sarah T.)", flush=True)

    # ------------------------------------------------------------------
    # 2. Read-only policies for a non-admin (Mark Davis).
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Mark Davis")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/policies.html")
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#publish-btn")).to_be_disabled()
    expect(page.locator("#add-rule")).to_be_hidden()
    expect(page.locator("#rule-list input, #rule-list textarea, #rule-list select").first).to_be_disabled()
    shot(page, "semantic-policies-readonly.png")
    print("PASS: policies page is read-only for a non-admin", flush=True)

    # ------------------------------------------------------------------
    # 3. Reviewer (Sarah T.) opens CP-8903, the semantic-only violation.
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Sarah T.")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/index.html")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("link", name="Q4 First-Time Buyer", exact=True).click()
    page.wait_for_load_state("domcontentloaded")

    # Not analyzed yet: panel visible, analyze button present, approval gate closed.
    expect(page.locator("#semantic-panel")).to_be_visible()
    expect(page.locator("#semantic-title")).to_have_text("Not analyzed")
    expect(page.locator("#analyze")).to_be_visible()
    expect(page.locator("#approve")).to_be_disabled()
    shot(page, "semantic-review-notanalyzed.png")
    print("PASS: CP-8903 reviewer sees 'Not analyzed' with approval gate closed", flush=True)

    # Run the real semantic analysis.
    page.locator("#analyze").click()
    expect(page.locator("#semantic-title")).to_have_text("1 semantic finding", timeout=ANALYZE_MS)
    expect(page.locator("#semantic-findings")).to_contain_text("CLAIM_002")
    # The finding carries a disposition form (Acknowledge/Dismiss) for the reviewer.
    expect(page.locator("#semantic-findings button")).to_have_count(2)
    shot(page, "semantic-review-findings.png")
    print("PASS: CP-8903 analysis flagged CLAIM_002 for the reviewer", flush=True)

    # ------------------------------------------------------------------
    # 4. Submitter (Jessica Lin) sees the finding read-only while unacknowledged:
    #    no analyze button, no disposition forms, and the approval gate stays closed.
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Jessica Lin")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/index.html")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("link", name="Q4 First-Time Buyer", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#semantic-panel")).to_be_visible()
    expect(page.locator("#semantic-findings")).to_contain_text("CLAIM_002")
    expect(page.locator("#analyze")).to_be_hidden()
    expect(page.locator("#semantic-findings button")).to_have_count(0)
    expect(page.locator("#approve")).to_be_disabled()
    shot(page, "semantic-submitter-readonly.png")
    print("PASS: submitter sees the finding read-only, approval gate closed", flush=True)

    # ------------------------------------------------------------------
    # 5. Back as the reviewer: acknowledge the finding -> approval gate opens.
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Sarah T.")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/index.html")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("link", name="Q4 First-Time Buyer", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    page.locator('textarea[aria-label="Disposition reason for CLAIM_002"]').fill(
        "Copy promises approval regardless of credit; the semantic model flagged CLAIM_002."
    )
    page.locator('#semantic-findings button[value="ACKNOWLEDGED"]').click()
    expect(page.locator("#semantic-findings")).to_contain_text(
        "Acknowledged — needs change"
    )
    expect(page.locator("#approve")).to_be_enabled()
    shot(page, "semantic-review-approve.png")
    print("PASS: acknowledging the finding opened the approval gate", flush=True)

    # ------------------------------------------------------------------
    # 6. Compliant counterexample (CP-8908) as its assigned reviewer (Mark).
    # ------------------------------------------------------------------
    page.locator("#persona").select_option(label="Mark Davis")
    page.wait_for_load_state("domcontentloaded")
    page.goto(BASE + "/static/index.html")
    page.wait_for_load_state("domcontentloaded")
    page.get_by_role("link", name="Spring Mortgage Preview", exact=True).click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("#semantic-title")).to_have_text("Not analyzed")
    page.locator("#analyze").click()

    # Record honestly: the compliant copy should stay clean, but we assert the
    # analysis actually completed and report whichever state the model produced.
    # Title either becomes "No semantic findings" (clean) or "N semantic finding(s)".
    title = page.locator("#semantic-title")
    page.wait_for_selector("#semantic-title:not(:has-text('Analyzing'))", timeout=ANALYZE_MS)
    outcome = title.text_content()
    clean = "No semantic findings" in (outcome or "")
    findings = page.locator("#semantic-findings button").count()
    if clean:
        expect(page.locator("#semantic-title")).to_have_text("No semantic findings")
    else:
        expect(page.locator("#semantic-findings")).to_be_visible()
    shot(page, "semantic-clean-check.png")
    print(
        f"RESULT: CP-8908 (compliant) analyzed -> {outcome!r} "
        f"({findings} disposition form(s) present). clean={clean}",
        flush=True,
    )

    assert not errors, f"console page errors: {errors}"
    browser.close()
    print("PASS: browser walkthrough of the semantic flow", flush=True)
