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

## Chạy trên GitHub Actions

Workflow `.github/workflows/bot.yml` chạy bot liên tục: mỗi lượt ~5 tiếng 45 phút, lưu dữ liệu, rồi tự gọi lượt kế tiếp (lịch 6 tiếng chỉ để dự phòng). Chỉ một lượt chạy một lúc.

Dữ liệu khách (`data.json`, `state.json`) được mã hoá AES-256 và lưu ở nhánh `data` (file `store.enc`), cứ 5 phút lưu một lần nếu có thay đổi.

Cài đặt một lần:

1. Tạo khoá mã hoá trên máy: `openssl rand -base64 32 > .data_key && chmod 600 .data_key` (giữ file này cẩn thận, mất khoá là mất dữ liệu).
2. Đưa dữ liệu hiện có lên: `python3 ci/store.py save`.
3. Thêm secrets cho repo: `BOT_TOKEN`, `DATA_KEY` (nội dung `.data_key`), tuỳ chọn `OWNER_CHAT_ID`.
4. Chạy lượt đầu: `gh workflow run bot.yml`.

## Tự kick khách hết hạn

Đúng 00:00 (giờ VN) ngày hết hạn, bot mở Chrome (Playwright), vào claude.ai → Admin settings → Members và bấm **Remove from team** cho khách hết hạn hoặc đã hoàn tiền, rồi báo lại qua Telegram. Bỏ qua email còn gói khác đang chạy và mọi owner/admin. Khách đã kick hiện 🚪 trên thẻ; gia hạn lại thì bot nhắc mời lại vào team.

Cài đặt:

1. Mở Chrome đang đăng nhập tài khoản owner/admin team → claude.ai → DevTools (⌥⌘I) → Application → Cookies → `https://claude.ai` → copy giá trị `sessionKey`.
2. Thêm secret `CLAUDE_SESSION_KEY` cho repo với giá trị đó (chạy trên máy thì ghi vào `.env`).
3. Gõ `/kick` trong bot để xem trước ai sẽ bị kick (không xoá ai). Báo lỗi đăng nhập nghĩa là cookie hết hạn hoặc bị claude.ai chặn.

Lần đầu bật, bot đợi tới 00:00 kế tiếp mới kick. Cookie hết hạn thì bot báo lỗi và thử lại mỗi tiếng: lấy cookie mới, cập nhật secret, chạy lại workflow.

Khi bot đã chạy trên Actions thì **không chạy bot trên máy nữa** (hai bot cùng lấy tin sẽ lỗi và lệch dữ liệu). Muốn lấy dữ liệu mới nhất về máy: `python3 ci/store.py load`.
