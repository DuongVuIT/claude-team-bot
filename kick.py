"""Kick khách khỏi Claude Team bằng trình duyệt thật (Playwright điều khiển Google Chrome).

Cần biến CLAUDE_SESSION_KEY = cookie `sessionKey` của tài khoản owner/admin team trên claude.ai.
Chỉ xoá member có vai trò User; owner/admin không bao giờ bị đụng tới.

Thử trên máy (chỉ kiểm tra, không xoá ai):
  pip install playwright
  python3 kick.py a@gmail.com b@gmail.com
"""
import os
import re
import sys

from playwright.sync_api import sync_playwright

BASE = "https://claude.ai"
MEMBERS_PAGE = BASE + "/admin-settings/members"


def kick(emails, dry=False):
    """Trả {email (chữ thường): "removed" | "absent" | "admin" | "present" (khi dry) | "lỗi: ..."}.
    Đăng nhập hỏng / bị chặn thì raise."""
    want = {x.strip().lower() for x in emails if x.strip()}
    with sync_playwright() as p:
        # thiếu cờ AutomationControlled là Cloudflare của claude.ai chặn ("Just a moment...")
        browser = p.chromium.launch(channel="chrome", headless=os.environ.get("KICK_HEADFUL") != "1",
                                    args=["--disable-blink-features=AutomationControlled"])
        # bỏ chữ "HeadlessChrome" trong user agent cho giống trình duyệt thường
        ua = browser.new_page().evaluate("navigator.userAgent").replace("HeadlessChrome", "Chrome")
        ctx = browser.new_context(user_agent=ua, locale="en-US")
        ctx.add_cookies([{"name": "sessionKey", "value": os.environ["CLAUDE_SESSION_KEY"].strip(),
                          "domain": ".claude.ai", "path": "/", "secure": True, "httpOnly": True}])
        page = ctx.new_page()
        page.goto(BASE + "/new")

        def api(path):
            return page.evaluate("""p => fetch(p).then(r => r.ok ? r.json() : r.text().then(t =>
                Promise.reject(new Error(r.status + ' ' + p + ' ' +
                    (r.headers.get('cf-mitigated') ? 'Cloudflare chặn' : t.slice(0, 150))))))""", path)

        try:
            org = next(o["uuid"] for o in api("/api/organizations") if o.get("raven_type") == "team")
        except StopIteration:
            raise RuntimeError("tài khoản này không thuộc team nào")
        except Exception as err:
            msg = str(err).splitlines()[0].removeprefix("Page.evaluate: Error: ")
            raise RuntimeError(f"không vào được claude.ai (cookie hết hạn hoặc bị chặn): {msg}")

        def members():
            return {m["account"]["email_address"].lower(): m for m in api(f"/api/organizations/{org}/members")}

        before = members()
        todo = [before[x]["account"]["email_address"] for x in want
                if x in before and before[x]["role"] == "user"]
        errors = {}
        if todo and not dry:
            ctx.add_cookies([{"name": "lastActiveOrg", "value": org, "url": BASE}])  # mở đúng team
            page.goto(MEMBERS_PAGE)
            search = page.get_by_placeholder("Search members")
            for email in todo:
                try:
                    search.fill(email)
                    more = page.get_by_role("button", name=f"More actions for {email}", exact=True)
                    more.click()
                    page.get_by_role("menuitem", name="Remove from team").click()
                    dialog = page.get_by_role("alertdialog").or_(page.get_by_role("dialog"))
                    try:  # hộp xác nhận (nếu có)
                        dialog.get_by_role("button", name=re.compile(r"remove|confirm", re.I)).click(timeout=5000)
                    except Exception:
                        pass
                    more.wait_for(state="detached", timeout=15000)
                except Exception as err:
                    errors[email.lower()] = str(err).splitlines()[0]
                    page.goto(MEMBERS_PAGE)
        after = members() if todo and not dry else before
        browser.close()

    out = {}
    for x in want:
        if x not in before:
            out[x] = "absent"
        elif before[x]["role"] != "user":
            out[x] = "admin"
        elif dry:
            out[x] = "present"
        elif x not in after:
            out[x] = "removed"
        else:
            out[x] = "lỗi: " + errors.get(x, "vẫn còn trong team")
    return out


if __name__ == "__main__":
    os.environ.setdefault("BOT_TOKEN", "-")  # chạy thử không cần token Telegram
    import bot  # noqa: F401  nạp .env

    res = kick(sys.argv[1:], dry=True)
    print("✅ Đăng nhập claude.ai OK, thấy team.")
    for email, r in res.items():
        print(f"  {email}: " + {"present": "còn trong team (sẽ bị kick nếu hết hạn)",
                                "absent": "không có trong team", "admin": "owner/admin, không đụng tới"}[r])
