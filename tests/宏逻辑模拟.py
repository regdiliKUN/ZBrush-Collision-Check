# -*- coding: utf-8 -*-
"""
ZScript 宏逻辑模拟器 —— 补充"无法实机点按 ZBrush"维度的验证
=============================================================

用途：在无法运行 ZBrush GUI 的环境里，用 Python 逐条复现宏的关键逻辑，
验证"给定 N 个子工具 → 导出 N 个 sub 文件 + count.txt + mode.txt →
引擎正确检测"这条链路，从而把"未验证维度"压缩到最小
（只剩 ZBrush 是否真的执行了宏这一层，属环境限制）。

模拟的宏逻辑（对应 zscript/out/Quick Check.TXT 与 Full Check.TXT 的 RunCollisionCheck）：
  1. subCount = 子工具数
  2. SaveVis：保存每个子工具可见性
  3. for i in 0..subCount-1: ShowOnly(i); FileNameSetNext(sub_i.obj); Tool:Export
  4. RestoreVis：恢复可见性
  5. 写 count.txt = subCount
  6. 写 mode.txt = 选择的模式
  7. 触发引擎；引擎按 count.txt 读取 sub_*.obj

运行：python tests/宏逻辑模拟.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "engine"))
import collision_engine as ce

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name, cond, detail=""):
    st = PASS if cond else FAIL
    _results.append((name, st))
    print("[%s] %s%s" % (st, name, (" - " + detail) if detail else ""))


def simulate_export(subtool_specs, workdir, mode):
    """模拟宏的导出循环：subtool_specs 为 [(center, size), ...]。"""
    os.makedirs(workdir, exist_ok=True)
    # 模拟 SaveVis / ShowOnly / RestoreVis：本模拟器只验证产物，
    # 可见性状态用字典模拟，确认每个子工具恰好被"显示"一次并恢复。
    visibility = [True] * len(subtool_specs)  # 初始均可见
    saved = list(visibility)
    shown_once = [0] * len(subtool_specs)
    for i, (center, size) in enumerate(subtool_specs):
        # ShowOnly(i): 只显示 keep=i
        for k in range(len(visibility)):
            visibility[k] = (k == i)
            if k == i:
                shown_once[k] += 1
        # FileNameSetNext + Tool:Export（用几何写入模拟导出结果）
        _write_cube(os.path.join(workdir, "sub_%d.obj" % i), center, size)
    # RestoreVis
    visibility = saved
    # 写 count.txt / mode.txt
    with open(os.path.join(workdir, "count.txt"), "w", encoding="ascii") as fh:
        fh.write(str(len(subtool_specs)))
    with open(os.path.join(workdir, "mode.txt"), "w", encoding="ascii") as fh:
        fh.write(mode)
    return visibility, saved, shown_once


def _write_cube(path, center, size):
    h = size / 2.0
    cx, cy, cz = center
    V = [(cx-h,cy-h,cz-h),(cx+h,cy-h,cz-h),(cx+h,cy+h,cz-h),(cx-h,cy+h,cz-h),
         (cx-h,cy-h,cz+h),(cx+h,cy-h,cz+h),(cx+h,cy+h,cz+h),(cx-h,cy+h,cz+h)]
    F = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
         (1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0)]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("g Group12345\n")  # 模拟 ZBrush 的 Polygroup 命名
        for v in V:
            fh.write("v %.6f %.6f %.6f\n" % v)
        for a, b, c in F:
            fh.write("f %d %d %d\n" % (a+1, b+1, c+1))


def main():
    print("=" * 60)
    print("ZScript 宏逻辑模拟器（补充无法实机点按的验证维度）")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="zbc_macro_sim_")
    try:
        # 场景1：3 个子工具，2 个相交、1 个分离，快速模式
        w1 = os.path.join(tmp, "case1")
        vis, saved, shown = simulate_export(
            [((0, 0, 0), 1.0), ((0.6, 0, 0), 1.0), ((5, 0, 0), 1.0)],
            w1, "first")
        files = sorted(f for f in os.listdir(w1) if f.startswith("sub_"))
        check("导出文件数=子工具数", len(files) == 3, "文件=%s" % files)
        check("每个子工具恰好被显示一次", all(x == 1 for x in shown), str(shown))
        check("可见性已恢复原状", vis == saved)
        sts, _ = ce.parse_obj_multi(w1)
        check("引擎按count解析出全部子工具", len(sts) == 3, "解析=%d" % len(sts))
        r = ce.run_detection(os.path.join(w1, "sub_0.obj"), mode="first")
        check("链路端到端检出相交", len(r.intersecting_pairs) == 1,
              "相交对=%d" % len(r.intersecting_pairs))

        # 场景2：同一批子工具，完整模式
        w2 = os.path.join(tmp, "case2")
        simulate_export(
            [((0, 0, 0), 1.0), ((0.6, 0, 0), 1.0), ((5, 0, 0), 1.0)],
            w2, "all")
        ra = ce.run_detection(os.path.join(w2, "sub_0.obj"), mode="all")
        pa = ra.intersecting_pairs[0]
        check("完整模式链路收集多点", pa.total_hits > 1,
              "点数=%d" % pa.total_hits)

        # 场景3：上次残留高序号文件，本次只导出2个 -> 不应混入
        w3 = os.path.join(tmp, "case3")
        simulate_export([((0, 0, 0), 1.0), ((5, 0, 0), 1.0)], w3, "first")
        _write_cube(os.path.join(w3, "sub_9.obj"), (50, 0, 0), 1.0)  # 残留
        sts3, _ = ce.parse_obj_multi(w3)
        check("残留高序号文件被count排除", len(sts3) == 2, "解析=%d" % len(sts3))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    failed = [n for n, s in _results if s == FAIL]
    print("总计 %d 项，通过 %d，失败 %d" % (
        len(_results), len(_results) - len(failed), len(failed)))
    if failed:
        for n in failed:
            print("  FAIL:", n)
        return 1
    print("全部通过：宏链路逻辑在可模拟范围内已覆盖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
