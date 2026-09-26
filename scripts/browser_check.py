"""Real-browser acceptance check. Run against a disposable seeded database.

uv run --with playwright python scripts/browser_check.py http://127.0.0.1:8017
Uses installed Chrome; no frontend build tooling is needed.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8017"
OUT = Path("docs/screenshots")
OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
    context = browser.new_context(
        viewport={"width": 1440, "height": 1100}, device_scale_factor=1
    )
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(BASE)
    page.get_by_role("button", name="Reset demo", exact=True).click()
    with page.expect_navigation(wait_until="networkidle"):
        page.get_by_role("button", name="Reset workspace", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(6)
    expect(page.locator("#queue-body tr").first).to_contain_text("CreditKarma")
    page.screenshot(path=str(OUT / "queue-desktop.png"), full_page=True)
    page.get_by_role("link", name="ClearRewards Launch", exact=True).click()
    expect(page.locator("#preflight-title")).to_have_text("2 blocking findings")
    expect(page.locator("#approve")).to_be_disabled()
    expect(page.locator("mark")).to_have_text("pre-approved")
    page.screenshot(path=str(OUT / "review-desktop.png"), full_page=True)
    page.locator("#decision-comment").fill(
        "Replace the approval claim and add: Subject to credit approval."
    )
    page.get_by_role("button", name="Request changes", exact=True).click()
    expect(page.locator("#record-status")).to_have_text("Changes requested")
    page.locator("#persona").select_option(label="Jessica Lin")
    expect(page.locator("#revision-form")).to_be_visible()
    corrected = "You may be pre-qualified for ClearRewards. Explore rewards for everyday purchases. Subject to credit approval."
    page.locator("#revision-copy").fill(corrected)
    page.get_by_role("button", name="Save & resubmit").click()
    expect(page.locator("#record-status")).to_have_text("Under review")
    expect(page.locator("#preflight-title")).to_have_text("Checks passed")
    page.locator("#version").select_option("1")
    expect(page.locator("#copy")).to_contain_text("You're pre-approved")
    page.locator("#persona").select_option(label="Mark Davis")
    expect(page.locator("#decision-form")).to_be_hidden()
    page.locator("#persona").select_option(label="Sarah T.")
    expect(page.locator("#approve")).to_be_enabled()
    page.get_by_role("button", name="Approve version").click()
    expect(page.locator("#record-status")).to_have_text("Approved")
    expect(
        page.locator("#timeline").get_by_text("Approved", exact=True)
    ).to_be_visible()
    expect(page.locator("#assign-form")).to_be_hidden()
    page.locator("#back").click()
    expect(page.locator("#queue-body tr")).to_have_count(5)
    page.get_by_role("link", name="Completed", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(2)
    page.get_by_role("link", name="New submission").click()
    expect(page.locator('[name="title"]')).to_be_disabled()
    page.locator("#persona").select_option(label="Jessica Lin")
    expect(page.locator('[name="title"]')).to_be_enabled()
    page.screenshot(path=str(OUT / "intake-desktop.png"), full_page=True)
    page.locator('[name="title"]').fill("Browser acceptance affiliate campaign")
    page.locator('[name="product"]').select_option("PERSONAL_LOAN")
    page.locator("#channel").select_option("AFFILIATE")
    expect(page.locator("#partner")).to_have_attribute("required", "")
    page.locator("#partner").fill("Example partner")
    page.locator("#launch-date").fill(datetime.now(timezone.utc).date().isoformat())
    page.locator('[name="copy_text"]').fill(
        "Explore a loan. Subject to credit approval. ClearPath may compensate this partner."
    )
    page.get_by_role("button", name="Submit for review").click()
    expect(page.locator("#record-status")).to_have_text("Under review")
    expect(page.locator("#subtitle")).to_contain_text("Example partner")
    expect(page.locator("#preflight-title")).to_have_text("Checks passed")
    page.locator("#persona").select_option(label="Sarah T.")
    page.goto(BASE)
    page.locator("#search").fill("does not exist")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#queue-state")).to_contain_text("No campaigns match")
    page.get_by_role("link", name="Clear", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(6)
    print("PASS: desktop lifecycle, permissions, intake, and search", flush=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.reload(wait_until="networkidle")
    page.screenshot(path=str(OUT / "queue-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Queue overflows mobile viewport"
    )
    page.get_by_role("link", name="Spring Mortgage Preview", exact=True).click()
    expect(page.locator("#detail")).to_be_visible()
    page.screenshot(path=str(OUT / "review-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Review overflows mobile viewport"
    )
    page.goto(BASE + "/static/submit.html")
    page.locator("#persona").select_option(label="Jessica Lin")
    expect(page.locator('[name="title"]')).to_be_enabled()
    page.screenshot(path=str(OUT / "intake-mobile.png"), full_page=True)
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (
        "Intake overflows mobile viewport"
    )
    page.goto(BASE)
    page.locator("#persona").select_option(label="Sarah T.")
    page.get_by_role("button", name="Reset demo", exact=True).click()
    page.get_by_role("button", name="Cancel", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(6)
    page.get_by_role("button", name="Reset demo", exact=True).click()
    with page.expect_navigation(wait_until="networkidle"):
        page.get_by_role("button", name="Reset workspace", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(6)
    expect(
        page.get_by_role("link", name="ClearRewards Launch", exact=True)
    ).to_be_visible()
    # Failure recovery must retain feedback, and stale tabs must not overwrite.
    page.get_by_role("link", name="ClearRewards Launch", exact=True).click()
    expect(page.locator("#decision-form")).to_be_visible()
    stale = page.context.new_page()
    stale.on("pageerror", lambda error: errors.append(str(error)))
    stale.goto(page.url)
    expect(stale.locator("#decision-form")).to_be_visible()
    feedback = '<img src=x onerror="window.bad=true"> Please revise the claim.'
    page.locator("#decision-comment").fill(feedback)
    page.route("**/request-changes", lambda route: route.abort())
    page.get_by_role("button", name="Request changes", exact=True).click()
    expect(page.locator("#notice")).to_contain_text("Unable to connect")
    expect(page.locator("#decision-comment")).to_have_value(feedback)
    page.unroute("**/request-changes")
    page.get_by_role("button", name="Request changes", exact=True).click()
    expect(page.locator("#record-status")).to_have_text("Changes requested")
    expect(page.locator("#timeline")).to_contain_text(feedback)
    expect(page.locator("#timeline img")).to_have_count(0)
    stale.locator("#decision-comment").fill("Feedback from a stale tab")
    stale.get_by_role("button", name="Request changes", exact=True).click()
    expect(stale.locator("#notice")).to_contain_text("This submission changed")
    expect(stale.locator("#decision-comment")).to_have_value("Feedback from a stale tab")
    expect(stale.get_by_role("button", name="Reload latest")).to_be_visible()
    stale.close()
    page.goto(BASE)
    page.get_by_role("button", name="Reset demo", exact=True).click()
    with page.expect_navigation(wait_until="networkidle"):
        page.get_by_role("button", name="Reset workspace", exact=True).click()
    expect(page.locator("#queue-body tr")).to_have_count(6)
    print("PASS: connection failure/retry, stale-tab feedback preservation, safe text rendering", flush=True)
    # Exercise real navigation with a small page size on the disposable seed.
    page.goto(BASE + "/static/index.html?limit=2")
    expect(page.locator("#queue-body tr")).to_have_count(2)
    expect(page.locator("#count")).to_contain_text("1–2 of 6")
    first_page = page.locator(".campaign-title").all_text_contents()
    page.get_by_role("link", name="Next", exact=True).click()
    expect(page.locator("#count")).to_contain_text("3–4 of 6")
    second_page = page.locator(".campaign-title").all_text_contents()
    assert not set(first_page) & set(second_page)
    page.locator(".campaign-title").first.click()
    expect(page.locator("#detail")).to_be_visible()
    page.locator("#back").click()
    expect(page.locator("#count")).to_contain_text("3–4 of 6")
    page.get_by_role("link", name="Previous", exact=True).click()
    expect(page.locator("#count")).to_contain_text("1–2 of 6")
    page.get_by_role("link", name="Next", exact=True).click()
    page.locator("#search").fill("ClearRewards Launch")
    page.get_by_role("button", name="Apply filters").click()
    expect(page.locator("#count")).to_contain_text("1–1 of 1")
    assert "offset=" not in page.url
    page.goto(BASE + "/static/index.html?limit=2&offset=4")
    expect(page.locator("#count")).to_contain_text("5–6 of 6")
    page.locator("#persona").select_option(label="Jessica Lin")
    expect(page.locator("#count")).to_contain_text("1–2 of 6")
    assert "offset=" not in page.url
    page.goto(BASE + "/static/index.html?limit=2&offset=999")
    expect(page.locator("#count")).to_contain_text("1–2 of 6")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    print("PASS: pagination, detail return, filter/persona reset, out-of-range recovery", flush=True)
    assert not errors, errors
    browser.close()
    print(
        "PASS: browser lifecycle, permissions, intake, filters, reset, mobile overflow, and JavaScript errors"
    )
