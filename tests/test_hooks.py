import json

import pytest

PANEL = "/data/runs/compare-20261108-abc123/panel.png"
GIF = "/data/runs/timelapse-20261108-def456/timelapse.gif"


def tool_text(path, **extra):
    return json.dumps({"headline": "x", **extra, "deliver": f"MEDIA:{path}",
                       "instruction": f"End your reply with this exact line: MEDIA:{path}"})


@pytest.fixture
def hooks(plugin):
    mod = plugin.hooks
    mod._pending.clear()
    yield mod
    mod._pending.clear()


def record(hooks, tool, result, session="s1", turn="s1:task:aa"):
    # the payload post_tool_call sends (model_tools._emit_post_tool_call_hook)
    hooks.record_media(tool, {}, result, task_id="task", session_id=session, tool_call_id="c1",
                       turn_id=turn, api_request_id="", duration_ms=5, status="ok")


def reply(hooks, text, session="s1", turn="s1:task:aa"):
    # the payload transform_llm_output sends (agent/turn_finalizer.apply_llm_output_transform)
    return hooks.append_missing_media(text, session_id=session, model="m", platform="cli",
                                      turn_id=turn)


def test_append_missing_media_adds_once(hooks):
    record(hooks, "overhead_compare", tool_text(PANEL))
    envelope = {"_multimodal": True, "content": [{"type": "text", "text": tool_text(GIF)}],
                "text_summary": tool_text(GIF)}
    record(hooks, "overhead_timelapse", envelope)

    text = f"About 4,500 ha burned.\n\nMEDIA:{GIF}"
    out = reply(hooks, text)
    assert out == f"{text}\n\nMEDIA:{PANEL}"
    assert out.count(f"MEDIA:{GIF}") == 1
    assert reply(hooks, text) is None  # record cleared


@pytest.mark.parametrize("text", [
    f'Done.\n"deliver": "MEDIA:{PANEL}"',            # copied from the JSON: masked by the gateway
    f"The panel is at MEDIA:{PANEL} if you want it.",  # mid-sentence
    f"> MEDIA:{PANEL}",                                 # blockquote: masked
    f"Here:\n```\nMEDIA:{PANEL}\n```",                   # fenced code: masked
    f"Done.\n{PANEL}",                                  # bare path, no tag
])
def test_undeliverable_forms_still_get_the_line(hooks, text):
    record(hooks, "overhead_compare", tool_text(PANEL))
    assert reply(hooks, text) == f"{text}\n\nMEDIA:{PANEL}"


def test_a_line_of_its_own_counts_as_present(hooks):
    record(hooks, "overhead_compare", tool_text(PANEL))
    assert reply(hooks, f"Done.\n  MEDIA:{PANEL}  \nBye.") is None


def test_record_media_ignores_other_tools(hooks):
    record(hooks, "image_generate", tool_text(PANEL))
    record(hooks, "overhead_locate", tool_text(PANEL))
    record(hooks, "overhead_compare", json.dumps({"error": {"type": "bad_input"}}))
    assert reply(hooks, "Done.") is None


def test_one_shot_run_without_session_id(hooks):
    record(hooks, "overhead_compare", tool_text(PANEL), session="", turn="session:t:01")
    assert reply(hooks, "Done.", session="", turn="session:t:01").endswith(f"\n\nMEDIA:{PANEL}")


def test_media_never_leaks_into_a_later_turn(hooks):
    record(hooks, "overhead_compare", tool_text(PANEL), turn="s1:task:aa")  # no final text
    assert reply(hooks, "Next answer.", turn="s1:task:bb") is None


def test_no_key_records_nothing(hooks):
    record(hooks, "overhead_compare", tool_text(PANEL), session="", turn="")
    assert len(hooks._pending) == 0


def test_record_media_keeps_last_50_turns(hooks):
    for i in range(55):
        record(hooks, "overhead_compare", tool_text(f"/r/{i}/panel.png"), turn=f"t{i}")
    assert len(hooks._pending) == 50
    assert reply(hooks, "Done.", turn="t0") is None
    assert reply(hooks, "Done.", turn="t54").endswith("MEDIA:/r/54/panel.png")
