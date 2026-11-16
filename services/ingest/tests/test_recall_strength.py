"""The forgetting curve (ingest/recall/strength.py)."""

import math
import random

import pytest
from ingest.recall.strength import Answer, Signals, Weights, after, fit, initial, loss, replay, retention

DAY = 86400.0


def test_stability_is_the_days_until_ninety_percent_and_the_tail_is_long() -> None:
    assert retention(0, 5) == 1.0
    assert retention(5, 5) == pytest.approx(0.9)
    assert retention(10, 1) < retention(10, 5) < retention(10, 30)
    # A power curve: after ten stabilities far more is left than an exponential through the same point keeps.
    assert retention(50, 5) == pytest.approx(0.546, abs=0.001) and retention(50, 5) > 0.9**10


def test_what_held_attention_feeling_novelty_or_importance_starts_stronger() -> None:
    plain = initial(Signals())
    assert plain == 1.0
    assert initial(Signals(attention=1)) == pytest.approx(math.e)
    assert initial(Signals(attention=1, feeling=1)) > initial(Signals(attention=1))
    assert initial(Signals(feeling=1)) == plain  # a feeling about what was not attended adds nothing
    assert initial(Signals(novelty=1)) > plain and initial(Signals(importance=1)) > initial(Signals(novelty=1))


def test_a_recall_gives_more_the_more_had_faded_and_a_question_more_than_a_look() -> None:
    assert after(10, 0.9, "remembered") == pytest.approx(12.0)
    assert after(10, 0.5, "remembered") == pytest.approx(20.0)
    assert after(10, 0.5, "partly") == pytest.approx(15.0)
    assert after(10, 0.5, "opened") == pytest.approx(13.0)
    assert after(10, 0.5, "forgot") == pytest.approx(5.0)


def test_an_episode_is_replayed_through_its_recalls_in_time_order() -> None:
    made = 1_000_000.0
    untouched = replay(4.0, made, [], made + 30 * DAY)
    assert untouched.recalls == 0 and untouched.retention == pytest.approx(retention(30, 4.0))
    recalled = replay(4.0, made, [(made + 20 * DAY, "remembered"), (made + 5 * DAY, "remembered")], made + 30 * DAY)
    assert recalled.recalls == 2 and recalled.stability > 4.0 and recalled.retention > untouched.retention
    assert recalled.since == made + 20 * DAY
    # A recall after "now" has not happened yet.
    assert replay(4.0, made, [(made + 40 * DAY, "remembered")], made + 30 * DAY) == untouched


def answers(truth: Weights, n: int, seed: int = 0) -> list[Answer]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        signals = Signals(rng.random(), rng.random(), rng.random(), rng.random())
        made, gap = 1_000_000.0 + i * DAY, rng.choice([1, 3, 7, 21, 60]) * DAY
        chance = replay(initial(signals, truth), made, [], made + gap, truth).retention
        out.append(Answer(signals, made, (), made + gap, 1.0 if rng.random() < chance else 0.0))
    return out


def test_weights_are_fitted_to_answers_and_kept_only_when_they_predict_new_ones_better() -> None:
    truth = Weights(s=0.4, a=2.5, d=0.2)  # this person forgets faster, and attention matters more
    seen = answers(truth, 600)
    fitted = fit(seen)
    fresh = answers(truth, 400, seed=1)
    assert loss(fresh, fitted) < loss(fresh, Weights()) - 0.01
    assert fitted.s < 1.0 and fitted.a > 1.0
    assert fit(seen[:30]) == Weights()  # too few answers: the defaults stay
    coin = [Answer(a.signals, a.made, (), a.t, float(i % 2)) for i, a in enumerate(answers(Weights(), 200))]
    assert loss(coin[150:], fit(coin)) <= loss(coin[150:], Weights())  # noise never makes it worse
