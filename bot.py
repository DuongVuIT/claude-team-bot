"""Bot quản lý slot Claude Team: đếm ngày còn lại, bảo hành, tiền hoàn, gia hạn.

Chỉ dùng thư viện chuẩn Python 3.9+. Cấu hình qua .env cạnh file:
  BOT_TOKEN=...
  OWNER_CHAT_ID=...   (bỏ trống: người /start đầu tiên thành chủ bot; nhiều người: cách nhau dấu phẩy)
  REPORT_HOUR=8       (giờ báo sáng, giờ Việt Nam)
"""
import html
import http.client
import json
import os
import re
import secrets
import signal
import socket
import time
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data.json")
STATE = os.path.join(HERE, "state.json")
VN = timezone(timedelta(hours=7))
PER_PAGE = 8
PRICE_PRESETS = [150000, 390000, 430000, 1800000]


def load_env():
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
TOKEN = os.environ["BOT_TOKEN"]
REPORT_HOUR = int(os.environ.get("REPORT_HOUR", "8"))
e = html.escape


# ---------- lưu trữ ----------

def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load():
    data = read_json(DATA, [])
    changed = False
    for c in data:
        if "id" not in c:
            c["id"] = secrets.token_hex(3)
            changed = True
        c.setdefault("history", [])
        c.setdefault("note", "")
    if changed:
        save(data)
    return data


def save(data):
    write_json(DATA, data)


def by_id(data, cid):
    return next((c for c in data if c["id"] == cid), None)


# ---------- Telegram ----------

conns = {}  # kênh ("send" / "poll") -> kết nối HTTPS giữ sẵn, đỡ bắt tay SSL mỗi lần gọi


def call(method, params, timeout, lane="send"):
    body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
    for _ in range(2):
        conn = conns.get(lane)
        reused = conn is not None
        if not reused:
            conn = conns[lane] = http.client.HTTPSConnection("api.telegram.org", timeout=timeout)
        conn.timeout = timeout
        try:
            if conn.sock:
                conn.sock.settimeout(timeout)
            conn.request("POST", f"/bot{TOKEN}/{method}", body, {"Content-Type": "application/json"})
            r = conn.getresponse()
            raw = r.read()
            break
        except (http.client.HTTPException, OSError) as err:
            conn.close()
            conns.pop(lane, None)
            # kết nối cũ bị server đóng: mở lại thử 1 lần; timeout thì không thử lại (tránh gửi trùng)
            if not reused or isinstance(err, socket.timeout):
                raise
    if r.status >= 400:
        msg = raw.decode(errors="replace")
        if "message is not modified" in msg:
            return {}
        raise RuntimeError(f"{method}: {msg}")
    return json.loads(raw)


def tg(method, timeout=30, **params):
    return call(method, params, timeout)


def send(chat, text, kb=None):
    while text:
        cut = text if len(text) <= 4000 else text[: text.rfind("\n", 0, 4000)]
        text = text[len(cut):].lstrip("\n")
        tg("sendMessage", chat_id=chat, text=cut, parse_mode="HTML",
           disable_web_page_preview=True, reply_markup=None if text else kb)


def edit(chat, mid, text, kb=None):
    try:
        tg("editMessageText", chat_id=chat, message_id=mid, text=text, parse_mode="HTML",
           disable_web_page_preview=True, reply_markup=kb or {"inline_keyboard": []})
    except RuntimeError:  # tin quá cũ / quá dài: gửi tin mới
        send(chat, text, kb)


def btn(text, data):
    return {"text": text, "callback_data": data}


def ikb(*rows):
    return {"inline_keyboard": [r for r in rows if r]}


MENU = {"keyboard": [["📋 Báo cáo", "👥 Danh sách"], ["➕ Thêm khách", "❓ Hướng dẫn"]],
        "resize_keyboard": True, "is_persistent": True}


# ---------- tính toán ----------

def today():
    return datetime.now(VN).date()


def periods(c):
    """Mọi kỳ đã trả tiền (cũ + hiện tại) dạng (bắt đầu, số ngày, giá)."""
    for p in c["history"] + [c]:
        yield date.fromisoformat(p["start"]), p["days"], p["price"]


def end_of(c):
    return date.fromisoformat(c["start"]) + timedelta(days=c["days"])


def left_of(c, on=None):
    if c.get("refunded"):
        return 0
    return max(0, (end_of(c) - (on or today())).days)


def refund_of(c, on=None):
    if c.get("refunded"):
        return 0
    on = on or today()
    total = 0.0
    for start, days, price in periods(c):
        remain = max(0, min(days, (start + timedelta(days=days) - on).days))
        total += price * remain / days
    return int(round(total / 1000) * 1000)


def paid_of(c):
    return sum(p for _, _, p in periods(c))


def status(c):
    if c.get("refunded"):
        return "refunded"
    left = left_of(c)
    return "expired" if left == 0 else "soon" if left <= 3 else "active"


ICON = {"active": "🟢", "soon": "🟡", "expired": "🔴", "refunded": "⚪"}
ORDER = {"soon": 0, "active": 1, "expired": 2, "refunded": 3}


def sort_key(c):
    s = status(c)
    d = end_of(c).toordinal()
    return ORDER[s], -d if s in ("expired", "refunded") else d


def vnd(n):
    return f"{int(n):,}".replace(",", ".") + "đ"


def dmy(d):
    return d.strftime("%d/%m/%Y")


def plan(days):
    return {30: "tháng", 7: "tuần"}.get(days, f"{days} ngày")


def parse_money(s):
    s = s.lower().replace(" ", "").replace("đ", "").replace("vnd", "")
    m = re.fullmatch(r"(\d+)(?:[.,](\d+))?tr(\d*)", s)
    if m:
        return int(float(f"{m[1]}.{m[2] or m[3] or 0}") * 1_000_000)
    m = re.fullmatch(r"(\d+(?:[.,]\d+)?)k", s)
    if m:
        return int(float(m[1].replace(",", ".")) * 1000)
    s = s.replace(".", "").replace(",", "")
    if s.isdigit():
        return int(s)
    raise ValueError


def parse_date(s):
    s = s.strip().lower()
    if s in ("hôm nay", "hom nay", "hn", "nay"):
        return today()
    if s in ("hôm qua", "hom qua", "hq"):
        return today() - timedelta(days=1)
    parts = re.split(r"[/\-.]", s)
    if len(parts) == 2:
        parts.append(str(today().year))
    d, m, y = (int(x) for x in parts)
    return date(y + 2000 if y < 100 else y, m, d)


# ---------- màn hình ----------

def card_text(c, extra=""):
    s, left = status(c), left_of(c)
    warranty = {"active": "còn bảo hành", "soon": "sắp hết", "expired": "hết bảo hành",
                "refunded": "đã hoàn tiền"}[s]
    lines = [f"{ICON[s]} <b>{e(c['name'])}</b>",
             f"📧 <code>{e(c['email'])}</code>",
             f"📦 Gói {plan(c['days'])} · {vnd(c['price'])}",
             f"📅 {dmy(date.fromisoformat(c['start']))} → {dmy(end_of(c))}",
             f"⏳ Còn <b>{left}</b> ngày · 🛡 {warranty}"]
    if s == "refunded":
        r = c["refunded"]
        lines.append(f"💸 Đã hoàn {vnd(r['amount'])} ngày {dmy(date.fromisoformat(r['on']))}")
    else:
        lines.append(f"💸 Hoàn nếu hủy hôm nay: <b>{vnd(refund_of(c))}</b>")
    if c["history"]:
        lines.append(f"🔁 Đã gia hạn {len(c['history'])} lần · tổng đã thu {vnd(paid_of(c))}")
    if c["note"]:
        lines.append(f"📝 {e(c['note'])}")
    return "\n".join(lines) + (f"\n\n{extra}" if extra else "")


def card_kb(c):
    i = c["id"]
    if c.get("refunded"):
        return ikb([btn("↩️ Bỏ đánh dấu đã hoàn", f"u:{i}")],
                   [btn("🗑 Xóa", f"d:{i}"), btn("⬅️ Danh sách", "l:all:0")])
    return ikb([btn("🔁 +1 tuần", f"r:{i}:7"), btn("🔁 +1 tháng", f"r:{i}:30")],
               [btn("✏️ Sửa", f"e:{i}"), btn("💸 Hoàn tiền", f"h:{i}")],
               [btn("🗑 Xóa", f"d:{i}"), btn("⬅️ Danh sách", "l:all:0")])


def row_btn(c):
    s = status(c)
    tail = (f"còn {left_of(c)} ngày" if s in ("active", "soon")
            else f"hết {end_of(c):%d/%m}" if s == "expired" else "đã hoàn")
    return [btn(f"{ICON[s]} {c['name']} · {tail}", f"c:{c['id']}")]


FILTERS = [("all", "Tất cả"), ("active", "Còn hạn"), ("soon", "Sắp hết"),
           ("expired", "Hết hạn"), ("refunded", "Đã hoàn")]


def list_view(f="all", page=0):
    data = load()

    def match(c):
        s = status(c)
        return (s != "refunded") if f == "all" else (s in ("active", "soon")) if f == "active" else s == f

    rows = sorted(filter(match, data), key=sort_key)
    pages = max(1, -(-len(rows) // PER_PAGE))
    page = max(0, min(page, pages - 1))
    label = dict(FILTERS)[f]
    kb = [row_btn(c) for c in rows[page * PER_PAGE:(page + 1) * PER_PAGE]]
    tabs = [btn(("• " if k == f else "") + v, f"l:{k}:0") for k, v in FILTERS]
    kb += [tabs[:3], tabs[3:]]
    if pages > 1:
        kb.append([btn("◀️", f"l:{f}:{page - 1}"), btn(f"{page + 1}/{pages}", "noop"),
                   btn("▶️", f"l:{f}:{page + 1}")])
    kb.append([btn("➕ Thêm khách", "a")])
    text = (f"👥 <b>Danh sách — {label}</b> ({len(rows)} khách)\n"
            "Bấm vào khách để xem, gia hạn, sửa hoặc xóa.\n"
            "Muốn tìm nhanh: gõ tên hoặc email vào chat.")
    return text, {"inline_keyboard": kb}


def report():
    data = load()
    live = [c for c in data if not c.get("refunded")]
    rows = sorted(live, key=sort_key)
    soon = [c for c in rows if status(c) == "soon"]
    active = [c for c in rows if status(c) == "active"]
    expired = [c for c in rows if status(c) == "expired"]
    recent_expired = [c for c in expired if (today() - end_of(c)).days <= 14]
    out = [f"☀️ <b>Báo cáo Claude Team — {dmy(today())}</b>",
           f"🟢 Còn hạn {len(active) + len(soon)} · 🟡 Sắp hết {len(soon)} · 🔴 Hết hạn {len(expired)}",
           f"💰 Đang chạy: đã thu {vnd(sum(paid_of(c) for c in active + soon))} · "
           f"hoàn nếu hủy hết {vnd(sum(refund_of(c) for c in rows))}"]
    if soon:
        out.append("\n<b>🟡 Sắp hết hạn — nhắc khách gia hạn</b>")
        for c in soon:
            left = left_of(c)
            when = "hôm nay" if left == 0 else "ngày mai" if left == 1 else f"còn {left} ngày"
            out.append(f"• <b>{e(c['name'])}</b> — hết {dmy(end_of(c))} ({when})")
    if active:
        out.append("\n<b>🟢 Còn bảo hành</b>")
        for c in active:
            out.append(f"• {e(c['name'])} — còn <b>{left_of(c)}</b> ngày "
                       f"(→ {end_of(c):%d/%m}) · hoàn {vnd(refund_of(c))}")
    if recent_expired:
        out.append("\n<b>🔴 Hết hạn gần đây</b>")
        for c in recent_expired:
            out.append(f"• {e(c['name'])} — hết {dmy(end_of(c))}")
    if len(expired) > len(recent_expired):
        out.append(f"(+{len(expired) - len(recent_expired)} khách hết hạn lâu hơn, xem ở Danh sách)")
    kb = [row_btn(c) for c in soon + recent_expired]
    kb.append([btn("👥 Mở danh sách", "l:all:0")])
    return "\n".join(out), {"inline_keyboard": kb}


HELP = """<b>Cách dùng</b>
• <b>📋 Báo cáo</b> — tổng hợp hạn dùng (tự gửi mỗi sáng {h}h)
• <b>👥 Danh sách</b> — bấm vào khách để 🔁 gia hạn, ✏️ sửa, 💸 hoàn tiền, 🗑 xóa
• <b>➕ Thêm khách</b> — bot hỏi từng bước, có nút chọn sẵn
• Gõ <b>tên hoặc email</b> bất kỳ để tìm khách nhanh

<b>Quy tắc</b>
• Gói tháng 30 ngày, gói tuần 7 ngày
• Tiền hoàn = giá × ngày còn lại ÷ tổng ngày (làm tròn 1.000đ)
• Gia hạn khi còn hạn: nối tiếp từ ngày hết hạn cũ; đã hết hạn: tính từ hôm nay

<b>Thêm nhanh 1 dòng</b>
<code>/them Tên | email | 17/10 | thang | 430k</code>
Giá gõ kiểu 430k, 430.000, 1tr8 đều được. /huy để hủy thao tác đang làm."""


# ---------- xử lý ----------

pending = {}  # chat -> thao tác đang chờ nhập


def show_card(chat, c, mid=None, extra=""):
    (edit if mid else send)(*((chat, mid) if mid else (chat,)), card_text(c, extra), card_kb(c))


def renew_start(c):
    if c.get("refunded"):
        return today()
    return max(today(), end_of(c))


def renew_screen(c, days):
    start = renew_start(c)
    text = card_text(c, f"🔁 <b>Gia hạn +{plan(days)}</b>: {dmy(start)} → "
                        f"{dmy(start + timedelta(days=days))}\nChọn giá khách đã trả:")
    prices = ([c["price"]] if c["days"] == days else []) + PRICE_PRESETS
    prices = list(dict.fromkeys(prices))
    rows = [[btn(vnd(p) + (" (giá cũ)" if i == 0 and c["days"] == days else ""),
                 f"R:{c['id']}:{days}:{p}")] for i, p in enumerate(prices)]
    rows.append([btn("✏️ Giá khác", f"rp:{c['id']}:{days}"), btn("⬅️ Hủy", f"c:{c['id']}")])
    return text, {"inline_keyboard": rows}


def do_renew(data, c, days, price):
    start = renew_start(c)
    if not c.get("refunded"):
        c["history"].append({"start": c["start"], "days": c["days"], "price": c["price"]})
    c.pop("refunded", None)
    c.update(start=start.isoformat(), days=days, price=price)
    save(data)


FIELD_PROMPT = {
    "name": "Nhập <b>tên</b> mới:",
    "email": "Nhập <b>email</b> mới:",
    "start": "Nhập <b>ngày bắt đầu</b> (vd 17/10 hoặc 17/10/2026):",
    "days": "Chọn <b>gói</b> hoặc gõ số ngày:",
    "price": "Nhập <b>giá</b> (vd 430k, 1tr8):",
    "note": "Nhập <b>ghi chú</b> (gõ - để xóa ghi chú):",
}


def field_kb(cid, field):
    rows = []
    if field == "days":
        rows.append([btn("Tuần (7 ngày)", f"ev:{cid}:days:7"), btn("Tháng (30 ngày)", f"ev:{cid}:days:30")])
    elif field == "start":
        rows.append([btn("Hôm nay", f"ev:{cid}:start:{today().isoformat()}"),
                     btn("Hôm qua", f"ev:{cid}:start:{(today() - timedelta(days=1)).isoformat()}")])
    elif field == "price":
        rows.append([btn(vnd(p), f"ev:{cid}:price:{p}") for p in PRICE_PRESETS])
    rows.append([btn("⬅️ Hủy", f"c:{cid}")])
    return {"inline_keyboard": rows}


def apply_field(c, field, raw):
    """Trả về lỗi (str) hoặc None khi đã gán."""
    raw = raw.strip()
    try:
        if field == "name":
            if not raw:
                return "Tên không được trống."
            c["name"] = raw
        elif field == "email":
            if "@" not in raw or " " in raw:
                return "Email không hợp lệ."
            c["email"] = raw
        elif field == "start":
            c["start"] = (date.fromisoformat(raw) if re.fullmatch(r"\d{4}-\d\d-\d\d", raw)
                          else parse_date(raw)).isoformat()
        elif field == "days":
            raw = {"tuan": "7", "tuần": "7", "thang": "30", "tháng": "30"}.get(raw.lower(), raw)
            days = int(raw)
            if not 1 <= days <= 3650:
                return "Số ngày không hợp lệ."
            c["days"] = days
        elif field == "price":
            c["price"] = parse_money(raw)
        elif field == "note":
            c["note"] = "" if raw == "-" else raw
    except ValueError:
        return {"start": "Ngày không hợp lệ, gõ kiểu 17/10 hoặc 17/10/2026.",
                "price": "Giá không hợp lệ, gõ kiểu 430k hoặc 430000.",
                "days": "Gõ số ngày, vd 30."}.get(field, "Giá trị không hợp lệ.")
    return None


# Các bước thêm khách
ADD_STEPS = ["name", "email", "start", "days", "price"]
ADD_PROMPT = {
    "name": "➕ <b>Thêm khách</b> (1/5)\nNhập <b>tên khách</b>:",
    "email": "➕ (2/5) Nhập <b>email</b>:",
    "start": "➕ (3/5) <b>Ngày bắt đầu</b>? Bấm nút hoặc gõ (vd 17/10):",
    "days": "➕ (4/5) <b>Gói</b>?",
    "price": "➕ (5/5) <b>Giá</b> khách đã trả? Bấm nút hoặc gõ (vd 430k, 1tr8):",
}


def add_kb(step):
    rows = []
    if step == "start":
        rows.append([btn("Hôm nay", f"av:start:{today().isoformat()}"),
                     btn("Hôm qua", f"av:start:{(today() - timedelta(days=1)).isoformat()}")])
    elif step == "days":
        rows.append([btn("Tuần (7 ngày)", "av:days:7"), btn("Tháng (30 ngày)", "av:days:30")])
    elif step == "price":
        rows.append([btn(vnd(p), f"av:price:{p}") for p in PRICE_PRESETS])
    rows.append([btn("❌ Hủy", "x")])
    return {"inline_keyboard": rows}


def add_next(chat, p, mid=None):
    """Chuyển sang bước tiếp theo của wizard thêm khách."""
    draft = p["c"]
    step = next((s for s in ADD_STEPS if s not in draft), None)
    p["step"] = step
    if step:
        (edit if mid else send)(*((chat, mid) if mid else (chat,)), ADD_PROMPT[step], add_kb(step))
        return
    preview = {**draft, "history": [], "note": "", "id": "new"}
    text = card_text(preview, "Lưu khách này?")
    kb = ikb([btn("✅ Lưu", "A"), btn("❌ Hủy", "x")])
    (edit if mid else send)(*((chat, mid) if mid else (chat,)), text, kb)


def search(q):
    q = q.lower().strip()
    return [c for c in load() if q in c["email"].lower() or q in c["name"].lower()]


def on_text(chat, text):
    text = text.strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()

    if cmd in ("/start", "/help", "❓") or text == "❓ Hướng dẫn":
        pending.pop(chat, None)
        send(chat, "🤖 <b>Bot quản lý Claude Team</b>\n\n" + HELP.format(h=REPORT_HOUR), MENU)
        if cmd == "/start":
            send(chat, *report())
        return
    if cmd == "/huy":
        pending.pop(chat, None)
        send(chat, "Đã hủy.", MENU)
        return
    if cmd == "/baocao" or text == "📋 Báo cáo":
        pending.pop(chat, None)
        send(chat, *report())
        return
    if cmd in ("/ds", "/danhsach") or text == "👥 Danh sách":
        pending.pop(chat, None)
        send(chat, *list_view())
        return
    if cmd == "/them" and "|" in arg:
        parts = [x.strip() for x in arg.split("|")]
        if len(parts) != 5:
            send(chat, "Sai cú pháp. Ví dụ:\n<code>/them Tên | email | 17/10 | thang | 430k</code>")
            return
        c = {"id": secrets.token_hex(3), "history": [], "note": ""}
        for field, raw in zip(ADD_STEPS, parts):
            err = apply_field(c, field, raw)
            if err:
                send(chat, err)
                return
        data = load()
        data.append(c)
        save(data)
        show_card(chat, c, extra="✅ Đã thêm.")
        return
    if cmd == "/them" or text == "➕ Thêm khách":
        pending[chat] = {"act": "add", "c": {}}
        add_next(chat, pending[chat])
        return
    if cmd == "/hoan":
        text = arg

    p = pending.get(chat)
    if p and p["act"] == "add":
        draft = {}
        err = apply_field(draft, p["step"], text)
        if err:
            send(chat, err, add_kb(p["step"]))
            return
        p["c"].update(draft)
        add_next(chat, p)
        return
    if p and p["act"] == "edit":
        data = load()
        c = by_id(data, p["id"])
        if not c:
            pending.pop(chat, None)
            return
        err = apply_field(c, p["field"], text)
        if err:
            send(chat, err, field_kb(c["id"], p["field"]))
            return
        save(data)
        pending.pop(chat, None)
        show_card(chat, c, extra="✅ Đã cập nhật.")
        return
    if p and p["act"] == "renew_price":
        data = load()
        c = by_id(data, p["id"])
        try:
            price = parse_money(text)
        except ValueError:
            send(chat, "Giá không hợp lệ, gõ kiểu 430k hoặc 430000.")
            return
        pending.pop(chat, None)
        if c:
            do_renew(data, c, p["days"], price)
            show_card(chat, c, extra="✅ Đã gia hạn.")
        return

    if not text or text.startswith("/"):
        send(chat, HELP.format(h=REPORT_HOUR), MENU)
        return
    hits = search(text)
    if not hits:
        send(chat, f"Không tìm thấy khách nào khớp “{e(text)}”.")
    elif len(hits) == 1:
        show_card(chat, hits[0])
    else:
        send(chat, f"🔍 {len(hits)} khách khớp “{e(text)}”:",
             {"inline_keyboard": [row_btn(c) for c in sorted(hits, key=sort_key)[:20]]})


def on_callback(chat, mid, data_str):
    parts = data_str.split(":")
    kind = parts[0]
    data = load()
    c = by_id(data, parts[1]) if len(parts) > 1 and kind not in ("l", "av") else None

    if kind == "noop":
        return
    if kind == "l":
        pending.pop(chat, None)
        edit(chat, mid, *list_view(parts[1], int(parts[2])))
    elif kind == "a":
        pending[chat] = {"act": "add", "c": {}}
        add_next(chat, pending[chat], mid)
    elif kind == "av":
        p = pending.get(chat)
        if not p or p["act"] != "add":
            return
        apply_field(p["c"], parts[1], parts[2])
        add_next(chat, p, mid)
    elif kind == "A":
        p = pending.pop(chat, None)
        if not p or p["act"] != "add":
            return
        c = {"id": secrets.token_hex(3), "history": [], "note": "", **p["c"]}
        data.append(c)
        save(data)
        show_card(chat, c, mid, "✅ Đã thêm.")
    elif kind == "x":
        pending.pop(chat, None)
        edit(chat, mid, "Đã hủy.")
    elif not c:
        edit(chat, mid, "Khách này không còn trong danh sách.", ikb([btn("⬅️ Danh sách", "l:all:0")]))
    elif kind == "c":
        pending.pop(chat, None)
        show_card(chat, c, mid)
    elif kind == "r":
        edit(chat, mid, *renew_screen(c, int(parts[2])))
    elif kind == "R":
        do_renew(data, c, int(parts[2]), int(parts[3]))
        show_card(chat, c, mid, "✅ Đã gia hạn.")
    elif kind == "rp":
        pending[chat] = {"act": "renew_price", "id": c["id"], "days": int(parts[2])}
        edit(chat, mid, card_text(c, f"Gõ giá gia hạn +{plan(int(parts[2]))} (vd 430k):"),
             ikb([btn("⬅️ Hủy", f"c:{c['id']}")]))
    elif kind == "e":
        i = c["id"]
        edit(chat, mid, card_text(c, "✏️ Sửa mục nào?"),
             ikb([btn("Tên", f"ef:{i}:name"), btn("Email", f"ef:{i}:email")],
                 [btn("Ngày bắt đầu", f"ef:{i}:start"), btn("Gói", f"ef:{i}:days")],
                 [btn("Giá", f"ef:{i}:price"), btn("Ghi chú", f"ef:{i}:note")],
                 [btn("⬅️ Quay lại", f"c:{i}")]))
    elif kind == "ef":
        pending[chat] = {"act": "edit", "id": c["id"], "field": parts[2]}
        edit(chat, mid, card_text(c, "✏️ " + FIELD_PROMPT[parts[2]]), field_kb(c["id"], parts[2]))
    elif kind == "ev":
        pending.pop(chat, None)
        apply_field(c, parts[2], parts[3])
        save(data)
        show_card(chat, c, mid, "✅ Đã cập nhật.")
    elif kind == "h":
        amount = refund_of(c)
        edit(chat, mid, card_text(c, f"💸 Xác nhận đã hoàn <b>{vnd(amount)}</b> cho khách? "
                                     "Khách sẽ chuyển sang mục “Đã hoàn”."),
             ikb([btn(f"✅ Đã hoàn {vnd(amount)}", f"H:{c['id']}:{amount}")],
                 [btn("⬅️ Hủy", f"c:{c['id']}")]))
    elif kind == "H":
        c["refunded"] = {"on": today().isoformat(), "amount": int(parts[2])}
        save(data)
        show_card(chat, c, mid, "✅ Đã ghi nhận hoàn tiền.")
    elif kind == "u":
        c.pop("refunded", None)
        save(data)
        show_card(chat, c, mid, "↩️ Đã bỏ đánh dấu hoàn tiền.")
    elif kind == "d":
        edit(chat, mid, card_text(c, "🗑 <b>Xóa hẳn khách này?</b> Không khôi phục được."),
             ikb([btn("🗑 Xóa luôn", f"D:{c['id']}"), btn("⬅️ Hủy", f"c:{c['id']}")]))
    elif kind == "D":
        data.remove(c)
        save(data)
        text, kb = list_view()
        edit(chat, mid, f"🗑 Đã xóa {e(c['name'])}.\n\n" + text, kb)


# ---------- vòng lặp ----------

stopping = False  # nhận SIGTERM/SIGINT: xử lý xong lượt hiện tại rồi thoát


def request_stop(*_):
    global stopping
    stopping = True


def main():
    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    state = read_json(STATE, {})
    owners = {x.strip() for x in os.environ.get("OWNER_CHAT_ID", "").split(",") if x.strip()}
    if not owners and state.get("owner"):
        owners = {str(state["owner"])}
    offset = state.get("offset", 0)
    load()  # gán id cho dữ liệu cũ
    print("bot started, owners =", len(owners), flush=True)
    while not stopping:
        now = datetime.now(VN)
        if owners and now.hour >= REPORT_HOUR and state.get("last_report") != now.date().isoformat():
            try:
                for o in owners:
                    send(o, *report())
                state["last_report"] = now.date().isoformat()
                write_json(STATE, state)
            except Exception as err:  # mạng lỗi: thử lại vòng sau
                print("report failed:", err, flush=True)
        try:
            res = tg_poll(offset)
        except Exception as err:
            print("poll failed:", err, flush=True)
            if not isinstance(err, socket.timeout):  # treo/timeout: poll lại ngay
                time.sleep(3)
            continue
        for u in res.get("result", []):
            offset = state["offset"] = u["update_id"] + 1
            cq = u.get("callback_query")
            msg = (cq or {}).get("message") or u.get("message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            if not chat:
                continue
            if not owners and not cq and (msg.get("text") or "").startswith("/start"):
                owners = {chat}
                state["owner"] = chat
                state["last_report"] = now.date().isoformat()  # /start đã kèm báo cáo
            if chat not in owners:
                continue  # chỉ chủ bot được dùng
            try:
                if cq:
                    try:
                        tg("answerCallbackQuery", callback_query_id=cq["id"])
                    except Exception:
                        pass
                    on_callback(chat, msg["message_id"], cq.get("data") or "")
                elif msg.get("text"):
                    on_text(chat, msg["text"])
            except Exception as err:
                print("handle failed:", repr(err), flush=True)
                try:
                    send(chat, "⚠️ Có lỗi, thử lại giúp mình.")
                except Exception:
                    pass
        write_json(STATE, state)
    # báo Telegram đã nhận hết tin, để lần chạy sau không xử lý lại
    if offset:
        try:
            call("getUpdates", {"offset": offset, "timeout": 0}, 10, "poll")
        except Exception as err:
            print("confirm failed:", err, flush=True)
    print("bot stopped", flush=True)


def tg_poll(offset):
    # long-poll ngắn (10s) để kết nối bị treo được bỏ sớm; timeout HTTP (15s) phải lớn hơn
    return call("getUpdates", {"offset": offset, "timeout": 10,
                               "allowed_updates": ["message", "callback_query"]}, 15, "poll")


if __name__ == "__main__":
    main()
