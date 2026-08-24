#!/usr/bin/env python3
"""Convert a samply profile.json.gz + .syms.json sidecar to Brendan Gregg folded stacks."""

from __future__ import annotations

import argparse
import gzip
import json
from bisect import bisect_right
from collections import Counter
from pathlib import Path

WAIT_LEAVES = (
    "__psynch_cvwait",
    "__workq_kernreturn",
    "kevent",
    "__ulock_wait",
    "poll",
    "mach_msg2_trap",
    "semaphore_wait_trap",
)


def load_profile(path: Path) -> dict:
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        return json.loads(gzip.open(path).read())
    return json.loads(path.read_text())


def load_syms(path: Path) -> dict[str, tuple[list[int], list[int], list[str], dict[int, str]]]:
    raw = json.loads(path.read_text())
    strings: list[str] = raw["string_table"]
    out: dict[str, tuple[list[int], list[int], list[str], dict[int, str]]] = {}
    for lib in raw["data"]:
        table = sorted(lib["symbol_table"], key=lambda e: e["rva"])
        rvas = [e["rva"] for e in table]
        sizes = [e["size"] for e in table]
        names = [strings[e["symbol"]] for e in table]
        exact: dict[int, str] = {}
        for rva, idx in lib.get("known_addresses", []):
            if 0 <= idx < len(table):
                exact[int(rva)] = strings[table[idx]["symbol"]]
        out[lib["debug_name"]] = (rvas, sizes, names, exact)
    return out


def resolve(lib: str, rva: int, lookups: dict) -> str:
    info = lookups.get(lib)
    if info is None:
        return f"{lib}`0x{rva:x}" if lib else f"0x{rva:x}"
    rvas, sizes, names, exact = info
    if rva in exact:
        name = exact[rva]
    else:
        i = bisect_right(rvas, rva) - 1
        if i >= 0 and rva < rvas[i] + max(sizes[i], 1):
            name = names[i]
        else:
            name = f"0x{rva:x}"
    name = name.replace(";", ":")
    return f"{lib}`{name}" if lib else name


def shorten(name: str, limit: int = 160) -> str:
    """Keep folded lines grep-able; drop binary prefix for spur frames."""
    if name.startswith("spur`"):
        name = name[len("spur`") :]
    if len(name) > limit:
        name = "..." + name[-(limit - 3) :]
    return name


def walk_stack(stack_idx, prefix, frame) -> list[int]:
    frames: list[int] = []
    seen: set[int] = set()
    while stack_idx is not None and stack_idx >= 0 and stack_idx not in seen:
        seen.add(stack_idx)
        frames.append(frame[stack_idx])
        stack_idx = prefix[stack_idx]
    frames.reverse()
    return frames


def convert(profile: dict, lookups: dict) -> Counter[str]:
    counts: Counter[str] = Counter()
    libs = profile.get("libs") or []
    for thread in profile.get("threads") or []:
        samples = thread.get("samples") or {}
        n = samples.get("length") or 0
        if n == 0:
            continue
        stack_ids = samples["stack"]
        st = thread["stackTable"]
        ft = thread["frameTable"]
        fnt = thread["funcTable"]
        rt = thread.get("resourceTable") or {}
        prefix = st["prefix"]
        stack_frame = st["frame"]
        addrs = ft["address"]
        funcs = ft["func"]
        resources = fnt.get("resource") or [0] * fnt["length"]
        res_lib = rt.get("lib") or []
        thread_name = (thread.get("name") or "thread").replace(";", ":").replace(" ", "_")
        for i in range(n):
            sid = stack_ids[i]
            if sid is None or sid < 0:
                continue
            names: list[str] = [thread_name]
            for frame_idx in walk_stack(sid, prefix, stack_frame):
                func_idx = funcs[frame_idx]
                res_idx = resources[func_idx]
                lib_idx = res_lib[res_idx] if 0 <= res_idx < len(res_lib) else None
                lib_name = ""
                if lib_idx is not None and 0 <= lib_idx < len(libs):
                    lib_name = libs[lib_idx].get("debugName") or libs[lib_idx].get("name") or ""
                rva = int(addrs[frame_idx])
                names.append(shorten(resolve(lib_name, rva, lookups)))
            leaf = names[-1] if names else ""
            if any(w in leaf for w in WAIT_LEAVES):
                continue
            counts[";".join(names)] += 1
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--syms", type=Path, default=None)
    args = ap.parse_args()
    profile = load_profile(args.profile)
    syms_path = args.syms
    if syms_path is None:
        stem = args.profile.name.removesuffix(".gz").removesuffix(".json")
        cand = args.profile.with_name(args.profile.name.replace(".json.gz", ".json.syms.json"))
        if not cand.exists():
            cand = args.profile.parent / f"{stem}.json.syms.json"
        if not cand.exists():
            cand = args.profile.parent / f"{args.profile.name}.syms.json"
        syms_path = cand
    lookups = load_syms(syms_path)
    counts = convert(profile, lookups)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{stack} {n}\n" for stack, n in counts.most_common()]
    args.output.write_text("".join(lines))
    print("stacks", len(counts), "samples", sum(counts.values()), "wrote", args.output)


if __name__ == "__main__":
    main()
