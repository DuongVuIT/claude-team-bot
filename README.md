# Claude Team Bot

Bot Telegram quản lý slot Claude Team: đếm ngày còn lại, bảo hành, tiền hoàn, gia hạn. Mỗi sáng tự gửi báo cáo.

- Gói tháng 30 ngày, gói tuần 7 ngày.
- Tiền hoàn = giá × ngày còn lại ÷ tổng ngày (làm tròn 1.000đ), cộng phần còn lại của các kỳ gia hạn.
- Gia hạn khi còn hạn: nối tiếp ngày hết hạn cũ; đã hết hạn: tính từ hôm nay.

## Chạy

Chỉ cần Python 3.9+, không có thư viện ngoài.

```bash
cp .env.example .env        # điền BOT_TOKEN
cp data.example.json data.json
python3 -u bot.py
```

Chạy nền trên server (systemd): sửa đường dẫn trong `team-bot.service`, rồi

```bash
sudo cp team-bot.service /etc/systemd/system/ && sudo systemctl enable --now team-bot
```

`data.json` (dữ liệu khách) và `.env` (token) không được commit.
