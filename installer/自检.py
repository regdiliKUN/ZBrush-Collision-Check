# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测插件 —— 安装自检工具
========================================

一键检查插件是否安装完整、能否正常工作。若实机运行出错，先跑本工具定位问题。

用法：python 自检.py

检查项（全部中文输出）：
  1. Python 版本与 numpy 可用性
  2. 外部引擎工作目录 C:\\ZBCollision 的文件完整性
  3. 两个 ZBrush 版本的宏是否安装到位
  4. 宏的 IConfig 版本标记是否正确（2025 版=2025，2022 版=2021）
  5. 宏是否为纯 ASCII（ZScript 编译器要求）
  6. 外部工作目录是否无空格
  7. 内置几何用例自检（相交/分离判定是否正确）
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil

WORKDIR = r"C:\ZBCollision"
# 复用安装程序的发现逻辑，消除硬编码路径（换机器仍可自检）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import install as _install
    def _discover_zb():
        out = []
        for label, path in _install.discover_zbrush_paths():
            years = __import__("re").findall(r"20\d{2}", label + " " + path)
            year = years[0] if years else "2021"
            out.append((label, path, "2025" if int(year) >= 2025 else "2021"))
        return out
except Exception:
    def _discover_zb():
        return []
ENGINE_FILES = ["i18n.py", "collision_engine.py", "result_window.py", "detect_and_show.pyw",
                "run_detect.py", "run_detect.bat", "launcher.pyw",
                "bin/ZBCollisionEngine.exe"]

PASS = "[通过]"
FAIL = "[失败]"
WARN = "[提示]"

results = []


def log(status: str, msg: str):
    line = "%s %s" % (status, msg)
    print(line)
    results.append((status, msg))


def check_python():
    print("\n--- 1. Python 环境 ---")
    v = sys.version_info
    log(PASS if v.major == 3 else FAIL,
        "Python 版本：%d.%d.%d" % (v.major, v.minor, v.micro))
    try:
        import numpy
        log(PASS, "numpy 可用（版本 %s），检测将走加速路径。" % numpy.__version__)
    except ImportError:
        log(WARN, "numpy 未安装，检测将走纯 Python 备用路径（较慢但可用）。")


def check_engine():
    print("\n--- 2. 外部引擎工作目录 ---")
    if not os.path.isdir(WORKDIR):
        log(FAIL, "工作目录不存在：%s。请先运行安装程序 install.py。" % WORKDIR)
        return
    log(PASS, "工作目录存在：%s" % WORKDIR)
    if " " in WORKDIR:
        log(FAIL, "工作目录含空格，ZBrush 外部调用机制要求无空格路径。")
    else:
        log(PASS, "工作目录路径无空格。")
    for name in ENGINE_FILES:
        p = os.path.join(WORKDIR, name)
        if os.path.isfile(p):
            log(PASS, "引擎文件存在：%s" % name)
        else:
            log(FAIL, "引擎文件缺失：%s。请重新运行安装程序。" % name)
    # 失败路径保护：找不到 Python 时的中文说明页必须存在
    help_page = os.path.join(WORKDIR, "help_no_python.html")
    if os.path.isfile(help_page):
        log(PASS, "失败提示页存在（缺 Python 时自动打开）。")
    else:
        log(FAIL, "失败提示页缺失：help_no_python.html。请重新运行安装程序。")


def check_macros():
    print("\n--- 3. ZBrush 宏安装与版本标记 ---")
    import re
    zb_paths = _discover_zb()
    if not zb_paths:
        log(WARN, "未自动发现 ZBrush 安装目录；宏检查跳过。可用 --zbrush-path 或 install_config.json 指定。")
    for label, zb, expect_icfg in zb_paths:
        if not os.path.isdir(zb):
            log(WARN, "未找到 %s 安装目录：%s（跳过）" % (label, zb))
            continue
        legacy = os.path.join(zb, "ZStartup", "Macros", "分件碰撞检测")
        if os.path.isdir(legacy):
            log(WARN, "%s 仍有旧版中文名宏文件夹（会多出重复按钮），重新运行安装程序即可清理。" % label)
        for fname in ("Quick Check.TXT", "Full Check.TXT"):
            _check_one_macro(label, expect_icfg,
                             os.path.join(zb, "ZStartup", "Macros", "Collision Check", fname))


def _check_one_macro(label, expect_icfg, macro):
    import re
    label = "%s [%s]" % (label, os.path.basename(macro))
    if True:
        if not os.path.isfile(macro):
            log(FAIL, "%s 宏未安装：%s" % (label, macro))
            return
        log(PASS, "%s 宏已安装。" % label)
        data = open(macro, "rb").read()
        non_ascii = sum(1 for b in data if b > 127)
        if non_ascii == 0:
            log(PASS, "%s 宏为纯 ASCII（符合 ZScript 编译器要求）。" % label)
        else:
            log(FAIL, "%s 宏含 %d 个非 ASCII 字节，可能导致中文提示丢失。" % (label, non_ascii))
        text = data.decode("ascii", errors="replace")
        m = re.search(r"\[IConfig,(\d+)\]", text)
        if m and m.group(1) == expect_icfg:
            log(PASS, "%s IConfig=%s，版本标记正确。" % (label, expect_icfg))
        else:
            got = m.group(1) if m else "缺失"
            log(FAIL, "%s IConfig=%s，应为 %s。请重新运行安装程序。" % (label, got, expect_icfg))


def check_engine_function():
    print("\n--- 4. 检测引擎功能自检 ---")
    eng = os.path.join(WORKDIR, "collision_engine.py")
    if not os.path.isfile(eng):
        log(FAIL, "引擎文件不存在，跳过功能自检。")
        return
    sys.path.insert(0, WORKDIR)
    try:
        import collision_engine as ce
    except Exception as exc:
        log(FAIL, "引擎导入失败：%s" % exc)
        return
    import importlib
    importlib.reload(ce)

    tmp = tempfile.mkdtemp(prefix="zbc_check_")
    try:
        def cube(center, size=1.0):
            h = size / 2.0
            cx, cy, cz = center
            v = [(cx-h,cy-h,cz-h),(cx+h,cy-h,cz-h),(cx+h,cy+h,cz-h),(cx-h,cy+h,cz-h),
                 (cx-h,cy-h,cz+h),(cx+h,cy-h,cz+h),(cx+h,cy+h,cz+h),(cx-h,cy+h,cz+h)]
            f = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
                 (1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0)]
            return v, f

        def write(path, groups):
            lines, off = [], 0
            for name, center, size in groups:
                v, f = cube(center, size)
                lines.append("g " + name)
                lines.extend("v %.6f %.6f %.6f" % p for p in v)
                lines.extend("f %d %d %d" % (a+off+1, b+off+1, c+off+1) for a,b,c in f)
                off += len(v)
            open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")

        p_a = os.path.join(tmp, "sub_0.obj")
        p_b = os.path.join(tmp, "sub_1.obj")
        # 场景1：两个分离的立方体（模拟插件逐个导出的两个子工具）
        write(p_a, [("甲", (0, 0, 0), 1.0)])
        write(p_b, [("乙", (5, 0, 0), 1.0)])
        r1 = ce.run_detection(p_a)  # 引擎自动发现 sub_*.obj 多文件模式
        # 场景2：把 B 移到与 A 相交
        write(p_b, [("乙", (0.6, 0, 0), 1.0)])
        r2 = ce.run_detection(p_a)

        ok_miss = len(r1.intersecting_pairs) == 0
        ok_hit = len(r2.intersecting_pairs) == 1
        log(PASS if ok_miss else FAIL,
            "分离子工具判定：%s（模拟插件逐个导出模式）"
            % ("正确（无相交）" if ok_miss else "错误"))
        log(PASS if ok_hit else FAIL,
            "相交子工具判定：%s（模拟插件逐个导出模式）"
            % ("正确（检出相交）" if ok_hit else "错误"))

        # 场景3：写入 count.txt=2，并放一个残留 sub_9.obj，验证被正确忽略
        stale = os.path.join(tmp, "sub_9.obj")
        write(stale, [("残留", (50, 0, 0), 1.0)])
        with open(os.path.join(tmp, "count.txt"), "w", encoding="ascii") as fh:
            fh.write("2")
        st_counted, _ = ce.parse_obj_multi(tmp)
        ok_count = len(st_counted) == 2
        log(PASS if ok_count else FAIL,
            "残留文件过滤（count.txt）：%s"
            % ("正确（忽略残留 sub_9）" if ok_count else "错误"))
    except Exception as exc:
        log(FAIL, "功能自检出错：%s" % exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 56)
    print("ZBrush 分件碰撞检测插件 —— 安装自检")
    print("=" * 56)
    check_python()
    check_engine()
    check_macros()
    check_engine_function()

    print("\n" + "=" * 56)
    fails = [m for s, m in results if s == FAIL]
    warns = [m for s, m in results if s == WARN]
    if not fails:
        print("[总结] 全部检查通过，插件已正确安装。")
        if warns:
            print("[总结] 有 %d 条提示，请留意上方 [提示] 项。" % len(warns))
        print("\n使用：重启 ZBrush → Edit 模式 → Macro 面板 → Collision Check → Quick Check / Full Check")
    else:
        print("[总结] 发现 %d 项问题，请按上方 [失败] 项处理：" % len(fails))
        for m in fails:
            print("  - %s" % m)
        print("\n处理方法：重新运行 install.py 安装程序；若仍失败，把以上输出发我。")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
