# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 回归测试套件
=====================================

把迭代过程中发现并修复的关键 bug 固化为可重复运行的测试，
防止后续改动重新引入。运行：

    python tests/回归测试.py

覆盖的质量维度：
  - 正确性：三角相交判定（含曾误报的"交线区间分离"案例）、包含关系
  - 边界情况：退化面过滤、残缺导出警告、count.txt 残留文件过滤
  - 性能：AABB 缓存（每网格只计算一次）
  - 一致性：摘要措辞在引擎与两个入口间一致
"""

from __future__ import annotations

import os
import sys
import tempfile
import shutil

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE, "engine")
sys.path.insert(0, ENGINE)

import collision_engine as ce

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    _results.append((name, status, detail))
    print("[%s] %s%s" % (status, name, ("  - " + detail) if detail else ""))


def cube_obj(path, center, size=1.0, name="T"):
    h = size / 2.0
    cx, cy, cz = center
    v = [(cx-h,cy-h,cz-h),(cx+h,cy-h,cz-h),(cx+h,cy+h,cz-h),(cx-h,cy+h,cz-h),
         (cx-h,cy-h,cz+h),(cx+h,cy-h,cz+h),(cx+h,cy+h,cz+h),(cx-h,cy+h,cz+h)]
    f = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
         (1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0)]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("g %s\n" % name)
        for p in v:
            fh.write("v %.6f %.6f %.6f\n" % p)
        for a, b, c in f:
            fh.write("f %d %d %d\n" % (a+1, b+1, c+1))


def test_numpy_fallback():
    """纯 Python 降级路径：无 numpy 时结果与 numpy 路径一致、报告后端标记正确。"""
    original_np, original_flag = ce.np, ce._HAS_NUMPY
    try:
        def snapshot():
            out = {}
            for f in sorted(os.listdir(os.path.join(BASE, "tests", "fixtures"))):
                if not f.endswith(".obj"):
                    continue
                r = ce.run_detection(os.path.join(BASE, "tests", "fixtures", f))
                out[f] = bool(r.intersecting_pairs)
            return out
        numpy_res = snapshot()
        # 模拟无 numpy
        ce.np, ce._HAS_NUMPY = None, False
        # 清缓存（AABB 缓存与后端无关，但三角预计算有关）
        pure_res = snapshot()
        check("无numpy降级结果一致", numpy_res == pure_res)
        # 报告后端标记正确
        r = ce.run_detection(os.path.join(BASE, "tests", "fixtures", "01_相交.obj"))
        check("降级后端标记正确", r.has_numpy is False)
        check("降级报告文案正确", "纯 Python" in ce.format_text_report(r))
    finally:
        ce.np, ce._HAS_NUMPY = original_np, original_flag


def test_dual_mode():
    """双模式：快速只 1 点，完整收集全部，完整模式报告含全部坐标。"""
    import tempfile as _tf
    tmp = _tf.mkdtemp(prefix="zbc_mode_")
    try:
        def cube(path, cx):
            h = 0.5
            V = [(cx-h,-h,-h),(cx+h,-h,-h),(cx+h,h,-h),(cx-h,h,-h),
                 (cx-h,-h,h),(cx+h,-h,h),(cx+h,h,h),(cx-h,h,h)]
            F = [(0,1,2),(0,2,3),(4,6,5),(4,7,6),(0,4,5),(0,5,1),
                 (1,5,6),(1,6,2),(2,6,7),(2,7,3),(3,7,4),(3,4,0)]
            open(path, "w").write("g T\n" +
                "".join("v %.3f %.3f %.3f\n" % v for v in V) +
                "".join("f %d %d %d\n" % (a+1,b+1,c+1) for a,b,c in F))
        cube(os.path.join(tmp, "sub_0.obj"), 0)
        cube(os.path.join(tmp, "sub_1.obj"), 0.5)
        rf = ce.run_detection(os.path.join(tmp, "sub_0.obj"), mode="first")
        ra = ce.run_detection(os.path.join(tmp, "sub_0.obj"), mode="all")
        pf, pa = rf.intersecting_pairs[0], ra.intersecting_pairs[0]
        check("双模式-快速仅1点", pf.total_hits == 1 and len(pf.all_hits) == 0)
        check("双模式-完整收集多点", pa.total_hits > 1 and len(pa.all_hits) > 1,
              "点数=%d" % pa.total_hits)
        check("双模式-报告区分模式",
              "快速模式" in ce.format_text_report(rf)
              and "完整模式" in ce.format_text_report(ra))
        d = ce.report_to_dict(ra)
        check("双模式-JSON含全部点",
              len(d["相交对"][0]["全部相交点"]) == pa.total_hits)
        check("双模式-计数与列表一致",
              pa.total_hits == len(pa.all_hits),
              "total=%d list=%d" % (pa.total_hits, len(pa.all_hits)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _strmerge_max_args(text):
    """返回所有 StrMerge 调用中最大的顶层参数个数。"""
    best, i = 0, 0
    while True:
        i = text.find("[StrMerge,", i)
        if i < 0:
            return best
        depth, j, args = 0, i + 1, 1
        while j < len(text):
            ch = text[j]
            if ch == "[":
                depth += 1
            elif ch == "]":
                if depth == 0:
                    break
                depth -= 1
            elif ch == "," and depth == 0:
                args += 1
            j += 1
        best = max(best, args - 1)   # 减去命令名本身
        i += 1


def test_zscript_signatures():
    """ZScript 结构：两个宏文件逐一校验。"""
    import re
    for fname in ("Quick Check.TXT", "Full Check.TXT"):
        macro = os.path.join(BASE, "zscript", "out", fname)
        tag = fname.split(".")[0]
        if not os.path.isfile(macro):
            check("ZScript-%s 存在" % tag, False, "先运行 zscript/generate_macro.py")
            continue
        data = open(macro, "rb").read()
        text = data.decode("ascii", errors="replace")
        check("ZScript-%s 纯ASCII" % tag, all(b < 128 for b in data))
        check("ZScript-%s 括号平衡" % tag, text.count("[") == text.count("]"))
        defs = set(re.findall(r"\[VarDef,([A-Za-z0-9_]+)", text))
        vref = set(re.findall(r"\[Var,([A-Za-z0-9_]+)\]", text))
        href = set(re.findall(r"#([A-Za-z0-9_]+)", text))
        check("ZScript-%s 变量均已声明" % tag, not (vref - defs) and not (href - defs),
              "未声明=%s" % sorted((vref - defs) | (href - defs)))
        check("ZScript-%s 无读文件命令" % tag, "MemReadString" not in text)
        check("ZScript-%s 仅一个 ??? 按钮" % tag,
              text.count("[IButton,") == 1 and "[IButton,???," in text)
        n = _strmerge_max_args(text)
        check("ZScript-%s StrMerge参数<=10" % tag, n <= 10, "最大=%d" % n)
        # 导出前必须先选中该子工具（选中的子工具无法隐藏）
        loop = text[text.find("[Loop,subCount,"):text.find("[NoteBar,#s_progress_done")]
        check("ZScript-%s 导出前SubToolSelect" % tag,
              loop.find("[SubToolSelect,ei]") >= 0
              and loop.find("[SubToolSelect,ei]") < loop.find('[IPress,"Tool:Export"]'))
        check("ZScript-%s 结束后恢复原选中" % tag, "[SubToolSelect,gOrigSel]" in text)
        expect_mode = "first" if tag.startswith("Quick") else "all"
        check("ZScript-%s 模式=%s" % (tag, expect_mode),
              '[VarDef,gMode,"%s"]' % expect_mode in text)
        for token in ("Tool:Export", "SubToolGetStatus", "ShellExecute",
                      "count.txt", "NoteBar", "ZBCollisionEngine.exe"):
            check("ZScript-%s 含 %s" % (tag, token), token in text)


def test_launcher_detach():
    """实机缺陷回归：ZBrush 的 ShellExecute 会阻塞等待被启动程序退出，
    检测入口必须先分离出子进程再立即退出，否则 ZBrush 卡死被系统关闭。"""
    src = open(os.path.join(BASE, "engine", "detect_and_show.pyw"),
               encoding="utf-8").read()
    main_body = src[src.find("def main():"):src.find("def _run():")]
    check("入口-首启即分离子进程", "_relaunch_detached()" in main_body
          and main_body.find("_relaunch_detached()") < main_body.find("_run()"))
    check("入口-分离标志防止无限重启", "DETACH_FLAG not in sys.argv" in main_body)
    check("入口-DETACHED_PROCESS 标志", "0x00000008" in src)


def test_triangle_correctness():
    """正确性：曾把交线区间分离的两三角形误报为相交。"""
    t1 = ((0, 0, 0), (4, 0, 0), (0, 4, 0))
    separated = ((10, 2, -1), (16, 2, -1), (13, 2, 1))
    overlapping = ((0, 2, -1), (6, 2, -1), (3, 2, 1))
    check("三角判定-交线区间分离不误报",
          not ce.triangles_intersect(t1, separated)[0])
    check("三角判定-真实相交检出",
          ce.triangles_intersect(t1, overlapping)[0])
    # 边-面穿越：边穿过三角形内部但不碰其任何边
    piercing = ((1, 1, -1), (1.1, 1, -1), (1, 1, 1))
    check("三角判定-边穿面检出",
          ce.triangles_intersect(t1, piercing)[0])

    # 数值尺度鲁棒性：绝对阈值曾在 1e-6 尺度漏检
    scales_ok = True
    for s in (1e-6, 1e-3, 1.0, 1e3, 1e6):
        tri = ((0, 0, 0), (4*s, 0, 0), (0, 4*s, 0))
        cross = ((0, 2*s, -s), (6*s, 2*s, -s), (3*s, 2*s, s))
        sep = ((10*s, 2*s, -s), (16*s, 2*s, -s), (13*s, 2*s, s))
        if not (ce.triangles_intersect(tri, cross)[0]
                and not ce.triangles_intersect(tri, sep)[0]):
            scales_ok = False
    check("三角判定-跨尺度一致(1e-6~1e6)", scales_ok)

    # 异面线段误报：s,t∈[0,1] 但空间距离>0 时不应算相交
    skew = ce._segments_intersect_3d((0, 0, 0), (1, 0, 0),
                                     (0.5, 0, 1), (0.5, 1, 1))
    check("异面线段不误报", not skew)
    sep_planes = ce.triangles_intersect(
        ((0, 0, 0), (1, 0, 0), (0, 1, 0)),
        ((0.5, 0, 1), (0.5, 1, 1), (1.5, 1, 1)))[0]
    check("异面分离三角形不误报", not sep_planes)


def test_degenerate_faces(tmp):
    """边界：共线退化面应被过滤。"""
    p = os.path.join(tmp, "degenerate.obj")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(["g t", "v 0 0 0", "v 1 0 0", "v 2 0 0",
                            "v 0 0 1", "v 1 1 1", "f 1 2 3", "f 1 4 5"]))
    sts, _ = ce.parse_obj(p)
    check("退化三角面被过滤", sts[0].face_count == 1,
          "面数=%d" % sts[0].face_count)


def test_multi_and_count(tmp):
    """边界：count.txt 截断残留 sub 文件；残缺导出给出不可信警告。"""
    cube_obj(os.path.join(tmp, "sub_0.obj"), (0, 0, 0), 1.0, "A")
    cube_obj(os.path.join(tmp, "sub_1.obj"), (5, 0, 0), 1.0, "B")
    with open(os.path.join(tmp, "count.txt"), "w", encoding="ascii") as fh:
        fh.write("2")
    # 放一个残留的高序号文件
    cube_obj(os.path.join(tmp, "sub_9.obj"), (50, 0, 0), 1.0, "残留")
    sts, _ = ce.parse_obj_multi(tmp)
    check("count.txt过滤残留文件", len(sts) == 2, "解析数=%d" % len(sts))

    # 残缺场景：只有一个有效 sub 但 count=3 -> 不可信警告
    with open(os.path.join(tmp, "count.txt"), "w", encoding="ascii") as fh:
        fh.write("3")
    os.remove(os.path.join(tmp, "sub_1.obj"))
    os.remove(os.path.join(tmp, "sub_9.obj"))
    report = ce.run_detection(os.path.join(tmp, "sub_0.obj"))
    check("残缺导出标记不可信",
          any("不可信" in w for w in report.warnings))


def test_aabb_cache():
    """性能：每个网格 AABB 只计算一次。"""
    sts, _ = ce.parse_obj(os.path.join(BASE, "tests", "fixtures",
                                       "04_三件部分相交.obj"))
    orig = ce.mesh_aabb
    calls = [0]
    def counted(v):
        calls[0] += 1
        return orig(v)
    ce.mesh_aabb = counted
    try:
        for st in sts:
            st._aabb = None
        for i in range(len(sts)):
            for j in range(i + 1, len(sts)):
                ce.detect_pair(sts[i], sts[j])
        check("AABB每网格仅算一次", calls[0] == len(sts),
              "调用=%d 网格=%d" % (calls[0], len(sts)))
    finally:
        ce.mesh_aabb = orig


def test_basic_fixtures():
    """回归：5 个基础几何用例。"""
    fixtures = {
        "01_相交.obj": True, "02_分离.obj": False, "03_包含.obj": True,
        "04_三件部分相交.obj": True, "05_接触.obj": True,
    }
    fdir = os.path.join(BASE, "tests", "fixtures")
    for name, expected in sorted(fixtures.items()):
        r = ce.run_detection(os.path.join(fdir, name))
        got = bool(r.intersecting_pairs)
        check("基础用例-" + name, got == expected,
              "期望=%s 实际=%s" % (expected, got))


def test_summary_consistency():
    """一致性：摘要措辞在引擎与两个入口间一致。"""
    r = ce.run_detection(os.path.join(BASE, "tests", "fixtures", "01_相交.obj"))
    s_engine = ce.format_summary(r)
    try:
        import run_detect as rd
        s_batch = rd.build_note_summary(r)
        check("摘要-引擎与批处理器一致", s_engine == s_batch)
    except Exception as exc:
        check("摘要-引擎与批处理器一致", False, str(exc))
    check("摘要-单行无换行", "\n" not in s_engine and "\r" not in s_engine)


def main():
    print("=" * 60)
    print("ZBrush 分件碰撞检测 —— 回归测试套件")
    print("=" * 60)
    test_triangle_correctness()
    test_launcher_detach()
    test_zscript_signatures()
    test_dual_mode()
    test_numpy_fallback()
    test_basic_fixtures()
    test_summary_consistency()
    test_aabb_cache()
    tmp = tempfile.mkdtemp(prefix="zbc_regress_")
    try:
        test_degenerate_faces(tmp)
        test_multi_and_count(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    failed = [r for r in _results if r[1] == FAIL]
    print("总计 %d 项，通过 %d，失败 %d" % (
        len(_results), len(_results) - len(failed), len(failed)))
    if failed:
        print("失败项：")
        for name, _, detail in failed:
            print("  - %s %s" % (name, detail))
        return 1
    print("全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
