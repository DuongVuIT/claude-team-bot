import json
import os
import socket
import unittest
from unittest import mock

with mock.patch.dict(os.environ, {"BOT_TOKEN": "test-token"}):
    import bot


class TelegramCallTests(unittest.TestCase):
    def response(self, status, raw):
        conn = mock.Mock()
        conn.getresponse.return_value.status = status
        conn.getresponse.return_value.read.return_value = raw
        return conn

    def test_rate_limit_preserves_retry_after(self):
        raw = json.dumps({"description": "Too Many Requests: retry after 5",
                          "parameters": {"retry_after": 5}}).encode()
        conn = self.response(429, raw)
        with mock.patch.object(bot, "conns", {}), \
                mock.patch.object(bot.http.client, "HTTPSConnection", return_value=conn):
            with self.assertRaises(bot.TelegramError) as raised:
                bot.call("getUpdates", {}, 15, "poll")
        self.assertEqual(raised.exception.retry_after, 5)
        self.assertIn("HTTP 429", str(raised.exception))

    def test_gateway_html_is_reported_as_api_error(self):
        conn = self.response(502, b"<html>Bad Gateway</html>")
        with mock.patch.object(bot, "conns", {}), \
                mock.patch.object(bot.http.client, "HTTPSConnection", return_value=conn):
            with self.assertRaises(bot.TelegramError) as raised:
                bot.call("getUpdates", {}, 15, "poll")
        self.assertEqual(raised.exception.retry_after, 0)
        self.assertIn("HTTP 502", str(raised.exception))

    def test_unchanged_message_still_succeeds(self):
        conn = self.response(400, b'{"description":"Bad Request: message is not modified"}')
        with mock.patch.object(bot, "conns", {}), \
                mock.patch.object(bot.http.client, "HTTPSConnection", return_value=conn):
            self.assertEqual(bot.call("editMessageText", {}, 30), {})

    def test_timeout_discards_connection_without_resending(self):
        conn = mock.Mock()
        conn.getresponse.side_effect = socket.timeout("timed out")
        with mock.patch.object(bot, "conns", {"send": conn}):
            with self.assertRaises(socket.timeout):
                bot.call("sendMessage", {}, 3)
            self.assertNotIn("send", bot.conns)
        conn.request.assert_called_once()
        conn.close.assert_called_once()


class PollingTests(unittest.TestCase):
    def run_bot(self, results, callback_error=None):
        results = iter(results)

        def poll(_offset):
            result = next(results)
            if isinstance(result, Exception):
                raise result
            bot.request_stop()
            return {"result": result}

        with mock.patch.dict(os.environ, {"OWNER_CHAT_ID": "42"}), \
                mock.patch.object(bot, "stopping", False), \
                mock.patch.object(bot.signal, "signal"), \
                mock.patch.object(bot.time, "sleep") as sleep, \
                mock.patch.object(bot, "tg_poll", side_effect=poll), \
                mock.patch.multiple(bot, read_json=mock.DEFAULT, write_json=mock.DEFAULT,
                                    load=mock.DEFAULT, call=mock.DEFAULT, tg=mock.DEFAULT,
                                    on_callback=mock.DEFAULT) as mocks:
            mocks["read_json"].return_value = {"last_report": bot.today().isoformat()}
            mocks["tg"].side_effect = callback_error
            bot.main()
        return mocks, sleep

    def test_poll_waits_for_telegram_retry_after(self):
        err = bot.TelegramError("getUpdates", 429, {"parameters": {"retry_after": 5}})
        _, sleep = self.run_bot([err, []])
        sleep.assert_called_once_with(5)

    def test_poll_timeout_reconnects_without_extra_sleep(self):
        _, sleep = self.run_bot([socket.timeout("timed out"), []])
        sleep.assert_not_called()

    def test_callback_is_processed_after_ack_timeout(self):
        update = {"update_id": 7, "callback_query": {
            "id": "callback-id", "data": "list", "message": {
                "message_id": 9, "chat": {"id": 42}}}}
        mocks, _ = self.run_bot([[update]], callback_error=socket.timeout("timed out"))
        mocks["tg"].assert_called_once_with("answerCallbackQuery", timeout=3,
                                             callback_query_id="callback-id")
        mocks["on_callback"].assert_called_once_with("42", 9, "list")
        self.assertEqual(mocks["write_json"].call_args.args[1]["offset"], 8)


if __name__ == "__main__":
    unittest.main()
