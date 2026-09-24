from __future__ import annotations

import base64

import pytest

from cmdline_parser.decoders import DecodeFailure, DecodeSuccess
from cmdline_parser.decoders import base64_decoder as b64
from cmdline_parser.decoders import escape_decoder as esc
from cmdline_parser.decoders import percent_decoder as pct
from cmdline_parser.decoders._text import decode_text_prefix, is_plausible_text, looks_utf16le

PAYLOAD = "Write-Output hello"


def _b64(text: str, charset: str = "utf-8") -> str:
    return base64.b64encode(text.encode(charset)).decode("ascii")


def _ok(outcome) -> DecodeSuccess:
    assert isinstance(outcome, DecodeSuccess), outcome
    return outcome


def _fail(outcome) -> DecodeFailure:
    assert isinstance(outcome, DecodeFailure), outcome
    return outcome


# ---------------------------------------------------------------- 文本识别


def test_decode_text_prefix_complete_and_partial() -> None:
    assert decode_text_prefix("你好".encode(), "utf-8") == ("你好", True)
    assert decode_text_prefix("你好".encode()[:-1], "utf-8") == ("你", False)
    assert decode_text_prefix(b"a\xffb", "utf-8") is None
    assert decode_text_prefix(b"\xef\xbb\xbfabc", "utf-8") == ("abc", True)
    assert decode_text_prefix("ab".encode("utf-16le")[:-1], "utf-16le") == ("a", False)
    assert decode_text_prefix(b"\xff\xfea\x00", "utf-16le") == ("a", True)


def test_utf16le_heuristic() -> None:
    assert looks_utf16le("hello".encode("utf-16le"))
    assert looks_utf16le(b"\xff\xfe")
    assert not looks_utf16le(b"hello world")
    assert not looks_utf16le(b"\x00\x00\x00\x00")


def test_plausible_text() -> None:
    assert is_plausible_text("abcd", min_chars=4)
    assert not is_plausible_text("abc", min_chars=4)
    assert not is_plausible_text("ab\x00cd", min_chars=1)
    assert is_plausible_text("line1\r\nline2\tx", min_chars=4)
    assert not is_plausible_text("\x01\x02\x03abc", min_chars=1)
    assert not is_plausible_text("", min_chars=0)


# ---------------------------------------------------------------- Base64


def test_base64_utf8() -> None:
    value = _b64(PAYLOAD)
    assert b64.detect(value)
    outcome = _ok(b64.decode(value))
    assert (outcome.text, outcome.status, outcome.encoding, outcome.charset, outcome.rule) == (
        PAYLOAD,
        "complete",
        "base64",
        "utf-8",
        "D-B64-001",
    )


def test_base64url_variant() -> None:
    raw = "Write-Output hello?>>"
    value = base64.urlsafe_b64encode(raw.encode()).decode("ascii").rstrip("=")
    assert "-" in value or "_" in value
    outcome = _ok(b64.decode(value))
    assert outcome.encoding == "base64url" and outcome.rule == "D-B64URL-001"
    assert outcome.text == raw


def test_mixed_alphabets_are_rejected() -> None:
    assert not b64.detect("SGVsbG8+/_-SGVsbG8gV29y")


def test_base64_utf16le_general_detection() -> None:
    value = _b64(PAYLOAD, "utf-16le")
    outcome = _ok(b64.decode(value))
    assert outcome.charset == "utf-16le" and outcome.text == PAYLOAD


def test_base64_utf16le_hinted_short_payload() -> None:
    value = _b64("dir", "utf-16le")  # ZABpAHIA
    assert not b64.detect(value)
    assert b64.detect(value, True)
    outcome = _ok(b64.decode(value, True))
    assert (outcome.text, outcome.charset) == ("dir", "utf-16le")


def test_base64_truncated_is_partial() -> None:
    value = _b64(PAYLOAD)[:-3]
    outcome = _ok(b64.decode(value))
    assert outcome.status == "partial"
    assert PAYLOAD.startswith(outcome.text) and len(outcome.text) < len(PAYLOAD)


def test_base64_truncated_mid_multibyte_char_is_partial() -> None:
    value = _b64("Write-Output 你好世界")
    cut = value[: len(value) - 4]
    outcome = _ok(b64.decode(cut))
    assert outcome.status == "partial"
    assert "Write-Output 你好世界".startswith(outcome.text)


def test_base64_non_canonical_padding_bits() -> None:
    assert b64.detect("SGVsbG9=")
    assert _fail(b64.decode("SGVsbG9=")).reason == "non_canonical"
    assert _ok(b64.decode("SGVsbG8=")).text == "Hello"


@pytest.mark.parametrize(
    "value",
    [
        "SGVsbG8==",  # 长度不是 4 的倍数
        "SGVs=bG8",  # padding 在中间
        "SGVsbG8",  # 太短
        "0123456789abcdef0123456789abcdef",  # 裸十六进制
        "d41d8cd98f00b204e9800998ecf8427e",  # MD5
        "averylongidentifiername",  # 只有小写
        "AVERYLONGIDENTIFIERNAME",  # 只有大写
        "C:\\Windows\\System32",
    ],
)
def test_base64_rejected_shapes(value: str) -> None:
    assert not b64.detect(value)


@pytest.mark.parametrize("value", ["--EncodedCommand", "-ExecutionPolicy", "/usr/local/bin/myScript"])
def test_base64_shaped_words_fail_silently(value: str) -> None:
    # 字符集与长度符合 Base64 的选项名、路径会被尝试，但不产生文本也不报告。
    assert b64.detect(value)
    assert _fail(b64.decode(value)).notable is False


def test_base64_binary_result_is_not_text() -> None:
    value = base64.b64encode(bytes(range(0, 256, 7))).decode("ascii")
    assert b64.detect(value)
    failure = _fail(b64.decode(value))
    assert failure.reason == "not_text" and failure.notable is False


def test_base64_hinted_failure_is_notable() -> None:
    value = base64.b64encode(b"\xff\xfe\x00\xd8\x00\xd8").decode("ascii")
    failure = _fail(b64.decode(value, True))
    assert failure.notable is True


def test_base64_hinted_truncated_without_text_is_silent() -> None:
    # 只有半个 UTF-16 代码单元：属于截断，不是解码错误。
    failure = _fail(b64.decode("ZA==", True))
    assert (failure.reason, failure.notable) == ("truncated", False)


# ---------------------------------------------------------------- 百分号


def test_percent_basic() -> None:
    outcome = _ok(pct.decode("%63%6d%64"))
    assert (outcome.text, outcome.status, outcome.encoding, outcome.rule) == (
        "cmd",
        "complete",
        "percent",
        "D-PCT-001",
    )


def test_percent_plus_is_kept() -> None:
    assert _ok(pct.decode("a+b%20c")).text == "a+b c"


def test_percent_literal_percent_is_kept() -> None:
    assert _ok(pct.decode("100%zz%41")).text == "100%zzA"


@pytest.mark.parametrize(("value", "text"), [("%63%6d%6", "cm"), ("cmd%20%", "cmd "), ("%41%A", "A")])
def test_percent_truncated_tail_is_partial(value: str, text: str) -> None:
    outcome = _ok(pct.decode(value))
    assert (outcome.text, outcome.status) == (text, "partial")


def test_percent_truncated_utf8_is_partial() -> None:
    outcome = _ok(pct.decode("%E4%BD%A0%E5%A5"))
    assert (outcome.text, outcome.status) == ("你", "partial")


@pytest.mark.parametrize("value", ["%E4%BD", "%E4%BD%A"])
def test_percent_truncated_without_text_is_silent(value: str) -> None:
    failure = _fail(pct.decode(value))
    assert (failure.reason, failure.notable) == ("truncated", False)


def test_percent_utf8() -> None:
    assert _ok(pct.decode("%E4%BD%A0%E5%A5%BD")).text == "你好"


def test_percent_invalid_utf8_fails_without_splicing() -> None:
    failure = _fail(pct.decode("ab%FFcd%41"))
    assert failure.reason == "invalid_utf8" and failure.notable


def test_percent_variable_reference_is_silent() -> None:
    assert pct.detect("%DATE%")
    assert _fail(pct.decode("%DATE%")).notable is False


def test_percent_detect() -> None:
    assert not pct.detect("100%")
    assert not pct.detect("%A")
    assert not pct.detect("%zz")


# ---------------------------------------------------------------- 转义


def test_hex_escape_basic() -> None:
    outcome = _ok(esc.decode_hex(r"\x63\x6d\x64"))
    assert (outcome.text, outcome.status, outcome.encoding, outcome.rule, outcome.charset) == (
        "cmd",
        "complete",
        "hex_escape",
        "D-ESC-X-001",
        "utf-8",
    )


def test_hex_escape_in_place() -> None:
    assert _ok(esc.decode_hex(r"run:\x63\x6d\x64.exe")).text == "run:cmd.exe"


def test_single_hex_escape_like_path_does_not_trigger() -> None:
    assert not esc.detect_hex(r"C:\x64")
    assert not esc.detect_hex(r"C:\x64\bin")
    assert _ok(esc.decode_hex(r"C:\x64\x41\x42")).text == "C:dAB"
    assert _ok(esc.decode_hex(r"C:\x64\bin\x41\x42")).text == r"C:\x64\binAB"


def test_hex_escape_truncated() -> None:
    outcome = _ok(esc.decode_hex(r"\x63\x6d\x6"))
    assert (outcome.text, outcome.status) == ("cm", "partial")
    outcome = _ok(esc.decode_hex(r"\x63\x"))
    assert (outcome.text, outcome.status) == ("c", "partial")
    assert not esc.detect_hex(r"\x6")


def test_hex_escape_trailing_backslash() -> None:
    # 连续单元后的孤立反斜杠视为被截断的单元；单个单元加反斜杠证据不足。
    outcome = _ok(esc.decode_hex("\\x63\\x6d\\"))
    assert (outcome.text, outcome.status) == ("cm", "partial")
    assert not esc.detect_hex("C:\\x64\\")
    assert not esc.detect_unicode("C:\\u0041\\")


def test_hex_escape_utf8_multibyte() -> None:
    assert _ok(esc.decode_hex(r"\xe4\xbd\xa0\xe5\xa5\xbd")).text == "你好"
    outcome = _ok(esc.decode_hex(r"\xe4\xbd\xa0\xe5\xa5"))
    assert (outcome.text, outcome.status) == ("你", "partial")


def test_hex_escape_utf16le() -> None:
    outcome = _ok(esc.decode_hex(r"\x63\x00\x6d\x00\x64\x00"))
    assert (outcome.text, outcome.charset) == ("cmd", "utf-16le")


def test_hex_escape_invalid_bytes() -> None:
    failure = _fail(esc.decode_hex(r"\xff\xfe\xfd"))
    assert failure.notable


@pytest.mark.parametrize("value", [r"\xe4\xbd", r"\xe4\xbd\xa"])
def test_hex_escape_truncated_without_text_is_silent(value: str) -> None:
    failure = _fail(esc.decode_hex(value))
    assert (failure.reason, failure.notable) == ("truncated", False)


def test_unicode_escape_truncated_without_text_is_silent() -> None:
    value = r"\ud83d\ude0"
    assert esc.detect_unicode(value)
    failure = _fail(esc.decode_unicode(value))
    assert (failure.reason, failure.notable) == ("truncated", False)


def test_unicode_escape_basic() -> None:
    outcome = _ok(esc.decode_unicode(r"\u0063\u006d\u0064"))
    assert (outcome.text, outcome.encoding, outcome.rule, outcome.charset) == (
        "cmd",
        "unicode_escape",
        "D-ESC-U-001",
        "utf-16",
    )


def test_unicode_escape_surrogate_pair() -> None:
    assert _ok(esc.decode_unicode(r"\ud83d\ude00\u0041")).text == "😀A"


def test_unicode_escape_lone_surrogate_fails() -> None:
    assert _fail(esc.decode_unicode(r"\ude00\u0041")).reason == "lone_surrogate"
    assert _fail(esc.decode_unicode(r"\ud83d\u0041")).reason == "lone_surrogate"
    assert _fail(esc.decode_unicode(r"\u0041\ud83d x\u0042\u0043")).reason == "lone_surrogate"


def test_unicode_escape_dangling_high_surrogate_at_end_is_partial() -> None:
    outcome = _ok(esc.decode_unicode(r"\u0041\ud83d"))
    assert (outcome.text, outcome.status) == ("A", "partial")


def test_unicode_escape_truncated_unit() -> None:
    outcome = _ok(esc.decode_unicode(r"\u0041\u00"))
    assert (outcome.text, outcome.status) == ("A", "partial")
    outcome = _ok(esc.decode_unicode(r"\u0041\u0042\u"))
    assert (outcome.text, outcome.status) == ("AB", "partial")


def test_single_unicode_escape_does_not_trigger() -> None:
    assert not esc.detect_unicode(r"C:\u0041bc")
    assert not esc.detect_unicode(r"\users\name")
