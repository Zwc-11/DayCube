import hashlib
import importlib.util
import json
import os
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "capture-win"))
source = Path(
    os.environ.get(
        "DAYCUBE_PHONE_BENCH_SOURCE",
        Path(__file__).resolve().parents[1] / "services" / "capture-win" / "capture" / "phone_api.py",
    )
)
spec = importlib.util.spec_from_file_location("bench_phone_api", source)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NewUpload, Phones = module.NewUpload, module.Phones
result = {
    "python": platform.python_version(),
    "platform": platform.platform(),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "samples": {},
}
with tempfile.TemporaryDirectory(prefix="daycube-phone-synthetic-") as directory:
    phones = Phones(Path(directory))
    for size_mb in (1, 64):
        block = b"synthetic-phone-fixture\x00" * (1024 * 1024 // 24)
        data = block + b"x" * (1024 * 1024 - len(block))
        target = phones.staging / "ph-test" / "recording" / "audio.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with target.open("wb") as out:
            for _ in range(size_mb):
                out.write(data)
                digest.update(data)
        body = NewUpload(item="recording", name="audio.bin", bytes=size_mb * 1024 * 1024, sha256=digest.hexdigest())

        def sample(body: NewUpload = body) -> tuple[float, float]:
            start = time.perf_counter()
            cpu = time.process_time()
            answer = phones.start("ph-test", body)
            assert answer["done"] and answer["offset"] == body.bytes
            return (time.perf_counter() - start) * 1000, (time.process_time() - cpu) * 1000

        sample()
        sample()
        tracemalloc.start()
        times = [sample() for _ in range(7)]
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        result["samples"][f"{size_mb}MiB"] = {
            "wall_ms": [round(t[0], 3) for t in times],
            "cpu_ms": [round(t[1], 3) for t in times],
            "median_wall_ms": round(statistics.median(t[0] for t in times), 3),
            "peak_traced_bytes": peak,
        }
print(json.dumps(result, indent=2))
