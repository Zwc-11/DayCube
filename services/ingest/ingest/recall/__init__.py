"""Remembering the day (docs/recall.md): a day cut into episodes, each with a strength that fades
with time, starts higher when it held attention, feeling, novelty or importance, and grows each
time it is recalled.

    strength   the curve: how much of an episode is still there, and what a recall does to it
    episodes   a day's timeline cut into episodes, each with its signals (vault/<date>/episodes.json)
    history    every recall made (vault/recall/log.jsonl), each episode's strength now, and the
               curve's weights fitted to the person's own answers
    today      the morning's questions, the day's cue cards, and the nightly round

Everything here is computed from files in the vault and can be made again from them: the episodes
from the day's timeline and records, the strengths from the episodes and the log.
"""
