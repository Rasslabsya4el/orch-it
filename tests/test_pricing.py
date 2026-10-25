"""Prices of a turn from the price file (#393 decision 6): per-kind rates, the 1-hour cache
write apart, tiers by prompt size, and no number at all for a model the file lacks. Expected
dollars are worked by hand from the rates in prices.json."""

from orch_it.events.pricing import price_turn


def usage(input=0, output=0, cache_read=0, cache_write=0, cache_write_1h=0):
    return {"input_tokens": input, "output_tokens": output, "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write, "cache_write_1h_tokens": cache_write_1h}


def test_a_turn_prices_each_kind_at_its_rate_and_the_1h_write_apart():
    # opus 5.5: 10 in x 4 + 100 out x 20 + 1000 read x 0.2 + 200 1h-write x 8 = 3840 per million
    usd, table = price_turn("claude-opus-5-5", usage(10, 100, 1000, 200, 200))
    assert round(usd, 9) == 0.00384 and table.startswith("prices-")


def test_the_5_minute_write_is_the_write_minus_its_1h_part():
    # sonnet 5.5: 300 write of which 100 at 1h: 200 x 2.5 + 100 x 4 = 900 per million
    usd, _ = price_turn("claude-sonnet-5-5", usage(cache_write=300, cache_write_1h=100))
    assert round(usd, 9) == 0.0009


def test_a_tiered_model_prices_the_whole_turn_at_the_tier_its_prompt_reaches():
    # haiku 5.5, prompt 103 <= 100000: 3 x 0.1 + 30 x 0.5 + 100 x 0.125 = 27.8 per million
    usd, _ = price_turn("claude-haiku-5-5", usage(3, 30, 0, 100))
    assert round(usd, 9) == 0.0000278
    # prompt 150000 > 100000: 150000 x 0.5 + 1000 out x 2.5 = 77500 per million
    usd, _ = price_turn("claude-haiku-5-5", usage(150_000, 1000))
    assert round(usd, 9) == 0.0775


def test_a_model_absent_from_the_file_has_no_price():
    assert price_turn("claude-opus-9", usage(10, 10))[0] is None


def test_a_1h_write_on_a_model_with_no_1h_rate_has_no_price_rather_than_a_guess():
    assert price_turn("claude-opus-5", usage(cache_write=10, cache_write_1h=10))[0] is None
    # without any 1h part the same model prices: 10 x 6.25 = 62.5 per million
    assert round(price_turn("claude-opus-5", usage(cache_write=10))[0], 9) == 0.0000625
