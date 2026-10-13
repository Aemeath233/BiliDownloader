from __future__ import annotations

import unittest

from bili_client.ui.add_task_page import AddTaskPage


class _FakeInput:
    def __init__(self, value: str) -> None:
        self.value = value

    def get(self, *_args) -> str:
        return self.value


class _FakeApi:
    def extract_url(self, text: str) -> str | None:
        return text.strip() or None


class AddTaskLogicTests(unittest.TestCase):
    def test_parse_result_is_rejected_after_input_changes(self) -> None:
        page = type(
            "FakePage",
            (),
            {"_parse_request_token": 10, "url_input": _FakeInput("https://example/new"), "api": _FakeApi()},
        )()

        is_current = AddTaskPage._parse_result_is_current(page, 10, "https://example/old")

        self.assertFalse(is_current)


if __name__ == "__main__":
    unittest.main()
