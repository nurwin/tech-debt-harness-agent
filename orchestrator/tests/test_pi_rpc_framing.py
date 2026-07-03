"""Phase C framing unit test — runs OFFLINE (no Pi, no Docker).

Proves the RPC reader splits on \\n ONLY: JSON payloads containing U+2028/U+2029
(which generic line iterators treat as line breaks) survive intact, per CLAUDE.md
hard rule 4.
"""
import subprocess
import sys

from src.executor_adapters.pi_adapter import PiRpcClient, harvest_usage

TRICKY_TEXT = "before middle after"  # LINE SEPARATOR + PARAGRAPH SEPARATOR

# A fake `pi --mode rpc`: echoes three LF-delimited JSONL events; the middle of the
# first one carries U+2028/U+2029 inside a JSON string (ensure_ascii=False, so the
# raw bytes are on the wire exactly as Pi would emit them).
FAKE_PI = (
    "import sys, json\n"
    "events = [\n"
    "    {'type': 'message', 'text': 'before\\u2028middle\\u2029after',\n"
    "     'usage': {'input_tokens': 100, 'output_tokens': 25}},\n"
    "    {'type': 'tool_call', 'tool': 'edit'},\n"
    "    {'type': 'agent_end'},\n"
    "]\n"
    "for e in events:\n"
    "    sys.stdout.write(json.dumps(e, ensure_ascii=False) + '\\n')\n"
    "sys.stdout.flush()\n"
)


def _fake_pi_proc() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", FAKE_PI],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)


def test_reader_splits_on_lf_only():
    client = PiRpcClient(_fake_pi_proc())
    events = [client.read_event() for _ in range(3)]
    assert [e["type"] for e in events] == ["message", "tool_call", "agent_end"]
    # the U+2028/U+2029 payload came through as ONE event, uncorrupted
    assert events[0]["text"] == TRICKY_TEXT


def test_generic_splitlines_would_corrupt_this_payload():
    """Sanity check that the hazard is real: str.splitlines DOES split on U+2028."""
    payload = '{"text": "' + TRICKY_TEXT + '"}'
    assert len(payload.splitlines()) == 3  # the trap a generic reader falls into
    assert len(payload.split("\n")) == 1  # our framing: one line, one event


def test_usage_harvest():
    client = PiRpcClient(_fake_pi_proc())
    total = sum(harvest_usage(client.read_event()) for _ in range(3))
    assert total == 125
