from app.channels import channels_for_prompt, raw_channels


def test_raw_channels_loads_nonempty() -> None:
    channels = raw_channels()
    assert len(channels) > 50
    assert "gas" in channels
    assert "speedKmh" in channels
    assert channels["speedKmh"]["label"] == "Speed"


def test_channels_for_prompt_is_flat_name_equals_label() -> None:
    text = channels_for_prompt()
    lines = text.splitlines()
    # One line per channel, nothing else.
    assert len(lines) == len(raw_channels())
    assert "gas = Throttle" in lines
    assert "speedKmh = Speed" in lines
    assert "tyreTempFL = Tyre Temp Core FL" in lines


def test_channels_for_prompt_has_no_units_or_categories() -> None:
    # We deliberately drop unit brackets + category headings — the LLM only
    # needs column names + labels to decide, and keeping units out prevents
    # the agent from copying them into the signal field.
    text = channels_for_prompt()
    assert "[km/h]" not in text
    assert "[°C]" not in text
    assert "## Inputs" not in text
    assert "## Tyres" not in text


def test_channels_for_prompt_size_reasonable() -> None:
    # ~160 lines × ~30 chars is expected; ceiling is generous.
    assert len(channels_for_prompt()) < 10_000
