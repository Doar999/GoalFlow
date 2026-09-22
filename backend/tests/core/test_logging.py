"""结构化日志。每条必须带 request_id，见 03-code-and-test-standards.md 第 5 节。"""

import json
import logging

from goalflow.core.context import bind_request_id, reset_request_id
from goalflow.core.logging import JsonFormatter


def _record(**extra):
    record = logging.LogRecord("goalflow.test", logging.INFO, __file__, 1, "已提交作业", None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_log_line_is_single_line_json():
    line = JsonFormatter().format(_record())

    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["message"] == "已提交作业"


def test_request_id_is_attached_when_bound():
    token = bind_request_id("req_abc")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        reset_request_id(token)

    assert payload["request_id"] == "req_abc"


def test_request_id_absent_outside_request_context():
    assert "request_id" not in json.loads(JsonFormatter().format(_record()))


def test_extra_fields_are_kept():
    payload = json.loads(JsonFormatter().format(_record(job_id="job_1")))

    assert payload["job_id"] == "job_1"


def test_chinese_is_not_escaped():
    # 转义后的日志在 grep 与告警里都不可读。
    assert "已提交作业" in JsonFormatter().format(_record())
