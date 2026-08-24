#!/usr/bin/env python3
"""Render a Brendan Gregg folded file as a static SVG flamegraph."""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
from pathlib import Path


class Node:
    __slots__ = ("name", "self_samples", "children")

    def __init__(self, name: str) -> None:
        self.name = name
        self.self_samples = 0
        self.children: dict[str, Node] = {}


def parse_folded(path: Path) -> tuple[Node, int, int]:
    root = Node("")
    total = 0
    n_stacks = 0
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        stack, _, count_s = line.rpartition(" ")
        try:
            count = int(count_s)
        except ValueError:
            continue
        if count <= 0:
            continue
        frames = [f for f in stack.split(";") if f and not f.startswith("Thread_")]
        if not frames:
            continue
        node = root
        for frame in frames:
            node = node.children.setdefault(frame, Node(frame))
        node.self_samples += count
        total += count
        n_stacks += 1
    return root, total, n_stacks


def inclusive(node: Node) -> int:
    return node.self_samples + sum(inclusive(c) for c in node.children.values())


def max_depth(node: Node) -> int:
    if not node.children:
        return 0
    return 1 + max(max_depth(c) for c in node.children.values())


def short_name(name: str) -> str:
    if "`" in name:
        name = name.rsplit("`", 1)[-1]
    if "::" in name:
        name = name.rsplit("::", 1)[-1]
    return name


def fill_for(name: str) -> str:
    h = hashlib.md5(name.encode()).digest()
    r = 205 + h[0] % 50
    g = 80 + h[1] % 110
    b = 12 + h[2] % 36
    return f"rgb({r},{g},{b})"


def flame_svg(root: Node, total: int, n_stacks: int, title: str) -> str:
    width, row_h, pad_top, pad_x, pad_bot = 1400, 16, 28, 8, 10
    depth = max_depth(root)
    height = pad_top + depth * row_h + pad_bot
    inner_w = width - 2 * pad_x
    rects: list[str] = []

    def walk(node: Node, x: float, y: float, w: float) -> None:
        incl = inclusive(node)
        if incl <= 0 or w < 0.5:
            return
        name = node.name or "(all)"
        pct = 100.0 * incl / total
        title_t = html_lib.escape(f"{name}  {incl} samples ({pct:.1f}%)")
        label = html_lib.escape(short_name(name))
        fill = fill_for(name)
        rects.append(
            f'<g><title>{title_t}</title>'
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{row_h - 1}" '
            f'rx="1" fill="{fill}" stroke="#3a1d0a" stroke-width="0.3"/>'
        )
        if w >= 48:
            rects.append(
                f'<text x="{x + 3:.2f}" y="{y + 12:.2f}" font-size="10" '
                f'font-family="ui-monospace,Menlo,monospace" fill="#1a0a00">{label}</text>'
            )
        rects.append("</g>")
        cx = x
        kids = sorted(node.children.values(), key=lambda c: (-inclusive(c), c.name))
        for child in kids:
            cw = w * (inclusive(child) / incl)
            walk(child, cx, y - row_h, cw)
            cx += cw

    y0 = pad_top + (depth - 1) * row_h
    x0 = pad_x
    kids = sorted(root.children.values(), key=lambda c: (-inclusive(c), c.name))
    for child in kids:
        cw = inner_w * (inclusive(child) / total)
        walk(child, x0, y0, cw)
        x0 += cw

    caption = html_lib.escape(
        f"{title} · {total:,} samples · {n_stacks} unique stacks · width = inclusive · hover a frame"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" style="background:#1b140e">'
        f'<text x="{pad_x}" y="18" fill="#f3e6d4" font-size="12" '
        f'font-family="ui-sans-serif,system-ui,sans-serif">{caption}</text>'
        + "".join(rects)
        + "</svg>"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folded", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--title", default="CPU flamegraph")
    args = ap.parse_args()
    tree, total, n_stacks = parse_folded(args.folded)
    svg = flame_svg(tree, total, n_stacks, args.title)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(svg)
    print("samples", total, "stacks", n_stacks, "wrote", args.output)


if __name__ == "__main__":
    main()
