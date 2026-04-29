from karen_finder.keywords import keyword_boost


def test_blacklist_drops_post():
    assert keyword_boost("local Karen murder shocker") is None


def test_strong_whitelist_boosts():
    # "karen" + "freakout" = 2 strong hits → boost = 2.0
    assert keyword_boost("Karen has total freakout") == 2.0


def test_context_words_smaller_boost():
    # "delivery" alone = 0.3
    assert abs(keyword_boost("guy yells at delivery driver") - 0.3) < 1e-9


def test_strong_plus_context_compounds():
    # "karen"(1.0) + "parking"(0.3) + "neighbor"(0.3) = 1.6
    assert abs(keyword_boost("Karen blocks neighbor's parking spot") - 1.6) < 1e-9


def test_soft_blacklist_downranks():
    # "karen"(1.0) + "drunk"(-1.5) = -0.5
    assert abs(keyword_boost("drunk karen freaking out") - -0.5) < 1e-9


def test_cap_applied():
    # Many strong hits should be capped.
    title = "karen hoa freakout entitled demanding ruined"
    # 6 strong hits would be 6.0; capped at 3.0
    assert keyword_boost(title, max_boost=3.0) == 3.0


def test_empty_title_safe():
    assert keyword_boost("") == 0.0


def test_case_insensitive():
    assert keyword_boost("KAREN") == keyword_boost("karen")
