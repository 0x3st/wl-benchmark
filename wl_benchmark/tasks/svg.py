"""SVG drawing task (human-reviewed) — README three-stage design.

Stage 1  riding:       pick one word from A (person/animal) and one from B
                       (non-animal object); draw "A riding B"
Stage 2  relation:     pick two persons from A; draw a COMPLETE illustration
                       of how they are indirectly related
Stage 3  architecture: pick one item from B; draw its architecture diagram

Each stage runs once per model; random picks are recorded in detail
(reproducible via spec.seed). Output: SVG + PNG — a human grades the PNG,
no model-based judging.

Scenario JSON schema:
{
  "id": "svg-stage1",
  "stage": "riding" | "relation" | "architecture",
  "set_a": ["Xu Yangsheng (President)", "..."],     # riding/relation
  "set_b": ["iPhone", "..."],                        # riding/architecture
  "seed": 20260910                                   # optional, reproducible picks
}
"""
from __future__ import annotations

import glob
import json
import os
import random
import re
import xml.etree.ElementTree as ET
import shutil
import subprocess
import tempfile
from typing import Any, Dict, List, Optional

from .base import BaseTask, TaskResult

QUALITY_NOTE = (
    "Requirements: output one complete, valid, independently renderable SVG "
    "document (starting with <svg> and ending with </svg>), with no extra "
    "explanation text. The picture should have a clear layout, fully labeled "
    "elements, and a coherent color scheme. Hard format constraints (all "
    "checked automatically): the root <svg> must carry BOTH a viewBox and "
    "explicit width/height; every <text> element must have font-size >= 14 "
    "and a non-empty label; arrowheads must be drawn explicitly (marker or "
    "triangle path, not bare lines); include a title and a short legend "
    "box explaining the symbols used; keep all content INSIDE the canvas "
    "(no cropped labels at the edges)."
)


def discover(root: str) -> List[Dict[str, Any]]:
    out = []
    for f in sorted(glob.glob(os.path.join(root, "*.json"))):
        with open(f, encoding="utf-8") as fh:
            out.append(json.load(fh))
    return out


def build_instruction(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Pick words per stage and compose the instruction. Returns
    {"instruction": ..., "picks": {...}}."""
    rng = random.Random(spec.get("seed"))
    stage = spec["stage"]
    picks: Dict[str, str] = {}

    if stage == "riding":
        a = rng.choice(spec["set_a"])
        b = rng.choice(spec["set_b"])
        picks = {"a": a, "b": b}
        instr = (f"Draw a single SVG illustration of {a} riding {b}. "
                 f"(e.g. 'President Xu riding GPA' means the person is "
                 f"sitting on a giant GPA symbol as if it were a mount). "
                 f"The scene must contain at least 5 labeled elements "
                 f"(the rider, the mount, and 3 more environmental props, "
                 f"each with a short text label), a caption line naming "
                 f"the scene, and a background of sky AND ground.")
    elif stage == "relation":
        a1, a2 = rng.sample(spec["set_a"], 2)
        picks = {"a1": a1, "a2": a2}
        instr = (f"Draw a COMPLETE SVG illustration showing how {a1} and "
                 f"{a2} are indirectly related. The relation chain must "
                 f"contain at least 3 intermediate labeled nodes (persons/"
                 f"events/objects), every hop connected by a DIRECTIONAL "
                 f"arrow, each hop carrying a short dated caption (a year "
                 f"or era), plus a summary caption stating the full chain "
                 f"in one sentence.")
    elif stage == "architecture":
        b = rng.choice(spec["set_b"])
        picks = {"b": b}
        instr = (f"Draw an architecture diagram (SVG) of {b}: its main "
                 f"components, how they connect, and the flow of data/"
                 f"signals between them. The diagram must contain at least "
                 f"8 labeled component boxes, at least 6 directional arrows "
                 f"showing data/signal flow, a legend distinguishing "
                 f"component types (e.g. solid vs dashed borders), and "
                 f"group-related components with an enclosing boundary.")
    else:
        raise ValueError(f"unknown svg stage: {stage}")

    return {"instruction": instr, "picks": picks}


def extract_svg(text: str) -> str:
    """Pull the first <svg>...</svg> out of a (possibly fenced) reply.

    Truncation rescue: reasoning models may burn the token budget and
    leave the SVG unclosed; take everything from <svg to the end and
    append the closing tag, preserving as much of the drawing as possible.
    """
    m = re.search(r"<svg[\s\S]*?</svg>", text, re.IGNORECASE)
    if m:
        return m.group(0)
    m = re.search(r"<svg[\s\S]*", text, re.IGNORECASE)
    if m:
        return m.group(0).rstrip() + "\n</svg>"
    return ""


def validate_svg(svg: str) -> tuple[bool, str, str]:
    """Parse the SVG as XML; on failure attempt cheap repairs.

    Returns (ok, repaired_svg, note). Common model breakages: bare '&'
    in text, missing xmlns, stray control characters.
    """
    def parse(s):
        try:
            ET.fromstring(s)
            return True, ""
        except ET.ParseError as e:
            return False, str(e)

    ok, err = parse(svg)
    if ok:
        return True, svg, ""
    repaired = svg
    if 'xmlns=' not in repaired.split(">", 1)[0]:
        repaired = repaired.replace(
            "<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    repaired = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", repaired)
    repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
    ok2, err2 = parse(repaired)
    if ok2:
        return True, repaired, f"auto-repaired: {err}"
    return False, svg, f"invalid XML: {err}"


def sanitize_svg(svg: str) -> tuple[str, int]:
    """Strip executable content from an SVG before it is saved/rastered.

    The model's SVG is loaded into headless Chrome (and later downloaded
    from the platform and opened in browsers), so anything scriptable is
    removed: <script>/<foreignObject>/<iframe>/<embed>/<object>
    elements, on* event-handler attributes, and javascript:/external
    href references. Returns (svg, n_removed).
    """
    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return svg, 0
    drop = {"script", "foreignobject", "iframe", "embed", "object"}
    parent = {c: p for p in root.iter() for c in p}
    removed = 0
    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        tag = el.tag.split("}")[-1].lower()
        if tag in drop and el in parent and el in parent[el]:
            parent[el].remove(el)
            removed += 1
            continue
        for attr in list(el.attrib):
            val = el.attrib[attr] or ""
            if attr.lower().startswith("on") \
                    or val.strip().lower().startswith("javascript:") \
                    or (attr.lower().endswith("href")
                        and val.strip().lower().startswith(("http:", "https:", "file:"))):
                del el.attrib[attr]
                removed += 1
    if removed:
        svg = ET.tostring(root, encoding="unicode")
    return svg, removed


def svg_checks(svg: str, stage: str, picks: Dict[str, str]) -> List[dict]:
    """Machine-checkable structural constraints on the SVG reply.

    These do NOT replace human review of the picture; they flag format
    violations the reviewer can confirm at a glance.
    """
    out: List[dict] = []

    def add(name, ok, note):
        out.append({"constraint": name, "ok": bool(ok), "detail": note})

    if not svg:
        add("svg present", False, "no <svg> block extracted")
        return out
    add("svg present", True, f"{len(svg)} bytes")

    m = re.search(r"<svg[^>]*>", svg)
    root = m.group(0) if m else ""
    add("root has viewBox", 'viewBox' in root,
        "viewBox attr " + ("found" if "viewBox" in root else "MISSING"))
    add("root has width/height", 'width' in root and 'height' in root,
        "explicit canvas size " +
        ("found" if 'width' in root and 'height' in root else "MISSING"))

    texts = re.findall(r"<text[^>]*>([\s\S]*?)</text>", svg)
    labels = [re.sub(r"<[^>]+>", "", t).strip() for t in texts]
    labels = [l for l in labels if l]
    need = {"riding": 5, "relation": 6, "architecture": 8}[stage]
    add(f">= {need} labeled texts", len(labels) >= need,
        f"{len(labels)} non-empty <text> labels")

    small = re.findall(r'font-size\s*=\s*"(\d+(?:\.\d+)?)"', svg)
    bad_fs = [s for s in small if float(s) < 14]
    add("font-size >= 14", not bad_fs,
        "all readable" if not bad_fs else f"{len(bad_fs)} too small: "
        f"{sorted(set(bad_fs))[:5]}")

    arrows = len(re.findall(r"marker|polygon\s|<path[^>]*?[ML].*[zZ]", svg))
    need_ar = {"riding": 0, "relation": 3, "architecture": 6}[stage]
    add(f">= {need_ar} arrowheads", arrows >= need_ar or need_ar == 0,
        f"{arrows} marker/polygon/arrow-like elements")

    blob = re.sub(r"<[^>]+>", " ", svg)
    missing = [v for v in picks.values() if v and v.split(" (")[0] not in blob]
    add("picked subjects labeled", not missing,
        "all named in labels" if not missing else "missing: " + ", ".join(missing))

    has_leg = re.search(r"legend|caption|图例|说明", blob, re.I) is not None
    add("has legend/caption", has_leg,
        "legend/caption box found" if has_leg else "no legend or caption box")
    return out


def svg_to_png(svg_path: str, png_path: str, size: int = 1024) -> str:
    """Rasterize SVG -> PNG. Returns png path; raises on total failure."""
    if shutil.which("rsvg-convert"):
        subprocess.run(["rsvg-convert", "-w", str(size), "-h", str(size),
                        "-o", png_path, svg_path], check=True)
        return png_path

    chrome = None
    # google-chrome first: on Ubuntu /usr/bin/chromium is a snap wrapper
    # that misbehaves in headless environments
    for cand in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 shutil.which("google-chrome"), shutil.which("chromium"),
                 shutil.which("chromium-browser")):
        if cand and os.path.exists(cand):
            chrome = cand
            break
    if chrome:
        # the path must be absolute, or the file:// URL is invalid and Chrome renders an error page
        svg_path = os.path.abspath(svg_path)
        png_path = os.path.abspath(png_path)
        html = svg_path + ".wrap.html"
        with open(svg_path, encoding="utf-8") as f:
            svg = f.read()
        with open(html, "w", encoding="utf-8") as f:
            f.write(f'<body style="margin:0;background:#fff">'
                    f'<div style="width:{size}px;height:{size}px">{svg}</div>')
        user_data = tempfile.mkdtemp(prefix="wlb-chrome-")
        # --no-sandbox: Chrome's own seatbelt cannot nest inside the wlb
        # outer sandbox; the outer profile + SVG sanitization cover the
        # renderer instead.
        from ..chrome_capture import run_chrome_capture
        run_chrome_capture(
            [chrome, "--headless=new", "--disable-gpu",
             "--force-device-scale-factor=1",
             f"--user-data-dir={user_data}",
             "--no-sandbox", "--disable-crashpad",
             "--disable-crash-reporter",
             # on some headless environments (CI runners) Chrome never
             # renders the capture without a virtual time budget
             "--virtual-time-budget=2000",
             f"--screenshot={png_path}", f"--window-size={size},{size}",
             "--default-background-color=FFFFFF", "file://" + html],
            png_path, timeout=60)
        os.remove(html)
        shutil.rmtree(user_data, ignore_errors=True)
        return png_path
    raise RuntimeError("no rsvg-convert or Chrome found for SVG rasterization")


class SvgTask(BaseTask):
    task_type = "svg"

    def run(self, client, model: str,
            context: Optional[Dict[str, Any]] = None) -> TaskResult:
        built = build_instruction(self.spec)
        instruction = built["instruction"]
        size = int(self.spec.get("size", 1024))

        res = client.chat(
            model,
            [{"role": "user", "content": f"{instruction}\n\n{QUALITY_NOTE}"}],
            max_tokens=self.run_cfg.get("svg_max_tokens", 16384),
            temperature=self.run_cfg.get("temperature", 0.4))

        if not res.ok:
            return TaskResult(task_id=self.task_id, task_type=self.task_type,
                              model=model, provider=getattr(client, "label", "?"),
                              error=res.error, latency=res.latency,
                              usage=res.usage)

        os.makedirs(self.artifacts_dir, exist_ok=True)
        svg_path = os.path.join(self.artifacts_dir, f"{self.task_id}__{model}.svg")
        svg_raw = extract_svg(res.content or "")
        svg_ok, svg_fixed, svg_note = validate_svg(svg_raw)
        svg_fixed, n_sanitized = sanitize_svg(svg_fixed)
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg_fixed)

        artifacts = [svg_path]
        png_path = svg_path.replace(".svg", ".png")
        raster_error = None
        if not svg_ok:
            # unparsable SVG is never loaded into Chrome: the HTML parser
            # is forgiving and may still execute embedded scripts
            raster_error = "not rastered: SVG is not well-formed XML"
        else:
            try:
                svg_to_png(svg_path, png_path, size)
                artifacts.append(png_path)
            except Exception as e:  # noqa: BLE001
                raster_error = str(e)

        checks = svg_checks(svg_fixed,
                            self.spec.get("stage", "riding"),
                            built["picks"])
        if not svg_ok:
            checks.insert(1, {"constraint": "parses as XML", "ok": False,
                              "detail": svg_note})
        elif svg_note:
            checks.insert(1, {"constraint": "parses as XML", "ok": True,
                              "detail": svg_note})
        return TaskResult(
            task_id=self.task_id, task_type=self.task_type,
            model=model, provider=getattr(client, "label", "?"),
            score=None,   # human review against the rendered PNG
            detail={"status": "pending-human-review",
                    "stage": self.spec.get("stage"),
                    "picks": built["picks"],
                    "constraints": checks,
                    "constraints_passed": sum(1 for c in checks if c["ok"]),
                    "constraints_total": len(checks),
                    "svg_valid": svg_ok,
                    "svg_note": svg_note,
                    "svg_sanitized": n_sanitized,
                    "raster_blank": (raster_error is None and
                                     os.path.exists(png_path) and
                                     size >= 1024 and
                                     os.path.getsize(png_path) < 10_000),
                    "svg_bytes": os.path.getsize(svg_path),
                    "raster_error": raster_error},
            artifacts=artifacts,
            latency=res.latency, usage=res.usage,
        )
