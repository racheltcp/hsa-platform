"""End-to-end check in a real (headless) browser: every flow, the error paths,
desktop and phone widths, and what the page says when the server goes away.

Optional extra, not needed to run the app:
    pip install playwright && python -m playwright install chromium
    python scripts/e2e_check.py
It starts its own server on a temporary database, so your data/ folder is untouched.
"""
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
PORT = 8765
base = f"http://127.0.0.1:{PORT}"
server = subprocess.Popen([sys.executable, "run.py", "--port", str(PORT),
                           "--db", str(Path(tempfile.mkdtemp()) / "e2e.db")],
                          cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.2).close()
        break
    except OSError:
        time.sleep(0.1)

errors, checks = [], []


def check(name, cond, info=""):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


try:
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1440, "height": 900})
        pg.on("console", lambda m: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base); pg.wait_for_timeout(800)
        check("welcome shown on empty db", pg.is_visible("#welcome"))
        # --- race scenario
        pg.click("[data-action=scenario]"); pg.wait_for_timeout(1800)
        summary = pg.inner_text(".tl-summary")
        check("race: 1 approved 1 declined", "1 approved" in summary and "1 declined" in summary, summary)
        check("race: two timeline rows", pg.locator("#race-result .tl-row").count() == 2)
        # --- new account dialog with inline errors
        pg.click("#btn-new-account")
        pg.fill("#form-account [name=holder_name]", "Rachel"); pg.fill("#form-account [name=email]", "rachel@example.com")
        pg.fill("#form-account [name=date_of_birth]", "2999-01-01"); pg.click("#form-account [type=submit]"); pg.wait_for_timeout(400)
        err = pg.locator("#form-account .field-error")
        check("dialog: future DOB error shown inline", err.count() == 1 and "past" in err.inner_text(), err.all_inner_texts())
        check("dialog: error is next to DOB field", pg.get_attribute("#form-account [name=date_of_birth]", "aria-invalid") == "true")
        pg.fill("#form-account [name=date_of_birth]", "1999-03-02")
        check("dialog: typing clears the error", pg.locator("#form-account .field-error").count() == 0)
        pg.click("#form-account [type=submit]"); pg.wait_for_timeout(600)
        check("dialog: account created and selected", pg.inner_text("#acct-name") == "Rachel")
        # --- gates without a card
        check("gate: swipe locked without card", pg.is_visible("#h-purchase ~ .surface .gate") and pg.evaluate("document.querySelector('#form-purchase fieldset').disabled"))
        pg.click("#h-purchase ~ .surface .gate [data-action=issue]"); pg.wait_for_timeout(600)
        check("gate: issuing from the gate unlocks swiping", not pg.is_visible("#h-purchase ~ .surface .gate") and not pg.evaluate("document.querySelector('#form-purchase fieldset').disabled"))
        check("card: full number shown once", len(pg.inner_text(".card-number").replace(" ", "")) == 16)
        # --- deposit errors inline
        pg.fill("#form-deposit [name=amount]", "5000"); pg.click("#form-deposit [type=submit]"); pg.wait_for_timeout(400)
        t = pg.locator("#form-deposit .field-error")
        check("deposit: over-limit error inline", t.count() == 1 and "$4,400.00" in t.inner_text(), t.all_inner_texts())
        pg.fill("#form-deposit [name=amount]", "100"); pg.click("#form-deposit [type=submit]"); pg.wait_for_timeout(500)
        check("deposit: balance $100", pg.inner_text("#acct-balance") == "$100.00")
        # --- swipe
        pg.fill("#form-purchase [name=amount]", "1.234"); pg.click("#form-purchase [type=submit]"); pg.wait_for_timeout(400)
        check("swipe: fraction-of-cent error inline", "cent" in pg.inner_text("#form-purchase"))
        pg.fill("#form-purchase [name=amount]", "25"); pg.click("#form-purchase [type=submit]"); pg.wait_for_timeout(500)
        check("swipe: pharmacy approved, 5 checks passed", "Approved" in pg.inner_text(".verdict-stamp") and pg.locator(".slip .checks li.passed").count() == 5)
        pg.click("[data-preset='4']"); pg.fill("#form-purchase [name=amount]", "12"); pg.click("#form-purchase [type=submit]"); pg.wait_for_timeout(500)
        statuses = pg.eval_on_selector_all(".slip .checks li", "els => els.map(e => e.className)")
        check("swipe: restaurant fails check 4, skips 5", statuses == ["passed", "passed", "passed", "failed", "skipped"], statuses)
        # --- race lab helpers
        pg.click("[data-preset='0']")
        pg.fill("#form-race [name=amounts]", "10, 20"); pg.wait_for_timeout(200)
        check("race lab: warns when balance covers everything", pg.is_visible("#btn-make-race"))
        pg.click("#btn-make-race"); pg.wait_for_timeout(200)
        check("race lab: suggestion creates contention", "some must be declined" in pg.inner_text("#contention"), pg.inner_text("#contention"))
        pg.fill("#form-race [name=amounts]", "abc"); pg.click("#form-race [type=submit]"); pg.wait_for_timeout(300)
        check("race lab: bad amounts error inline", pg.locator("#form-race .field-error").count() == 1)
        pg.fill("#form-race [name=amounts]", "10"); pg.fill("#form-race [name=repeat]", "12"); pg.click("#form-race [type=submit]"); pg.wait_for_timeout(2500)
        check("race lab: 12 rows, 7 approved ($75 / $10)", pg.locator("#race-result .tl-row").count() == 12 and "7 approved" in pg.inner_text(".tl-summary"), pg.inner_text(".tl-summary"))
        check("race lab: never below zero", "never below zero" in pg.inner_text(".tl-summary"))
        # --- freeze
        pg.click("[data-card=freeze]"); pg.wait_for_timeout(500)
        pg.fill("#form-race [name=amounts]", "1"); pg.wait_for_timeout(100)
        check("frozen: race lab explains the card is frozen", "frozen" in pg.inner_text("#contention"))
        pg.fill("#form-purchase [name=amount]", "1"); pg.click("#form-purchase [type=submit]"); pg.wait_for_timeout(500)
        check("frozen: swipe declined at check 2", pg.eval_on_selector_all(".slip .checks li", "els => els.map(e => e.className)")[:2] == ["passed", "failed"])
        pg.click("[data-card=unfreeze]"); pg.wait_for_timeout(400)
        # --- ledger
        pg.locator("[data-trace]").first.click(); pg.wait_for_timeout(400)
        check("ledger: show checks expands a trace", pg.locator("tr.trace-row .checks li").count() == 5)
        # --- xss
        pg.click("#btn-new-account"); pg.fill("#form-account [name=holder_name]", "<img src=x onerror=alert(1)>")
        pg.fill("#form-account [name=email]", "x@example.com"); pg.click("#form-account [type=submit]"); pg.wait_for_timeout(500)
        check("xss: name rendered as text", pg.locator("img[src=x]").count() == 0 and "<img" in pg.inner_text("#acct-name"))
        pg.click("#btn-new-account"); pg.fill("#form-account [name=holder_name]", "Dup"); pg.fill("#form-account [name=email]", "rachel@example.com")
        pg.click("#form-account [type=submit]"); pg.wait_for_timeout(400)
        check("dialog: duplicate email error on the email field", "already exists" in pg.inner_text("#form-account") and pg.get_attribute("#form-account [name=email]", "aria-invalid") == "true")
        pg.click("#btn-cancel-account")
        # --- mobile
        m = b.new_page(viewport={"width": 390, "height": 844}); m.goto(base); m.wait_for_timeout(900)
        check("mobile: no horizontal overflow", not m.evaluate("document.documentElement.scrollWidth > innerWidth"))
        m.click("#btn-scenario"); m.wait_for_timeout(1800)
        check("mobile: race renders without overflow", m.locator("#race-result .tl-row").count() == 2 and not m.evaluate("document.documentElement.scrollWidth > innerWidth"))
        # --- server goes away
        server.terminate(); server.wait(); pg.wait_for_timeout(300)
        pg.fill("#form-deposit [name=amount]", "5"); pg.click("#form-deposit [type=submit]"); pg.wait_for_timeout(600)
        check("server down: clear message", "Can't reach the server" in pg.inner_text("#toasts"), pg.inner_text("#toasts"))
        b.close()
    check("no console errors", not errors, errors)
    passed = sum(ok for _, ok in checks)
    print(f"\n{passed}/{len(checks)} checks passed")

finally:
    if server.poll() is None:
        server.terminate()
sys.exit(0 if passed == len(checks) else 1)
