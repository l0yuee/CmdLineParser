"""性质测试：原文保持、区间可切片、确定性、截断稳定与输出有界。"""

from __future__ import annotations

import base64

from hypothesis import given
from hypothesis import strategies as st
from invariants import check_result

from cmdline_parser import AnalyzerOptions, analyze

_B64 = base64.b64encode(b"Write-Output hello").decode()
_B64_U16 = base64.b64encode("Write-Output hello".encode("utf-16le")).decode()

INTERESTING_CHARS = list(" \t\r\n|&;<>()[]{}'\"^`\\%=+-/_:.,?*$@!#~AaZz09xXuU") + [
    "\u3000",
    "\u00a0",
    "\u0301",
    "你",
    "😀",
    "Ｗ",
    "ß",
    "İ",
]
FRAGMENTS = [
    " ",
    "\t",
    '"',
    "'",
    "^",
    "`",
    "|",
    "&&",
    ">",
    "=",
    "%",
    "%41",
    "%e4%bd%a0",
    "%2",
    "\\x",
    "\\x41",
    "\\x4",
    "\\u",
    "\\u0041",
    "\\ud83d",
    "\\ude00",
    "-enc",
    "-EncodedCommand",
    "--data=",
    "netstat",
    "po",
    "wer",
    "shell",
    "C:\\Program Files\\",
    "SGVsbG8=",
    _B64,
    _B64_U16,
    _B64[:10],
    "ZABpAHIA",
]

texts = st.text(alphabet=st.sampled_from(INTERESTING_CHARS) | st.characters(), max_size=120)
fragment_texts = st.lists(st.sampled_from(FRAGMENTS), max_size=16).map("".join)
surrogate_texts = st.lists(
    st.sampled_from(["a", " ", "%41%42", "\\x41\\x42", '"']) | st.integers(0xD800, 0xDFFF).map(chr),
    max_size=12,
).map("".join)
any_text = texts | fragment_texts


@given(any_text)
def test_invariants(text: str) -> None:
    check_result(text, analyze(text))


@given(surrogate_texts)
def test_lone_surrogates_do_not_fail(text: str) -> None:
    check_result(text, analyze(text))


@given(any_text)
def test_deterministic(text: str) -> None:
    assert analyze(text).to_dict() == analyze(text).to_dict()


@given(any_text)
def test_decoding_does_not_change_root_layer(text: str) -> None:
    with_decode = analyze(text).to_dict()
    without = analyze(text, options=AnalyzerOptions(decode=False)).to_dict()
    for key in ("raw", "tokens", "groups", "symbols"):
        assert with_decode[key] == without[key]
    assert without["decoded_views"] == []


@given(any_text, st.data())
def test_terminated_tokens_are_prefix_stable(text: str, data) -> None:
    cut = data.draw(st.integers(0, len(text)))
    full = analyze(text)
    part = analyze(text[:cut])

    def stable(items):
        return [item.to_dict() for item in items if item.span[1] < cut]

    assert stable(full.tokens) == stable(part.tokens)
    assert stable(full.symbols) == stable(part.symbols)


options_strategy = st.builds(
    AnalyzerOptions,
    max_scan_chars=st.integers(0, 80),
    max_decode_depth=st.integers(0, 3),
    max_decode_attempts=st.integers(0, 8),
    max_candidate_chars=st.integers(0, 64),
    max_decoded_views=st.integers(0, 4),
    max_decoded_bytes=st.integers(0, 64),
    max_terms_per_token=st.integers(0, 6),
    max_output_items=st.integers(0, 20),
    max_notices=st.integers(0, 3),
    subwords=st.booleans(),
)


@given(fragment_texts | texts, options_strategy)
def test_limits_are_respected(text: str, options: AnalyzerOptions) -> None:
    check_result(text, analyze(text, options=options), options)


SAMPLES = [
    'tool.exe --output "C:\\Program Files\\Exam',
    "netstat -ano | findstr 443",
    "c^md /c \"ne^tstat -ano\" && echo 'done'",
    f"powershell -NoP -enc {_B64_U16}",
    f"curl --data={_B64} https://example.com/a%20b?x=%41%42",
    "echo \\x63\\x6d\\x64 \\u0063\\u006d\\ud83d\\ude00",
    "echo 你好 😀 cafe\u0301 Ｗｒｉｔｅ",
]


def test_every_prefix_of_samples_is_analyzed() -> None:
    for sample in SAMPLES:
        full = analyze(sample)
        for cut in range(len(sample) + 1):
            prefix = sample[:cut]
            result = analyze(prefix)
            check_result(prefix, result)
            # 截断不补全：前缀结果里不存在超出前缀的内容。
            assert all(t.span[1] <= cut for t in result.tokens)
            expected = [t.to_dict() for t in full.tokens if t.span[1] < cut]
            assert [t.to_dict() for t in result.tokens if t.span[1] < cut] == expected
