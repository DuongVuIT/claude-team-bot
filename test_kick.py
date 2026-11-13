"""Kiểm tra chọn đúng khách cần kick: python3 test_kick.py"""
import os
from datetime import timedelta

os.environ.setdefault("BOT_TOKEN", "test")
import bot  # noqa: E402

t = bot.today()


def cust(name, email, start_days_ago, days=30, **kw):
    return {"id": name, "name": name, "email": email, "start": (t - timedelta(days=start_days_ago)).isoformat(),
            "days": days, "price": 430000, "history": [], "note": "", **kw}


ends_today = cust("A", "a@x.com", 30)                       # hạn đến hôm nay -> kick
active = cust("B", "b@x.com", 5)                            # còn hạn -> giữ
old_dup = cust("C1", "C@x.com", 60)                         # hết hạn nhưng cùng email
new_dup = cust("C2", "c@x.com", 2)                          # còn gói mới -> giữ cả email
kicked = cust("D", "d@x.com", 40, kicked=(t - timedelta(days=10)).isoformat())  # kick rồi -> bỏ
rekick = cust("E", "e@x.com", 31, kicked=(t - timedelta(days=20)).isoformat())  # gia hạn rồi hết lại -> kick
refunded = cust("F", "f@x.com", 3, refunded={"on": (t - timedelta(days=1)).isoformat(), "amount": 1})  # hoàn -> kick
refund_kicked = cust("G", "g@x.com", 3, refunded={"on": (t - timedelta(days=1)).isoformat(), "amount": 1},
                     kicked=t.isoformat())                  # hoàn + kick rồi -> bỏ

data = [ends_today, active, old_dup, new_dup, kicked, rekick, refunded, refund_kicked]
got = sorted(c["name"] for c in bot.kick_targets(data))
assert got == ["A", "E", "F"], got
assert bot.is_kicked(kicked) and not bot.is_kicked(rekick)
print("ok", got)
