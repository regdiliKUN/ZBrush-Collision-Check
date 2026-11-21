# -*- coding: utf-8 -*-
"""
生成测试用 OBJ 模型，覆盖多种相交情形，用于验证检测引擎。
每个立方体作为一个 group（模拟 ZBrush 导出的 SubTool）。
输出目录：tests/fixtures
"""

import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
FIXDIR = os.path.join(BASE, "fixtures")
os.makedirs(FIXDIR, exist_ok=True)


def cube(center, size=1.0):
    h = size / 2.0
    cx, cy, cz = center
    v = [
        (cx-h, cy-h, cz-h), (cx+h, cy-h, cz-h),
        (cx+h, cy+h, cz-h), (cx-h, cy+h, cz-h),
        (cx-h, cy-h, cz+h), (cx+h, cy-h, cz+h),
        (cx+h, cy+h, cz+h), (cx-h, cy+h, cz+h),
    ]
    f = [
        (0,1,2),(0,2,3),  # 底
        (4,6,5),(4,7,6),  # 顶
        (0,4,5),(0,5,1),  # 前
        (1,5,6),(1,6,2),  # 右
        (2,6,7),(2,7,3),  # 后
        (3,7,4),(3,4,0),  # 左
    ]
    return v, f


def write_obj(path, groups):
    """groups: [(name, center, size)]"""
    lines = ["# 测试模型 - ZBrush 分件碰撞检测"]
    voff = 0
    for name, center, size in groups:
        v, f = cube(center, size)
        lines.append("g %s" % name)
        for (x, y, z) in v:
            lines.append("v %.6f %.6f %.6f" % (x, y, z))
        for (a, b, c) in f:
            lines.append("f %d %d %d" % (a+voff+1, b+voff+1, c+voff+1))
        voff += len(v)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    print("已生成：%s" % path)


def main():
    # 用例1：两个明显相交的立方体
    write_obj(os.path.join(FIXDIR, "01_相交.obj"), [
        ("立方体A", (0, 0, 0), 1.0),
        ("立方体B", (0.5, 0, 0), 1.0),
    ])
    # 用例2：两个完全分离的立方体
    write_obj(os.path.join(FIXDIR, "02_分离.obj"), [
        ("立方体A", (0, 0, 0), 1.0),
        ("立方体B", (3, 0, 0), 1.0),
    ])
    # 用例3：一个完全包含另一个（内外相交）
    write_obj(os.path.join(FIXDIR, "03_包含.obj"), [
        ("外壳", (0, 0, 0), 2.0),
        ("内核", (0, 0, 0), 0.6),
    ])
    # 用例4：三个子工具，仅 A 与 C 相交
    write_obj(os.path.join(FIXDIR, "04_三件部分相交.obj"), [
        ("头部", (0, 0, 0), 1.0),
        ("手臂", (4, 0, 0), 1.0),
        ("身体", (0.4, 0, 0), 1.0),
    ])
    # 用例5：刚好共面接触（面贴面）
    write_obj(os.path.join(FIXDIR, "05_接触.obj"), [
        ("左块", (0, 0, 0), 1.0),
        ("右块", (1.0, 0, 0), 1.0),
    ])


if __name__ == "__main__":
    main()
