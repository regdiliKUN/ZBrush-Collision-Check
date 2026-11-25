# -*- coding: utf-8 -*-
"""英文界面（ZBC_LANG=en / lang.txt=en）的回归测试。

重点不是逐字核对翻译，而是确认：
  - 报告、摘要、记录都输出英文；
  - "导出不完整 → 结果不可信" 的判断在英文下仍然成立（这是安全相关逻辑）；
  - 合并导出（只有一个未命名子工具）的警告在英文下仍会触发；
  - lang.txt 能被读取，结果文件名随语言切换。
运行：python tests/英文模式测试.py
"""
import os
import re
import sys
import shutil
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "engine"))
os.environ["ZBC_LANG"] = "en"

import i18n                     # noqa: E402
import collision_engine as ce   # noqa: E402

CJK = re.compile(r"[一-鿿「」【】：，。；]")
results = []


def check(name, ok, info=""):
    results.append(ok)
    print("[%s] %s %s" % ("PASS" if ok else "FAIL", name, info))


def cube_obj(path, center, size=1.0, name="T"):
    h = size / 2.0
    cx, cy, cz = center
    v = [(cx-h, cy-h, cz-h), (cx+h, cy-h, cz-h), (cx+h, cy+h, cz-h), (cx-h, cy+h, cz-h),
         (cx-h, cy-h, cz+h), (cx+h, cy-h, cz+h), (cx+h, cy+h, cz+h), (cx-h, cy+h, cz+h)]
    f = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6), (0, 4, 5), (0, 5, 1),
         (1, 5, 6), (1, 6, 2), (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    with open(path, "w", encoding="utf-8") as fh:
        if name:
            fh.write("g %s\n" % name)
        for p in v:
            fh.write("v %.6f %.6f %.6f\n" % p)
        for a, b, c in f:
            fh.write("f %d %d %d\n" % (a + 1, b + 1, c + 1))


def no_cjk(text):
    return not CJK.search(text)


def main():
    tmp = tempfile.mkdtemp(prefix="zbc_en_")
    try:
        check("语言为英文", i18n.lang() == "en")
        check("结果文件名为英文", i18n.report_name() == "report.txt"
              and i18n.history_name() == "collision_log.txt")

        # 相交
        cube_obj(os.path.join(tmp, "sub_0.obj"), (0, 0, 0), 1.0, "PartA")
        cube_obj(os.path.join(tmp, "sub_1.obj"), (0.5, 0, 0), 1.0, "PartB")
        with open(os.path.join(tmp, "count.txt"), "w", encoding="ascii") as fh:
            fh.write("2")
        r = ce.run_detection(os.path.join(tmp, "sub_0.obj"), zbrush_version="2025")
        s = ce.format_summary(r)
        check("相交-摘要英文", s.startswith("[Result] Found 1 intersection") and no_cjk(s), s)
        t = ce.format_text_report(r)
        check("相交-报告无中文", no_cjk(t))
        check("快速模式文案", "Quick (first intersection point per pair)" in t)
        ra = ce.run_detection(os.path.join(tmp, "sub_0.obj"), mode="all")
        check("完整模式文案", "Full (all intersection points)" in ce.format_text_report(ra))
        hist = os.path.join(tmp, i18n.history_name())
        ce.append_history(r, hist)
        with open(hist, encoding="utf-8") as fh:
            h = fh.read()
        check("记录英文", no_cjk(h) and "1 intersection(s)" in h)

        # 分离
        cube_obj(os.path.join(tmp, "sub_1.obj"), (5, 0, 0), 1.0, "PartB")
        r = ce.run_detection(os.path.join(tmp, "sub_0.obj"))
        s = ce.format_summary(r)
        check("分离-摘要英文", s.startswith("[Result] No intersections found") and no_cjk(s), s)

        # 导出不完整：count=3 但只有 1 个有效 sub -> 必须标为不可信
        with open(os.path.join(tmp, "count.txt"), "w", encoding="ascii") as fh:
            fh.write("3")
        os.remove(os.path.join(tmp, "sub_1.obj"))
        r = ce.run_detection(os.path.join(tmp, "sub_0.obj"))
        s = ce.format_summary(r)
        check("残缺导出-英文仍标不可信", s.startswith("[IMPORTANT]"), s)

        # 合并导出：没有分组的单个 OBJ -> 未命名子工具警告 + 不可信
        merged = os.path.join(tmp, "merged", "all.obj")
        os.makedirs(os.path.dirname(merged))
        cube_obj(merged, (0, 0, 0), 1.0, name=None)
        r = ce.run_detection(merged)
        check("合并导出-子工具名英文", r.subtools and r.subtools[0].startswith("Unnamed SubTool"), str(r.subtools))
        s = ce.format_summary(r)
        check("合并导出-英文仍标不可信", s.startswith("[IMPORTANT]"), s)

        # lang.txt
        del os.environ["ZBC_LANG"]
        with open(os.path.join(tmp, "lang.txt"), "w", encoding="utf-8") as fh:
            fh.write("en\n")
        i18n._lang = "zh"
        check("读取 lang.txt", i18n.load(tmp) == "en")
        os.remove(os.path.join(tmp, "lang.txt"))
        i18n._lang = "zh"
        check("无 lang.txt 默认中文", i18n.load(tmp) == "zh")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    print("总计 %d 项，通过 %d，失败 %d" % (len(results), sum(results), len(results) - sum(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
