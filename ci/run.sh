#!/usr/bin/env bash
# Chạy bot trong RUN_MINUTES phút (mặc định 345), tự khởi động lại nếu bot bị văng,
# và cứ 5 phút lưu dữ liệu lên nhánh data một lần nếu có thay đổi.
set -u
cd "$(dirname "$0")/.."
: "${BOT_TOKEN:?Thiếu secret BOT_TOKEN}"

end=$(( $(date +%s) + ${RUN_MINUTES:-345} * 60 ))
rm -f .stop

(
  while [ ! -f .stop ]; do
    for _ in $(seq 60); do [ -f .stop ] && exit 0; sleep 5; done
    python3 ci/store.py save || echo "lưu giữa chừng lỗi, thử lại sau"
  done
) &
saver=$!

while :; do
  left=$(( end - $(date +%s) ))
  [ "$left" -le 30 ] && break
  timeout -k 60 "$left" python3 -u bot.py
  code=$?
  [ "$code" -eq 124 ] && break  # hết giờ: bot đã dừng êm
  echo "bot thoát với mã $code, khởi động lại sau 5 giây"
  sleep 5
done

touch .stop
wait "$saver"
