"""Shared test helpers: import the scripts by path, build synthetic dump pages."""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

SCRIPTS = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..',
                                       'ClaudeCode', 'skills', 'documents', 'scripts'))
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def tmpdir(test):
    """A tempfile.mkdtemp() directory removed when the test ends."""
    d = tempfile.mkdtemp(prefix='documents-test-')
    test.addCleanup(shutil.rmtree, d, True)
    return d


def load(name):
    if name == 'replica_common':      # the very module the scripts import (one Refusal class)
        return importlib.import_module(name)
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, name + '.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(text, x, baseline, size=12.0, font='LiberationSerif', bold=False, italic=False, cw=6.0):
    """A dump run; every character cw pt wide."""
    return {'text': text, 'x': x, 'baseline': baseline,
            'bbox': [x, baseline - 0.9 * size, x + cw * len(text), baseline + 0.2 * size],
            'font': font, 'size': size, 'bold': bold, 'italic': italic, 'fill': [0, 0, 0]}


def page(n, runs, w=595.304, h=841.89, rects=None, images=None, rotation=0):
    return {'page': n, 'mediabox': [0.0, 0.0, w, h], 'cropbox': [0.0, 0.0, w, h], 'rotation': rotation,
            'width': w, 'height': h, 'runs': runs, 'rects': rects or [], 'images': images or []}


def body_page(n, pitch=13.8, first=71.801, lines=8, margin=56.8, right=538.5, pn=True, centre=297.65):
    """Heading (centred, bold) + a justified paragraph + page number."""
    runs = [run('Heading', centre - 21.0, first, bold=True, font='LiberationSerif-Bold', cw=6.0)]
    y = first + 2 * pitch
    for i in range(lines):
        full = i < lines - 1
        width = right - margin if full else 120.0
        runs.append(run('x' * int(round(width / 6.0)), margin, round(y, 3), cw=width / int(round(width / 6.0))))
        y += pitch
    if pn:
        runs.append(run(str(n), 291.8, 782.601))
    return page(n, runs)


def write_jsonl(path, pages):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(json.dumps({'convention': 'top-left,y-down,pt', 'fonts': []}) + '\n')
        for p in pages:
            fh.write(json.dumps(p) + '\n')
