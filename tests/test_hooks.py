import json

import pytest

PANEL = "/data/runs/compare-20261108-abc123/panel.png"
GIF = "/data/runs/timelapse-20261108-def456/timelapse.gif"


def tool_text(path, **extra):
    return json.dumps({"headline": "x", **extra, "deliver": f"MEDIA:{path}",
                       "instruction": "Include the deliver line verbatim at the end of your reply."})


@pytest.fixture
def hooks(plugin):
    mod = plugin.hooks
    mod._pending.clear()
    yield mod
    mod._pending.clear()


def test_append_missing_media_adds_once(hooks):
    hooks.record_media("overhead_compare", {}, tool_text(PANEL), task_id="t", session_id="s1")
    envelope = {"_multimodal": True, "content": [{"type": "text", "text": tool_text(GIF)}],
                "text_summary": f"headline\nMEDIA:{GIF}"}
    hooks.record_media("overhead_timelapse", {}, envelope, task_id="t", session_id="s1")

    reply = f"About 4,500 ha burned.\n\nMEDIA:{GIF}"
    out = hooks.append_missing_media(reply, session_id="s1", model="m", platform="cli")
    assert out == f"{reply}\n\nMEDIA:{PANEL}"
    assert out.count(f"MEDIA:{GIF}") == 1
    assert hooks.append_missing_media(reply, session_id="s1") is None  # record cleared


def test_append_missing_media_none_when_reply_has_everything(hooks):
    hooks.record_media("overhead_compare", {}, tool_text(PANEL), session_id="s1")
    assert hooks.append_missing_media(f"Done.\nMEDIA:{PANEL}", session_id="s1") is None
    assert hooks.append_missing_media("Done.", session_id="other") is None


def test_record_media_ignores_other_tools(hooks):
    hooks.record_media("image_generate", {}, tool_text(PANEL), session_id="s1")
    hooks.record_media("overhead_locate", {}, tool_text(PANEL), session_id="s1")
    hooks.record_media("overhead_compare", {}, json.dumps({"error": {"type": "bad_input"}}),
                       session_id="s1")
    assert hooks.append_missing_media("Done.", session_id="s1") is None


def test_record_media_falls_back_to_task_id(hooks):
    hooks.record_media("overhead_compare", {}, tool_text(PANEL), task_id="t9", session_id="")
    assert hooks.append_missing_media("Done.", session_id="t9").endswith(f"MEDIA:{PANEL}")


def test_record_media_keeps_last_50_sessions(hooks):
    for i in range(55):
        hooks.record_media("overhead_compare", {}, tool_text(f"/r/{i}/panel.png"), session_id=f"s{i}")
    assert len(hooks._pending) == 50
    assert hooks.append_missing_media("Done.", session_id="s0") is None
    assert hooks.append_missing_media("Done.", session_id="s54").endswith("MEDIA:/r/54/panel.png")
