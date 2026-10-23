"""Which memory keeps the most of a life at a fixed byte budget?

A synthetic life follows the exploration-and-preferential-return model of human mobility (Song et
al., Nature Physics 2010): a person goes home, to work, and to other places, returning to a place
in proportion to how often they went before and exploring a new one with probability
``rho * S**-gamma`` after ``S`` distinct places. Each place has a persistent state (``SLOTS``
facts, each one of ``VALUES``) that changes as a Poisson process. Each visit yields one
observation embedding: the place's look, its state, the time-of-day light at that place, maybe a
passer-by, and sensor noise.

Every policy stores the same thing per item (the embedding, place id and time), gets the same
budget of items, and answers queries the same way: decode the queried place's latest stored
observation at or before the visit, else its earliest after. Only *which observations it keeps*
differs, so the comparison isolates the write policy.

The question: does deciding what to keep by comparing a visit with the place's own history (and,
in ``place_predict``, a learned prediction of it) beat time-ordered and random memories at the
same budget, and where does each stop working? Run ``python eval/memory_sim.py --help``.
"""

from __future__ import annotations

import argparse
import heapq
import json
import os
import time
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace

import numpy as np

DIM, SLOTS, VALUES = 512, 8, 16
PLACE_NORM = 3.0  # a place's own look dominates its observation
WINDOW = 64  # recent-context length for the streaming-novelty baseline
RIDGE = 2.0  # shrinkage of each place's light model toward the shared one
FIRST = 1e9  # write score of a place's first observation in the place-keyed policies
QUERIES = 4000


@dataclass(frozen=True)
class Life:
    name: str
    days: int = 730
    rho: float = 0.6  # Song et al. fitted rho and gamma
    gamma: float = 0.21
    stops: float = 3.0  # mean daily stops besides home (and work, if any)
    work: bool = False  # weekday work 9:00-12:00 and 13:30-17:00, with a lunch stop between
    home_days: int = 0  # 0 = one home; n = a new home every n days (travel)
    changes_per_day: float = 0.03  # persistent changes per place per day
    lookalike: float = 0.2  # share of new places that look like an earlier one (chains, twin rooms)
    light: float = 2.0  # amplitude of the daily light cycle; one changed fact moves an observation 1.41
    passerby: float = 0.3  # chance a visit has someone in view
    passerby_size: float = 1.5  # a transient, not part of the state; one changed fact is 1.41
    noise: float = 0.03  # per-dimension sensor noise


LIVES = {
    "office": Life("office", rho=0.3, stops=1.5, work=True),
    "student": Life("student", rho=0.6, stops=3.0, work=True),
    "average": Life("average"),
    "nomad": Life("nomad", rho=0.95, gamma=0.0, home_days=3),
}


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.linalg.norm(v, axis=-1, keepdims=True)


def _phase(hours: np.ndarray) -> np.ndarray:
    """Daily-cycle features; the light at a place is linear in these."""
    a = 2 * np.pi * np.asarray(hours) / 24
    return np.stack([np.cos(a), np.sin(a)], axis=-1)


@dataclass(frozen=True)
class Day:
    """One simulated life, visits in time order."""

    t: np.ndarray  # (n,) days since start
    hour: np.ndarray  # (n,)
    place: np.ndarray  # (n,)
    state: np.ndarray  # (n, SLOTS) true persistent state at the visit
    x: np.ndarray  # (n, DIM) float32 observation
    decoded: np.ndarray  # (n, SLOTS) what a fixed decoder reads from x
    changed: np.ndarray  # (n,) state differs from this place's previous visit
    first: np.ndarray  # (n,) first visit to the place


def live(life: Life, seed: int) -> Day:
    rng = np.random.default_rng([seed, 0])
    facts = _unit(rng.standard_normal((SLOTS, VALUES, DIM)))
    shared_light = _unit(rng.standard_normal(DIM))
    looks: list[np.ndarray] = []
    lights: list[np.ndarray] = []
    states: list[np.ndarray] = []
    twin_of: list[int] = []  # a lookalike copies its twin's state as of its own first visit
    next_change: list[float] = []

    def new_place(t: float) -> int:
        k = len(looks)
        twin = int(rng.integers(1, k)) if k > 2 and rng.random() < life.lookalike else -1
        if twin >= 0:
            looks.append(PLACE_NORM * _unit(looks[twin] + 0.17 * PLACE_NORM * _unit(rng.standard_normal(DIM))))
        else:
            looks.append(PLACE_NORM * _unit(rng.standard_normal(DIM)))
        states.append(rng.integers(0, VALUES, SLOTS))
        twin_of.append(twin)
        lights.append(_unit(0.6 * shared_light + 0.8 * _unit(rng.standard_normal(DIM))))
        next_change.append(t + rng.exponential(1 / life.changes_per_day))
        return k

    def advance(k: int, t: float) -> None:
        while next_change[k] < t:
            f = rng.integers(SLOTS)
            states[k][f] = (states[k][f] + rng.integers(1, VALUES)) % VALUES  # a change changes the fact
            next_change[k] += rng.exponential(1 / life.changes_per_day)

    returns: dict[int, int] = {}

    def epr(t: float) -> int:
        n = len(returns)
        if n == 0 or rng.random() < life.rho * n**-life.gamma:
            k = new_place(t)
            returns[k] = 1
            return k
        keys = np.fromiter(returns, int, n)
        weights = np.fromiter(returns.values(), float, n)
        k = int(keys[rng.choice(n, p=weights / weights.sum())])
        returns[k] += 1
        return k

    home = new_place(0.0)
    work = new_place(0.0) if life.work else -1
    plan: list[tuple[float, int]] = []
    for day in range(life.days):
        if life.home_days and day and day % life.home_days == 0:
            home = new_place(float(day))
        stops: list[tuple[float, int]] = [(7.5, home)]
        if life.work and day % 7 < 5:
            stops += [(9.0, work), (12.2, epr(day + 0.5)), (13.5, work)]
            evening = rng.poisson(max(life.stops - 1, 0))
            stops += [(h, epr(day + h / 24)) for h in np.sort(rng.uniform(18, 21, evening))]
        else:
            stops += [(h, epr(day + h / 24)) for h in np.sort(rng.uniform(9, 21, rng.poisson(life.stops)))]
        stops.append((22.0, home))
        # Jitter, then order: visit index is time everywhere. Waking up starts a new visit at home.
        today: list[tuple[float, int]] = []
        for h, k in sorted((float(np.clip(h + rng.normal(0, 0.3), 0.5, 23.5)), k) for h, k in stops):
            if not today or today[-1][1] != k:  # staying put is one visit
                today.append((h, k))
        plan += [(day + h / 24, k) for h, k in today]

    n = len(plan)
    t = np.array([p[0] for p in plan])
    place = np.array([p[1] for p in plan])
    hour = (t % 1) * 24
    state = np.empty((n, SLOTS), int)
    changed = np.zeros(n, bool)
    first = np.zeros(n, bool)
    seen: dict[int, np.ndarray] = {}
    for i, (ti, k) in enumerate(plan):
        if k not in seen and twin_of[k] >= 0:
            advance(twin_of[k], ti)
            states[k] = states[twin_of[k]].copy()
        advance(k, ti)
        state[i] = states[k]
        first[i] = k not in seen
        changed[i] = not first[i] and bool((seen[k] != state[i]).any())
        seen[k] = state[i].copy()

    look = np.asarray(looks)[place]
    light = np.asarray(lights)[place] * (life.light * np.cos(2 * np.pi * (hour - 14) / 24))[:, None]
    content = facts[np.arange(SLOTS), state].sum(axis=1)
    passer = (rng.random(n) < life.passerby)[:, None] * life.passerby_size * _unit(rng.standard_normal((n, DIM)))
    x = (look + content + light + passer + life.noise * rng.standard_normal((n, DIM))).astype(np.float32)
    decoded = np.einsum("nd,fvd->nfv", x, facts.astype(np.float32)).argmax(-1)
    return Day(t, hour, place, state, x, decoded, changed, first)


# Memory ----------------------------------------------------------------------------------------


class Memory:
    """At most ``capacity`` observations; the lowest write score is evicted first."""

    def __init__(self, capacity: int, x: np.ndarray, place: np.ndarray) -> None:
        self.capacity, self.x, self.place = max(capacity, 1), x, place
        self.heap: list[tuple[float, int]] = []
        self.by_place: dict[int, list[int]] = defaultdict(list)  # stored indices, time order
        self.rows = np.zeros((self.capacity + 1, x.shape[1]), np.float32)
        self.row_of: dict[int, int] = {}
        self.free = list(range(self.capacity, -1, -1))
        self.live = np.zeros(self.capacity + 1, bool)

    def last(self, k: int) -> int | None:
        stored = self.by_place.get(k)
        return stored[-1] if stored else None

    def write(self, i: int, score: float) -> None:
        heapq.heappush(self.heap, (score, i))
        self.by_place[int(self.place[i])].append(i)
        row = self.free.pop()
        self.rows[row], self.row_of[i], self.live[row] = self.x[i], row, True
        if len(self.heap) > self.capacity:
            _, j = heapq.heappop(self.heap)
            self.by_place[int(self.place[j])].remove(j)
            row = self.row_of.pop(j)
            self.live[row] = False
            self.free.append(row)

    def nearest(self, v: np.ndarray) -> float:
        if not self.row_of:
            return FIRST
        d2 = ((self.rows - v) ** 2).sum(axis=1)
        return float(np.sqrt(d2[self.live].min()))

    def kept(self) -> list[int]:
        return sorted(i for _, i in self.heap)


def _scored(day: Day, capacity: int, score: Callable[[Memory, int], float]) -> list[int]:
    memory = Memory(capacity, day.x, day.place)
    for i in range(len(day.t)):
        memory.write(i, score(memory, i))  # heap entries are (score, index): ties evict the older
    return memory.kept()


def fifo(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    return list(range(max(len(day.t) - capacity, 0), len(day.t)))


def reservoir(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    kept: list[int] = []
    for i in range(len(day.t)):
        if len(kept) < capacity:
            kept.append(i)
        elif (j := int(rng.integers(i + 1))) < capacity:
            kept[j] = i
    return sorted(kept)


def temporal_surprise(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Distance from the previous observation: streaming-video surprise."""
    x = day.x
    return _scored(day, capacity, lambda m, i: float(np.linalg.norm(x[i] - x[i - 1])) if i else FIRST)


def window_novelty(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Distance to the nearest of the last WINDOW observations: novelty against recent context.
    The window buffer is not charged to the budget, which favours this policy at small budgets."""
    x = day.x

    def score(m: Memory, i: int) -> float:
        if i == 0:
            return FIRST
        return float(np.sqrt(((x[max(i - WINDOW, 0) : i] - x[i]) ** 2).sum(axis=1).min()))

    return _scored(day, capacity, score)


def memory_novelty(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Distance to the nearest stored observation: content dedupe with no place key."""
    return _scored(day, capacity, lambda m, i: m.nearest(day.x[i]))


def place_diff(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Distance from the place's last stored observation."""

    def score(m: Memory, i: int) -> float:
        ref = m.last(int(day.place[i]))
        return FIRST if ref is None else float(np.linalg.norm(day.x[i] - day.x[ref]))

    return _scored(day, capacity, score)


def place_nearest(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Distance to the nearest stored observation of the same place: within-place dedupe, no
    model. A stored observation at a similar hour cancels the light without predicting it."""

    def score(m: Memory, i: int) -> float:
        stored = m.by_place.get(int(day.place[i]))
        if not stored:
            return FIRST
        return float(np.sqrt(((day.x[stored] - day.x[i]) ** 2).sum(axis=1).min()))

    return _scored(day, capacity, score)


def place_cover_random(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """One observation per place is pinned; everything else is kept at random."""
    draw = rng.random(len(day.t))
    return _scored(day, capacity, lambda m, i: FIRST if m.last(int(day.place[i])) is None else float(draw[i]))


def place_predict(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Error of a prediction from the place's own stored history.

    The prediction is the last stored observation, relit to the current hour. The relighting is
    linear in the daily-cycle features: a shared model, fit online on every visit against the
    place's last stored observation, is the prior; each place refines it in-context from pairs of
    its own stored observations (ridge toward the shared model). The shared model is 2 x DIM
    floats, so this policy gets 2 fewer items of budget.
    """
    phase = _phase(day.hour)
    gram, cross = 1e-3 * np.eye(2), np.zeros((2, DIM))

    def score(m: Memory, i: int) -> float:
        nonlocal gram, cross
        stored = m.by_place.get(int(day.place[i]))
        if not stored:
            return FIRST
        ref = stored[-1]
        shared = np.linalg.solve(gram, cross)
        dp = phase[stored[1:]] - phase[stored[:-1]]
        dx = day.x[stored[1:]] - day.x[stored[:-1]]
        relight = np.linalg.solve(dp.T @ dp + RIDGE * np.eye(2), dp.T @ dx + RIDGE * shared)
        step = phase[i] - phase[ref]
        residual = day.x[i] - day.x[ref]
        gram += np.outer(step, step)
        cross += np.outer(step, residual)
        return float(np.linalg.norm(residual - step @ relight))

    return _scored(day, capacity - 2, score)


def place_semantic(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Facts that differ between the decoded observation and the place's last stored one. Needs a
    decoder that names the facts; in this world it is near perfect, in the real one it is a
    captioner."""

    def score(m: Memory, i: int) -> float:
        ref = m.last(int(day.place[i]))
        return FIRST if ref is None else float((day.decoded[i] != day.decoded[ref]).sum())

    return _scored(day, capacity, score)


def oracle(day: Day, capacity: int, rng: np.random.Generator) -> list[int]:
    """Knows the true state: keeps first visits and visits whose state changed since the place's
    last stored observation. Not an upper bound: below one item per place it degenerates to a
    FIFO of first visits, where reservoir sampling beats it."""

    def score(m: Memory, i: int) -> float:
        ref = m.last(int(day.place[i]))
        return FIRST if ref is None else float((day.state[i] != day.state[ref]).any())

    return _scored(day, capacity, score)


POLICIES: dict[str, Callable[[Day, int, np.random.Generator], list[int]]] = {
    "fifo": fifo,
    "reservoir": reservoir,
    "temporal_surprise": temporal_surprise,
    "window_novelty": window_novelty,
    "memory_novelty": memory_novelty,
    "place_cover_random": place_cover_random,
    "place_diff": place_diff,
    "place_nearest": place_nearest,
    "place_predict": place_predict,
    "place_semantic": place_semantic,
    "oracle": oracle,
}


# Queries ---------------------------------------------------------------------------------------


def queries(day: Day, rng: np.random.Generator) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """(visit, fact) pairs. ``changed`` asks about a fact that just changed."""
    n = len(day.t)
    visits = np.bincount(day.place)
    pools = {
        "all": np.arange(n),
        "rare": np.flatnonzero(visits[day.place] <= 2),
        "changed": np.flatnonzero(day.changed),
        "recent": np.flatnonzero(day.t >= day.t[-1] - 14),
    }
    out = {}
    for name, pool in pools.items():
        if len(pool) == 0:
            continue
        i = rng.choice(pool, QUERIES)
        if name == "changed":
            prev = {}
            last_state = np.empty_like(day.state)
            for j in range(n):
                k = int(day.place[j])
                last_state[j] = prev.get(k, day.state[j])
                prev[k] = day.state[j]
            f = np.array([rng.choice(np.flatnonzero(day.state[j] != last_state[j])) for j in i])
        else:
            f = rng.integers(0, SLOTS, QUERIES)
        out[name] = (i, f)
    return out


def accuracy(day: Day, kept: list[int], qs: dict[str, tuple[np.ndarray, np.ndarray]]) -> dict[str, float]:
    """Answer from the queried place's latest stored observation at or before the visit, else its
    earliest after; a place with nothing stored is a guess."""
    by_place: dict[int, list[int]] = defaultdict(list)
    for i in kept:
        by_place[int(day.place[i])].append(i)
    result = {}
    for name, (visit, fact) in qs.items():
        score = 0.0
        for i, f in zip(visit.tolist(), fact.tolist(), strict=True):
            stored = by_place.get(int(day.place[i]))
            if not stored:
                score += 1 / VALUES
                continue
            j = bisect_right(stored, i)
            ref = stored[j - 1] if j else stored[0]
            score += float(day.decoded[ref, f] == day.state[i, f])
        result[name] = score / len(visit)
    return result


def describe(day: Day) -> dict[str, float]:
    n, places = len(day.t), int(day.first.sum())
    novelty = places + int(day.changed.sum())
    return {
        "visits": n,
        "places": places,
        "changes_seen": int(day.changed.sum()),
        "novelty": novelty,
        "novelty_share": novelty / n,  # where the oracle reaches full recall, as a share of visits
        "decoder_ceiling": float((day.decoded == day.state).mean()),
    }


# Experiments ---------------------------------------------------------------------------------


def _job(args: tuple[Life, int, list[str], list[float], list[int]]) -> list[dict]:
    life, seed, policies, shares, absolute = args
    day = live(life, seed)
    facts = describe(day)
    if not policies:
        return [{"life": asdict(life), "seed": seed, **facts}]
    qs = queries(day, np.random.default_rng([seed, 1]))
    budgets = sorted({max(int(round(s * len(day.t))), 3) for s in shares} | set(absolute))
    rows = []
    for name in policies:
        for k in budgets:
            started = time.perf_counter()
            kept = POLICIES[name](day, min(k, len(day.t)), np.random.default_rng([seed, 2]))
            rows.append(
                {
                    "life": asdict(life),
                    "seed": seed,
                    "policy": name,
                    "budget": k,
                    "share": k / len(day.t),
                    "seconds": time.perf_counter() - started,
                    **facts,
                    **{f"acc_{q}": v for q, v in accuracy(day, kept, qs).items()},
                }
            )
    return rows


def run(jobs: list[tuple], workers: int) -> list[dict]:
    with ProcessPoolExecutor(workers) as pool:
        return [row for rows in pool.map(_job, jobs) for row in rows]


SHARES = [0.01, 0.02, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0]


def experiments(seeds: int) -> dict[str, list[tuple]]:
    every = list(POLICIES)
    core = ["reservoir", "memory_novelty", "place_cover_random", "place_nearest", "place_predict", "oracle"]
    change = ["memory_novelty", "place_diff", "place_nearest", "place_predict", "place_semantic", "oracle"]
    seeds_ = range(seeds)
    office, average = LIVES["office"], LIVES["average"]
    return {
        "main": [(life, s, every, SHARES, []) for life in LIVES.values() for s in seeds_],
        "change_rate": [
            (replace(average, changes_per_day=r), s, core, [0.1, 0.2, 0.5], [])
            for r in (0.003, 0.01, 0.03, 0.1, 0.3, 1.0)
            for s in seeds_
        ],
        "nuisance": [
            (replace(office, light=a, passerby=p), s, change, [0.2], [])
            for a in (0.0, 1.0, 2.0, 3.0)
            for p in (0.0, 0.3, 0.6)
            for s in seeds_
        ],
        "transient_size": [
            (replace(office, passerby_size=z), s, change, [0.2, 0.35], []) for z in (0.75, 1.5, 3.0) for s in seeds_
        ],
        "lookalike": [
            (replace(life, lookalike=q), s, core, [0.1, 0.2, 0.35, 0.5], [])
            for life in (office, average)
            for q in (0.0, 0.2, 0.4)
            for s in seeds_
        ],
        "years": [
            (replace(LIVES[name], days=d), s, core, [], [100, 200, 400, 800, 1600])
            for name in ("office", "average")
            for d in (182, 365, 730, 1460)
            for s in seeds_
        ],
        "growth": [
            (replace(LIVES[name], days=d), s, [], [], [])
            for name in LIVES
            for d in (91, 182, 365, 730, 1460, 2920, 5840)
            for s in seeds_
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=6)
    parser.add_argument("--only", nargs="*", help="experiments to run (default: all)")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--output", default="eval/results/memory-sim.json")
    args = parser.parse_args()
    results = {}
    for name, jobs in experiments(args.seeds).items():
        if args.only and name not in args.only:
            continue
        started = time.perf_counter()
        results[name] = run(jobs, args.workers)
        print(f"{name}: {len(results[name])} runs in {time.perf_counter() - started:.0f} s", flush=True)
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(results, f)


if __name__ == "__main__":
    main()
