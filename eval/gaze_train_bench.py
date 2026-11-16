"""Which way of training gaze from clicks works best?

Real webcam images (MPIIFaceGaze, 15 people on their own laptops over days), DayCube's geometry
(eval/gaze_train_prepare.py), DayCube's situation:

- a 20-image calibration on the first day (clean labels);
- then labels arriving in time order as clicks would: the first N images after the calibration, each
  label moved by click noise (a Gaussian with a 20 mm median, and 12% of labels replaced by a random
  point on the screen: clicks made without looking);
- the test is the person's last 40% of images, on later days, with their true targets.

Every method predicts each eye's direction in the head and the same geometry carries it to the
screen, so only what is learned differs. Errors are on-screen millimetres on the test images.

    python eval/gaze_train_bench.py /path/prepared --stage main --people p00 p01 --out results.jsonl
    python eval/gaze_train_bench.py --report results.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

CAL = 20
TEST_SHARE = 0.4
NOISE_SIGMA_MM = 17.0  # Rayleigh median 20 mm: published click labels after filtering (PACE: 74 px)
NOISE_OUTLIERS = 0.12  # clicks made without looking
ALPHAS = np.logspace(-4, 2, 13)


# ---------------------------------------------------------------- geometry (as gaze.head)
@dataclass
class Screen:
    origin: np.ndarray
    ex: np.ndarray
    ey: np.ndarray
    s: float

    def point(self, px: np.ndarray) -> np.ndarray:
        return self.origin + self.s * (px[..., :1] * self.ex + px[..., 1:2] * self.ey)


def directions(xy: np.ndarray) -> np.ndarray:
    xy = np.asarray(xy, dtype=float)
    square = np.einsum("...i,...i->...", xy, xy)
    scale = np.where(square > 0.95**2, 0.95 / np.sqrt(np.maximum(square, 1e-24)), 1.0)
    out = np.empty((*xy.shape[:-1], 3))
    out[..., :2] = xy * scale[..., None]
    out[..., 2] = -np.sqrt(1.0 - np.minimum(square, 0.95**2))
    return out


def required(S: Screen, R: np.ndarray, eyes: np.ndarray, target_px: np.ndarray) -> np.ndarray:
    aim = S.point(target_px)[:, None, :] - eyes
    aim /= np.linalg.norm(aim, axis=-1, keepdims=True)
    return np.einsum("nji,nej->nei", R, aim)[..., :2]


def to_px(S: Screen, R: np.ndarray, eyes: np.ndarray, xy: np.ndarray) -> np.ndarray:
    rays = np.matmul(directions(xy), R.transpose(0, 2, 1))
    n = np.cross(S.ex, S.ey)
    facing = rays @ n
    k = np.full(facing.shape, np.nan)
    np.divide((S.origin - eyes) @ n, facing, out=k, where=facing != 0)
    k[~(k > 0)] = np.nan
    hit = eyes + k[..., None] * rays - S.origin
    points = np.stack([hit @ S.ex, hit @ S.ey], -1) / S.s
    return np.nanmean(points, axis=1)


def pose_terms(R: np.ndarray, eyes: np.ndarray, ref: tuple[np.ndarray, np.ndarray]) -> np.ndarray:
    R0, p0 = ref
    moved = (eyes.mean(1) - p0) / 10.0
    rel = np.einsum("ji,njk->nik", R0, R)
    turned = 0.5 * np.stack([rel[:, 2, 1] - rel[:, 1, 2], rel[:, 0, 2] - rel[:, 2, 0], rel[:, 1, 0] - rel[:, 0, 1]], -1)
    return np.concatenate([moved, turned * 10.0], -1)


# ---------------------------------------------------------------- one person's data and split
class Person:
    def __init__(self, path: Path, low: bool = False) -> None:
        d = np.load(path)
        self.name = path.stem
        ok = d["found"] & d["found_low"]
        X = (d["X_low"] if low else d["X"])[ok]
        self.R = X[:, :9].reshape(-1, 3, 3)
        self.eyes = X[:, 12:18].reshape(-1, 2, 3)
        self.F = X[:, 18:].reshape(-1, 2, 4)
        self.target = d["target_px"][ok]
        self.days = d["days"][ok]
        self.distance = d["distance_mm"][ok]
        self.S = Screen(d["screen_origin"], d["screen_ex"], d["screen_ey"], float(d["mm_per_px"]))
        self.mm = float(d["true_mm_per_px"])
        self.screen_px = d["screen_px"].astype(float)
        self._crops = d["crops_low"][ok] if low else d["crops"][ok]
        n = len(X)
        self.cal = np.arange(CAL)
        self.test = np.arange(n - int(n * TEST_SHARE), n)
        self.pool = np.arange(CAL, self.test[0])
        Rm = self.R[self.cal].mean(0)
        u, _, vt = np.linalg.svd(Rm)
        self.P = pose_terms(self.R, self.eyes, (u @ vt, self.eyes[self.cal].mean((0, 1))))
        self.req = required(self.S, self.R, self.eyes, self.target)  # clean, for the test and the calibration

    def crops(self, size: tuple[int, int] = (60, 36)) -> np.ndarray:
        """(n, 2, H, W) float32, each crop with zero mean and unit spread; the left eye mirrored."""
        import cv2

        c = self._crops
        if (c.shape[3], c.shape[2]) != size:
            c = np.stack([[cv2.resize(e, size, interpolation=cv2.INTER_AREA) for e in pair] for pair in c])
        c = c.astype(np.float32)
        c = (c - c.mean((2, 3), keepdims=True)) / (c.std((2, 3), keepdims=True) + 1e-3)
        c[:, 1] = c[:, 1, :, ::-1]
        return c

    def labels(self, n: int, noise: bool, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
        """Indices of the calibration and the first n pool images, and their labels as required eye
        directions (the pool's with click noise when asked)."""
        idx = np.r_[self.cal, self.pool[:n]]
        target = self.target[idx].copy()
        if noise:
            rng = np.random.default_rng(seed + 1000 * int(self.name[1:]))
            m = len(target) - CAL
            target[CAL:] += rng.normal(0, NOISE_SIGMA_MM / self.mm, (m, 2))
            out = rng.random(m) < NOISE_OUTLIERS
            target[CAL:][out] = rng.random((int(out.sum()), 2)) * self.screen_px
        return idx, required(self.S, self.R[idx], self.eyes[idx], target)

    def score(self, xy: np.ndarray) -> dict:
        """Test error of per-eye directions (n_test, 2, 2)."""
        t = self.test
        px = to_px(self.S, self.R[t], self.eyes[t], xy)
        err = np.linalg.norm(px - self.target[t], axis=1) * self.mm
        err = np.where(np.isnan(err), 1e3, err)
        deg = np.degrees(np.arctan2(err, self.distance[t]))
        return {"mm": float(np.median(err)), "p90_mm": float(np.percentile(err, 90)), "deg": float(np.median(deg))}


# ---------------------------------------------------------------- landmark-number methods
def mirror(F10: np.ndarray) -> np.ndarray:
    """Features of both eyes with the left eye's mirrored, so one model serves both: gx, and the
    head's sideways move, yaw and roll change sign."""
    out = F10.copy()
    flip = {10: [0, 4, 8, 9], 6: [0, 4, 5], 4: [0]}[out.shape[2]]
    out[:, 1, flip] *= -1
    return out


def features(p: Person, idx: np.ndarray, pose: bool = True) -> np.ndarray:
    F = p.F[idx]
    if pose:
        F = np.concatenate([F, np.repeat(p.P[idx][:, None, :], 2, 1)], -1)
    return F


def ridge_fit(F: np.ndarray, Y: np.ndarray, weights: np.ndarray | None = None, rounds: int = 0) -> Any:
    """A ridge per eye from features (n, 2, k) to directions (n, 2, 2); ``rounds`` Huber reweightings."""
    from sklearn.linear_model import RidgeCV

    models = []
    for e in range(2):
        mean, sd = F[:, e].mean(0), F[:, e].std(0) + 1e-9
        Z = (F[:, e] - mean) / sd
        w = np.ones(len(Z)) if weights is None else weights.copy()
        for r in range(rounds + 1):
            model = RidgeCV(alphas=ALPHAS).fit(Z, Y[:, e], sample_weight=w)
            if r < rounds:
                res = np.linalg.norm(model.predict(Z) - Y[:, e], axis=1)
                scale = 1.4826 * np.median(res) + 1e-9
                w = (np.ones(len(Z)) if weights is None else weights) * np.minimum(
                    1.0, 1.345 * scale / np.maximum(res, 1e-12)
                )
        models.append((mean, sd, model))
    return models


def ridge_predict(models: Any, F: np.ndarray) -> np.ndarray:
    return np.stack([m.predict((F[:, e] - mean) / sd) for e, (mean, sd, m) in enumerate(models)], 1)


def gbm_fit_predict(F_train: np.ndarray, Y: np.ndarray, F_test: np.ndarray) -> np.ndarray:
    from sklearn.ensemble import HistGradientBoostingRegressor

    out = np.zeros((len(F_test), 2, 2))
    for e in range(2):
        for c in range(2):
            m = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.06, max_leaf_nodes=15, l2_regularization=1.0,
                                              loss="absolute_error", random_state=0)  # fmt: skip
            out[:, e, c] = m.fit(F_train[:, e], Y[:, e, c]).predict(F_test[:, e])
    return out


# ---------------------------------------------------------------- the eye network
def make_net(extra: int) -> Any:
    import torch.nn as nn

    def block(i: int, o: int):
        return [nn.Conv2d(i, o, 3, padding=1, bias=False), nn.BatchNorm2d(o), nn.ReLU(inplace=True)]

    class EyeNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.conv = nn.Sequential(*block(1, 16), nn.MaxPool2d(2), *block(16, 32), nn.MaxPool2d(2), *block(32, 64),
                                      nn.MaxPool2d(2), *block(64, 64), nn.AdaptiveAvgPool2d((2, 3)), nn.Flatten())  # fmt: skip
            self.body = nn.Sequential(nn.Linear(384 + extra, 96), nn.ReLU(inplace=True))
            self.out = nn.Sequential(nn.Dropout(0.1), nn.Linear(96, 2))

        def embed(self, img: Any, extra: Any):
            import torch

            return self.body(torch.cat([self.conv(img), extra], 1))

        def forward(self, img: Any, extra: Any):
            return self.out(self.embed(img, extra))

    return EyeNet()


def eye_rows(crops: np.ndarray, F: np.ndarray, Y: np.ndarray | None) -> tuple:
    """Both eyes of each image as separate rows: image (m, 1, H, W), extra (m, k + 1), target (m, 2);
    the left eye mirrored throughout."""
    n = len(crops)
    img = crops.reshape(n * 2, 1, *crops.shape[2:])
    Fm = mirror(F)
    extra = np.concatenate([Fm.reshape(n * 2, -1), np.tile([[0.0], [1.0]], (n, 1))], 1).astype(np.float32)
    target = None
    if Y is not None:
        target = Y.copy()
        target[:, 1, 0] *= -1
        target = target.reshape(n * 2, 2).astype(np.float32)
    return img, extra, target


def unmirror(pred: np.ndarray) -> np.ndarray:
    xy = pred.reshape(-1, 2, 2).astype(float).copy()
    xy[:, 1, 0] *= -1
    return xy


def train_net(net: Any, img: Any, extra: Any, target: Any, *, steps: int, lr: float = 2e-3, loss: str = "huber", batch: int = 128,
              seed: int = 0, weights: np.ndarray | None = None, wd: float = 1e-3) -> Any:  # fmt: skip
    import torch
    import torch.nn.functional as Fn

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    img_t, extra_t, target_t = torch.from_numpy(img), torch.from_numpy(extra), torch.from_numpy(target)
    w_t = None if weights is None else torch.from_numpy(weights.astype(np.float32))
    opt = torch.optim.AdamW(net.parameters(), lr=lr, weight_decay=wd)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=lr, total_steps=steps, pct_start=0.15)
    n = len(img_t)
    net.train()
    for step in range(steps):
        idx = torch.from_numpy(rng.integers(0, n, min(batch, n)))
        x = img_t[idx]
        # augmentation: a shift of up to 2 px, and a change of brightness and contrast
        dx, dy = rng.integers(-2, 3, 2)
        x = torch.roll(x, (int(dy), int(dx)), (2, 3))
        gain = torch.from_numpy(rng.uniform(0.8, 1.25, (len(idx), 1, 1, 1)).astype(np.float32))
        bias = torch.from_numpy(rng.normal(0, 0.15, (len(idx), 1, 1, 1)).astype(np.float32))
        pred = net(x * gain + bias, extra_t[idx])
        d = pred - target_t[idx]
        if loss == "mse":
            per = (d * d).sum(1)
        else:  # huber on the direction error, delta 0.06 (about 3.4 degrees)
            per = Fn.huber_loss(pred, target_t[idx], delta=0.06, reduction="none").sum(1)
        if loss == "trim" and step > 0.3 * steps:  # drop the 15% of the batch that fits worst
            keep = per.detach().argsort()[: int(0.85 * len(per))]
            per = per[keep]
            idx = idx[keep]
        if w_t is not None:
            per = per * w_t[idx]
        opt.zero_grad()
        per.mean().backward()
        opt.step()
        sched.step()
    return net


def predict_net(net: Any, img: Any, extra: Any, embed: bool = False) -> np.ndarray:
    import torch

    net.eval()
    out = []
    with torch.no_grad():
        for i in range(0, len(img), 512):
            a, b = torch.from_numpy(img[i : i + 512]), torch.from_numpy(extra[i : i + 512])
            out.append((net.embed(a, b) if embed else net(a, b)).numpy())
    return np.concatenate(out)


def standardise(train: np.ndarray, *others: np.ndarray) -> tuple:
    mean, sd = train[:, :-1].mean(0), train[:, :-1].std(0) + 1e-6
    out = []
    for a in (train, *others):
        b = a.copy()
        b[:, :-1] = (b[:, :-1] - mean) / sd
        out.append(b)
    return out


def steps_for(n_rows: int) -> int:
    return int(min(1000, max(300, 40 * n_rows / 128)))


# ---------------------------------------------------------------- experiments
def run_person(path: Path, args: argparse.Namespace) -> list[dict]:
    import torch

    torch.set_num_threads(1)
    rows: list[dict] = []
    stage = args.stage

    def add(p: Person, method: str, n: int, noise: bool, res: str, xy: np.ndarray, started: float, **more: Any) -> None:
        row = {"person": p.name, "method": method, "n": n, "noise": noise, "res": res, **p.score(xy),
               "seconds": round(time.time() - started, 1), **more}  # fmt: skip
        rows.append(row)
        with open(args.out, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{p.name} {method:<22} n={n:<5} noise={int(noise)} res={res:<4} {row['mm']:6.1f} mm  {row['deg']:.2f} deg  "
              f"({row['seconds']} s)", flush=True)  # fmt: skip

    native, low = Person(path), Person(path, low=True)
    sizes = {"mid": (60, 36), "high": (96, 60)}

    def numbers(p: Person, res: str, n: int, noise: bool) -> None:
        """Methods that use only the tracker's numbers."""
        t0 = time.time()
        idx, Y = p.labels(n, noise)
        Ft = features(p, p.test)
        cal_model = ridge_fit(p.F[p.cal], p.req[p.cal])
        add(p, "calibration only", 0, False, res, ridge_predict(cal_model, p.F[p.test]), t0)
        if n == 0:
            return
        t0 = time.time()
        add(p, "ridge on landmarks", n, noise, res, ridge_predict(ridge_fit(features(p, idx), Y), Ft), t0)
        t0 = time.time()
        add(p, "ridge, Huber weights", n, noise, res, ridge_predict(ridge_fit(features(p, idx), Y, rounds=2), Ft), t0)
        t0 = time.time()
        add(p, "boosting on landmarks", n, noise, res, gbm_fit_predict(features(p, idx), Y, Ft), t0)

    def network(p: Person, res: str, size: str, n: int, noise: bool, kind: str, loss: str = "huber") -> None:
        """kind: 'eye' (picture + head pose), 'eye+lm' (picture + landmark numbers + head pose),
        'residual' (the picture corrects the landmark ridge)."""
        t0 = time.time()
        idx, Y = p.labels(n, noise)
        crops = p.crops(sizes[size])
        Ftr, Fte = features(p, idx), features(p, p.test)
        if kind == "eye":
            Ftr, Fte = Ftr[:, :, 4:], Fte[:, :, 4:]
        base_tr = base_te = 0.0
        if kind == "residual":
            model = ridge_fit(features(p, idx), Y, rounds=2)
            base_tr, base_te = ridge_predict(model, features(p, idx)), ridge_predict(model, features(p, p.test))
        img, extra, target = eye_rows(crops[idx], Ftr, Y - base_tr)
        img_te, extra_te, _ = eye_rows(crops[p.test], Fte, None)
        extra, extra_te = standardise(extra, extra_te)
        first = "huber" if loss == "selfclean" else loss
        net = train_net(make_net(extra.shape[1]), img, extra, target, steps=steps_for(len(img)), loss=first)
        if loss == "selfclean":  # drop the fifth of the click labels the first model fits worst, train again
            res = np.linalg.norm(predict_net(net, img, extra) - target, axis=1).reshape(-1, 2).mean(1)
            keep = np.ones(len(res), bool)
            clicks = np.arange(CAL, len(res))
            keep[clicks[np.argsort(res[clicks])[int(0.8 * len(clicks)) :]]] = False
            rows_kept = np.repeat(keep, 2)
            net = train_net(
                make_net(extra.shape[1]),
                img[rows_kept],
                extra[rows_kept],
                target[rows_kept],
                steps=steps_for(int(rows_kept.sum())),
                loss="huber",
            )
        xy = unmirror(predict_net(net, img_te, extra_te)) + base_te
        name = {"eye": "eye network", "eye+lm": "eye network + landmarks", "residual": "eye network corrects ridge"}[
            kind
        ]
        if loss != "huber":
            name += f" ({loss})"
        add(p, name, n, noise, f"{res}/{size}" if res == "low" else size, xy, t0)

    def cross(p: Person) -> None:
        """A network trained on other people (their true targets), then fitted to this person in
        four ways of growing capacity: as it is; an offset per eye; a ridge on its last layer; all
        of its weights."""
        import torch

        fold = sorted(q.stem for q in args.prepared.glob("p*.npz")).index(p.name) % FOLDS
        while not (args.prepared / f"pretrain-{fold}.pt").exists():  # the other process is still training it
            time.sleep(20)
        time.sleep(2)
        state = torch.load(args.prepared / f"pretrain-{fold}.pt", weights_only=False)
        mean, sd = state["mean"], state["sd"]
        crops = p.crops(sizes["mid"])

        def rows_of(idx: np.ndarray, Y: np.ndarray):
            img, extra, target = eye_rows(crops[idx], features(p, idx), Y)
            extra[:, :-1] = (extra[:, :-1] - mean) / sd
            return img, extra, target

        img_te, extra_te, _ = rows_of(p.test, None)
        for n in args.curve:
            idx, Y = p.labels(n, True)
            img, extra, target = rows_of(idx, Y)
            net = make_net(extra.shape[1])
            net.load_state_dict(state["net"])
            t0 = time.time()
            zero = predict_net(net, img_te, extra_te)
            if n == args.curve[0]:
                add(p, "other people's network, as is", 0, False, "mid", unmirror(zero), t0)
            t0 = time.time()
            res = (target - predict_net(net, img, extra)).reshape(-1, 2, 2)
            add(
                p,
                "other people's network + offset",
                n,
                True,
                "mid",
                unmirror(zero + np.tile(np.median(res, 0), (len(zero) // 2, 1))),
                t0,
            )
            t0 = time.time()
            E, E_te = predict_net(net, img, extra, embed=True), predict_net(net, img_te, extra_te, embed=True)
            Z, Z_te = np.concatenate([E, extra], 1), np.concatenate([E_te, extra_te], 1)
            model = ridge_fit(Z.reshape(-1, 2, Z.shape[1]), target.reshape(-1, 2, 2), rounds=2)
            add(
                p,
                "other people's network + ridge",
                n,
                True,
                "mid",
                unmirror(ridge_predict(model, Z_te.reshape(-1, 2, Z.shape[1])).reshape(-1, 2)),
                t0,
            )
            t0 = time.time()
            tuned = train_net(net, img, extra, target, steps=max(150, steps_for(len(img)) // 2), lr=5e-4, loss="trim")
            add(p, "other people's network, tuned", n, True, "mid", unmirror(predict_net(tuned, img_te, extra_te)), t0)

    N = args.n
    if stage == "cross":
        cross(native)
    elif stage == "main":
        numbers(native, "native", N, True)
        numbers(low, "low", N, True)
        network(native, "native", "mid", N, True, "eye+lm")
        network(low, "low", "mid", N, True, "eye+lm")
    elif stage == "extra":
        network(native, "native", "mid", N, True, "eye")
        network(native, "native", "mid", N, True, "residual")
        network(native, "native", "high", N, True, "eye+lm")
    elif stage == "clean":
        numbers(native, "native", N, False)
        network(native, "native", "mid", N, False, "eye+lm")
    elif stage == "curve":
        for n in args.curve:
            numbers(native, "native", n, True)
            network(native, "native", "mid", n, True, "eye+lm")
    elif stage == "loss":
        for loss in ("mse", "trim", "selfclean"):
            network(native, "native", "mid", N, True, "eye+lm", loss)
    elif stage == "lowcurve":
        for n in args.curve:
            numbers(low, "low", n, True)
            network(low, "low", "mid", n, True, "eye+lm")
    return rows


FOLDS = 5


def pretrain(prepared: Path, fold: int, steps: int = 1500) -> None:
    """The network trained on every person outside ``fold`` (people are dealt into FOLDS by their
    order), with their true targets; saved with the scaling of its inputs."""
    import torch

    torch.set_num_threads(1)
    paths = sorted(prepared.glob("p*.npz"))
    imgs, extras, targets = [], [], []
    for k, path in enumerate(paths):
        if k % FOLDS == fold:
            continue
        p = Person(path)
        idx = np.arange(len(p.target))
        img, extra, target = eye_rows(p.crops((60, 36)), features(p, idx), p.req)
        imgs.append(img), extras.append(extra), targets.append(target)
    img, extra, target = np.concatenate(imgs), np.concatenate(extras), np.concatenate(targets)
    mean, sd = extra[:, :-1].mean(0), extra[:, :-1].std(0) + 1e-6
    extra[:, :-1] = (extra[:, :-1] - mean) / sd
    started = time.time()
    net = train_net(make_net(extra.shape[1]), img, extra, target, steps=steps, batch=256, lr=2e-3)
    torch.save({"net": net.state_dict(), "mean": mean, "sd": sd}, prepared / f"pretrain-{fold}.tmp")
    (prepared / f"pretrain-{fold}.tmp").rename(prepared / f"pretrain-{fold}.pt")
    print(f"fold {fold}: {len(img)} eye rows, {steps} steps, {time.time() - started:.0f} s", flush=True)


def report(results: Path, save: Path | None = None) -> list[dict]:
    """Per method, the mean over people of each person's median error; a person's last run wins."""
    groups: dict[tuple, dict[str, dict]] = {}
    for line in results.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            groups.setdefault((r["method"], r["n"], r["noise"], r["res"]), {})[r["person"]] = r
    rows = []
    for (method, n, noise, res), people in groups.items():
        mean = {k: float(np.mean([p[k] for p in people.values()])) for k in ("mm", "deg", "p90_mm", "seconds")}
        rows.append({"method": method, "n": n, "noise": noise, "res": res, "people": len(people),
                     "mm": round(mean["mm"], 1), "deg": round(mean["deg"], 2), "p90_mm": round(mean["p90_mm"], 1),
                     "seconds": round(mean["seconds"], 1)})  # fmt: skip
    rows.sort(key=lambda r: (r["res"], r["n"], r["noise"], r["mm"]))
    print(f"{'method':<40} {'res':<9} {'n':>5} noise people    mm   deg   p90   secs")
    for r in rows:
        print(f"{r['method']:<40} {r['res']:<9} {r['n']:>5} {int(r['noise']):>5} {r['people']:>6} {r['mm']:5.1f} "
              f"{r['deg']:5.2f} {r['p90_mm']:5.1f} {r['seconds']:6.1f}")  # fmt: skip
    if save:
        save.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prepared", type=Path, nargs="?")
    parser.add_argument("--stage", default="main")
    parser.add_argument("--people", nargs="*")
    parser.add_argument("--n", type=int, default=1000)
    parser.add_argument("--curve", type=int, nargs="*", default=[100, 300])
    parser.add_argument("--out", type=Path, default=Path("results.jsonl"))
    parser.add_argument(
        "--pretrain", type=int, nargs="*", help="Train the cross-person network of these folds, then stop"
    )
    parser.add_argument("--report", type=Path, help="Print the table of a results file, then stop")
    parser.add_argument("--save", type=Path, help="With --report: also write the table as JSON")
    args = parser.parse_args()
    if args.report:
        report(args.report, args.save)
        return
    if args.pretrain is not None:
        for fold in args.pretrain:
            pretrain(args.prepared, fold)
        return
    paths = sorted(args.prepared.glob("p*.npz"))
    if args.people:
        paths = [p for p in paths if p.stem in args.people]
    for path in paths:
        run_person(path, args)


if __name__ == "__main__":
    main()
