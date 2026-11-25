# -*- coding: utf-8 -*-
"""Interface language for the collision check engine (Chinese / English).

The language comes from, in order:
  1. the ZBC_LANG environment variable ("zh" or "en");
  2. a lang.txt file in the working directory (written by the installer
     when it is run with --lang en), loaded with load(work_dir);
  3. Chinese, the default.

T(zh, en) returns the text for the current language. It is evaluated at call
time, so calling load() after the other modules are imported still works.
Data written for programs (result.json keys, internal markers) stays as it is.
"""
import os

_lang = "zh"


def _norm(value):
    value = (value or "").strip().lower()
    return "en" if value.startswith("en") else "zh"


def load(work_dir=None):
    """Pick up the language from ZBC_LANG or <work_dir>/lang.txt."""
    global _lang
    env = os.environ.get("ZBC_LANG")
    if env:
        _lang = _norm(env)
        return _lang
    if work_dir:
        path = os.path.join(work_dir, "lang.txt")
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                _lang = _norm(fh.read())
        except OSError:
            pass
    return _lang


def lang():
    return _lang


def T(zh, en):
    return en if _lang == "en" else zh


def report_name():
    """File name of the full text report in the working directory."""
    return T("完整报告.txt", "report.txt")


def history_name():
    """File name of the appended history log in the working directory."""
    return T("碰撞检测记录.txt", "collision_log.txt")


load()
