from app import front_matter

HANDOUT = """[Page 1]
RETREAT WEEKS
WEEK 3 / resting in God

RETREAT WEEKS / WEEK 3
Resting in God

We are made for rest in God, and yet the week crowds in on every side. This week
we practise stopping: a few minutes of stillness each morning, noticing what we carry
and setting it down, one thing at a time, before we begin to pray with the passage.
Some days will feel easy and others will not; both are part of learning to rest.

I pray for the following graces: to rest in God's care;
to trust that I am held

[Page 2]
RETREAT WEEKS
WEEK 3 / resting in God
WEEK 3/ DAY 1: Psalm 131
My heart is not proud, nor are my eyes haughty.
"""
PLAN = {"mode": "follows_source", "days": [{"day": 1, "passage_text": "My heart is not proud, nor are my eyes haughty."}]}


def test_the_introduction_and_graces_before_day_one_are_kept_word_for_word():
    fm = front_matter.extract(HANDOUT, PLAN)
    assert fm["text"].startswith("We are made for rest in God, and yet the week crowds in on every side. This week we practise")
    assert "RETREAT WEEKS" not in fm["text"] and "Resting in God\n" not in fm["text"]
    assert fm["graces"] == "to rest in God's care; to trust that I am held"


def test_graces_alone_count_and_a_composed_plan_has_none():
    only = HANDOUT.replace(HANDOUT[HANDOUT.index("We are made"):HANDOUT.index("I pray")], "")
    assert front_matter.extract(only, PLAN) == {"text": "", "graces": "to rest in God's care; to trust that I am held"}
    assert front_matter.extract(HANDOUT, {**PLAN, "mode": "composed"}) is None
