# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测引擎
========================

职责：读取 ZBrush 导出的 OBJ 文件（所有 SubTool 按 group/object 分组），
对全部 SubTool 两两进行精确的三角面相交检测，输出中文结果报告与持久化记录。

设计要点：
  - 只依赖 numpy（ZBrush 用户机上常见）；无 numpy 时自动降级为纯 Python 实现。
  - 逐个导出模式：读取 sub_0.obj、sub_1.obj…，每个文件是一个子工具；
    count.txt 界定本次导出的数量，自动忽略上次残留文件。
  - 宽相（Broad Phase）：网格级 AABB（缓存）+ 三角面级 AABB 广播求交，
    快速排除不可能相交的三角面对。
  - 窄相（Narrow Phase）：共面用 2D SAT；空间相交用严格的三三角形
    边-面求交（Möller–Trumbore），避免"各自跨越平面但交线分离"的误报。
  - 包含关系：AABB 包含时用射线奇偶法判定"一个完全穿入另一个内部"。
  - 退化三角面（共线）在解析阶段过滤，避免法线计算数值不稳定。
  - 采用"命中即早退"策略：一旦某对 SubTool 找到一处相交即停止该对检测。
  - 全部面向用户的文本均为中文。

命令行用法：
    python collision_engine.py --obj <输入.obj> [--out-dir <输出目录>] [--json <结果.json>]

退出码：
    0 = 检测完成（无论是否有相交）
    2 = 参数/文件错误
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from i18n import T

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:  # pragma: no cover - 降级路径
    np = None
    _HAS_NUMPY = False


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SubToolMesh:
    """一个 SubTool 的几何数据。"""
    name: str
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    faces: List[Tuple[int, int, int]] = field(default_factory=list)

    # 预计算的三角面顶点数组（numpy），惰性填充
    _tri_a: object = None
    _tri_b: object = None
    _tri_c: object = None
    _tri_min: object = None
    _tri_max: object = None
    _aabb: object = None

    @property
    def face_count(self) -> int:
        return len(self.faces)


@dataclass
class HitInfo:
    """一处相交的详细信息。"""
    subtool_a: str
    subtool_b: str
    face_a: int
    face_b: int
    contact_point: Tuple[float, float, float]


@dataclass
class PairResult:
    """一对 SubTool 的检测结果。

    mode=first 时 hit 只保留第一处相交点（快速）；
    mode=all   时 all_hits 收集全部相交点（受 max_hits 上限约束）。
    """
    name_a: str
    name_b: str
    intersecting: bool
    triangles_tested: int = 0
    hit: Optional[HitInfo] = None
    all_hits: List[HitInfo] = field(default_factory=list)
    hits_truncated: bool = False
    total_hits: int = 0


@dataclass
class Report:
    """整次检测的汇总结果。"""
    obj_path: str
    timestamp: str
    zbrush_version: str = T("未知", "unknown")
    subtools: List[str] = field(default_factory=list)
    pairs: List[PairResult] = field(default_factory=list)
    elapsed_sec: float = 0.0
    warnings: List[str] = field(default_factory=list)
    mode: str = "first"
    has_numpy: bool = True  # 由 run_detection 按实际后端赋值

    @property
    def intersecting_pairs(self) -> List[PairResult]:
        return [p for p in self.pairs if p.intersecting]

    @property
    def pair_count(self) -> int:
        return len(self.pairs)


# ---------------------------------------------------------------------------
# OBJ 解析（按 group / object 分组为 SubTool）
# ---------------------------------------------------------------------------

def _float_triplet(tokens: Sequence[str]) -> Optional[Tuple[float, float, float]]:
    if len(tokens) < 3:
        return None
    try:
        return (float(tokens[0]), float(tokens[1]), float(tokens[2]))
    except ValueError:
        return None


def _non_degenerate(a, b, c, eps2=1e-18) -> bool:
    """三角形是否非退化（叉积平方大于阈值），避免 SAT 法线计算不稳定。"""
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    cx = uy * vz - uz * vy
    cy = uz * vx - ux * vz
    cz = ux * vy - uy * vx
    return (cx * cx + cy * cy + cz * cz) > eps2


def parse_obj(path: str, merge_all: bool = False) -> Tuple[List[SubToolMesh], List[str]]:
    """
    解析 OBJ，按 `g` / `o` 分组为多个 SubTool。

    参数 merge_all=True 时忽略所有分组，把全部面归为单个子工具
    （逐个导出模式下使用：一个 sub_i.obj 文件就是一个子工具，
    内部的 g GroupNNN 只是 Polygroup，应合并而非区分）。

    兼容性处理：
      - 顶点索引支持负数（相对索引）。
      - 面支持三角/四边/多边（做扇形三角化）。
      - 忽略 vt / vn 的索引，仅取顶点位置用于几何检测。

    返回 (subtools, warnings)
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(T("找不到 OBJ 文件：%s", "OBJ file not found: %s") % path)

    subtools: List[SubToolMesh] = []
    warnings: List[str] = []
    all_vertices: List[Tuple[float, float, float]] = []
    current: Optional[SubToolMesh] = None

    if merge_all:
        # 快速路径：所有面归一组，不解析 g/o，避免逐组处理开销
        current = SubToolMesh(name=T("合并网格", "Merged mesh"))
        subtools.append(current)

    def ensure_group(name: str) -> SubToolMesh:
        nonlocal current
        # 复用同名组（OBJ 可能分段重复出现同名 group）
        for st in subtools:
            if st.name == name:
                current = st
                return st
        st = SubToolMesh(name=name)
        subtools.append(st)
        current = st
        return st

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            # 快速过滤：OBJ 里 vt/vn/#/空行占多数，按首字符跳过避免 split
            head = raw[:1]
            if head != "v" and head != "f" and head != "g" and head != "o":
                continue
            # 精确首 token（排除 vt/vn 等以 v 开头的非顶点行）
            if head == "v" and not (raw.startswith("v ") or raw.startswith("v\t")):
                continue
            parts = raw.split()
            if not parts:
                continue
            tag = parts[0]

            if tag == "v":
                # 内联解析，避免函数调用开销（这是最热路径）
                try:
                    all_vertices.append((float(parts[1]), float(parts[2]),
                                         float(parts[3])))
                except (ValueError, IndexError):
                    warnings.append(T("第 %d 行顶点格式无效，已跳过。", "Line %d: invalid vertex, skipped.") % lineno)

            elif tag == "g" or tag == "o":
                if merge_all:
                    continue  # 忽略 Polygroup 分组
                name = " ".join(parts[1:]).strip()
                if not name or name in ("default", "Default"):
                    name = T("未命名子工具_%d", "Unnamed SubTool_%d") % (len(subtools) + 1)
                ensure_group(name)

            elif tag == "f":
                if current is None:
                    ensure_group(T("未命名子工具_%d", "Unnamed SubTool_%d") % (len(subtools) + 1))
                idx_raw = parts[1:]
                idx: List[int] = []
                for tok in idx_raw:
                    # OBJ 面索引格式：v / vt / vn，只取 v
                    vpart = tok.split("/")[0]
                    if not vpart:
                        continue
                    try:
                        iv = int(vpart)
                    except ValueError:
                        continue
                    n = len(all_vertices)
                    if iv < 0:
                        iv = n + iv + 1  # 相对索引
                    if 1 <= iv <= n:
                        idx.append(iv - 1)
                    else:
                        warnings.append(
                            T("第 %d 行的面引用了不存在的顶点 %d，已忽略该角。", "Line %d: a face uses vertex %d, which does not exist; that corner was ignored.")
                            % (lineno, iv)
                        )
                # 扇形三角化；过滤索引重复与共线退化面
                for k in range(1, len(idx) - 1):
                    a, b, c = idx[0], idx[k], idx[k + 1]
                    if a != b and b != c and a != c and \
                            _non_degenerate(all_vertices[a], all_vertices[b],
                                            all_vertices[c]):
                        current.faces.append((a, b, c))

    if not subtools:
        warnings.append(T("OBJ 中没有找到任何分组（g/o），已将全部面视为单个子工具。", "No groups (g/o) found in the OBJ; all faces are treated as one SubTool."))
        st = SubToolMesh(name=T("未命名子工具", "Unnamed SubTool"))
        st.vertices = list(all_vertices)
        subtools.append(st)

    # 每个 SubTool 只保存自己用到的顶点（重新映射），减少内存与计算量
    result: List[SubToolMesh] = []
    for st in subtools:
        if not st.faces:
            warnings.append(T('子工具"%s"没有任何有效三角面，已跳过。', 'SubTool "%s" has no valid triangles, skipped.') % st.name)
            continue
        used = sorted({i for f in st.faces for i in f})
        remap = {old: new for new, old in enumerate(used)}
        st.vertices = [all_vertices[i] for i in used]
        st.faces = [tuple(remap[i] for i in f) for f in st.faces]
        result.append(st)

    if not result:
        raise ValueError(T("OBJ 中没有可用于检测的有效三角面。", "The OBJ has no valid triangles to check."))

    return result, warnings


def parse_obj_multi(directory: str, prefix: str = "sub_",
                    manifest_path: Optional[str] = None):
    """
    逐个导出模式：读取目录下 sub_0.obj, sub_1.obj ... 每个文件作为一个子工具。

    ZBrush 导出的 OBJ 用 `g GroupNNN` 标记多边形组（Polygroup），并非子工具名，
    因此整体导出无法可靠区分 SubTool。让 ZScript 逐个隐藏/导出子工具，
    每个文件对应一个确定的子工具，是最可靠的方式。

    同一文件内的多个 polygroup 会被合并为同一个子工具（几何相加）。

    返回 (subtools, warnings)；若目录中没有匹配文件则返回 ([], [])。
    """
    import glob
    import re as _re

    pattern = os.path.join(directory, prefix + "*.obj")
    def file_index(p):
        m = _re.findall(r"(\d+)", os.path.basename(p))
        return int(m[-1]) if m else -1

    paths = sorted(glob.glob(pattern), key=file_index)
    # 若 ZBrush 写了 count.txt，只取本次导出的前 N 个文件，
    # 忽略上次运行可能残留的更高序号 sub_*.obj。
    count_path = os.path.join(directory, "count.txt")
    if os.path.isfile(count_path):
        try:
            with open(count_path, "r", encoding="ascii", errors="ignore") as fh:
                # ZScript 写数字可能带小数（如 "3.000"），先转 float 再取整
                expected = int(float(fh.read().strip()))
            paths = [p for p in paths if 0 <= file_index(p) < expected]
        except Exception:
            pass
    if not paths:
        return [], []

    names: List[str] = []
    if manifest_path and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as fh:
                names = [ln.strip() for ln in fh if ln.strip()]
        except Exception:
            names = []

    subtools: List[SubToolMesh] = []
    warnings: List[str] = []
    valid_indices = []
    for idx, path in enumerate(paths):
        m = _re.findall(r"(\d+)", os.path.basename(path))
        file_idx = int(m[-1]) if m else idx
        valid_indices.append(file_idx)
        try:
            # 快速路径：一个 sub_i.obj 文件就是一个子工具，内部 Polygroup 全部合并
            parts, w = parse_obj(path, merge_all=True)
        except Exception as exc:
            warnings.append(T("读取 %s 失败：%s", "Failed to read %s: %s") % (os.path.basename(path), exc))
            continue
        if not parts or not parts[0].faces:
            warnings.append(T("子工具文件 %s 没有有效面，可能导出失败，已跳过。", "SubTool file %s has no valid faces (export may have failed); skipped.")
                            % os.path.basename(path))
            continue
        nm = names[idx] if idx < len(names) else (T("子工具 %d", "SubTool %d") % (file_idx + 1))
        mesh = parts[0]
        mesh.name = nm
        subtools.append(mesh)
        warnings.extend(w)

    # 序号缺口检测：sub_0, sub_2 缺 sub_1 说明有子工具导出失败
    if valid_indices:
        full = set(range(min(valid_indices), max(valid_indices) + 1))
        gaps = sorted(full - set(valid_indices))
        if gaps:
            warnings.append(T("以下子工具文件缺失，可能导出失败：%s。检测结果可能不完整。", "These SubTool files are missing (export may have failed): %s. The result may be incomplete.")
                            % ", ".join("sub_%d.obj" % g for g in gaps))

    return subtools, warnings


# ---------------------------------------------------------------------------
# 几何工具函数（纯 Python 兜底 + numpy 加速）
# ---------------------------------------------------------------------------

def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _normalize(a):
    length = (_dot(a, a)) ** 0.5
    if length < 1e-15:
        return (0.0, 0.0, 0.0)
    return (a[0] / length, a[1] / length, a[2] / length)


def _segments_intersect_3d(p1, p2, q1, q2, eps=1e-9):
    """判断两条空间线段是否相交（含共面与异面）。"""
    d1 = _sub(p2, p1)
    d2 = _sub(q2, q1)
    r = _sub(p1, q1)
    a = _dot(d1, d1)
    e = _dot(d2, d2)
    f = _dot(d2, r)
    if a < 1e-18 and e < 1e-18:
        # 两点重合
        return _dot(r, r) <= eps * eps
    if a < 1e-18:
        s = f / e
        s = min(1.0, max(0.0, s))
        closest = (q1[0] + d2[0] * s, q1[1] + d2[1] * s, q1[2] + d2[2] * s)
        return _dot(_sub(p1, closest), _sub(p1, closest)) <= eps * eps
    if e < 1e-18:
        t = -_dot(d1, r) / a
        t = min(1.0, max(0.0, t))
        closest = (p1[0] + d1[0] * t, p1[1] + d1[1] * t, p1[2] + d1[2] * t)
        return _dot(_sub(q1, closest), _sub(q1, closest)) <= eps * eps
    c = _dot(d1, r)
    b = _dot(d1, d2)
    denom = a * e - b * b
    if abs(denom) > 1e-18:
        s = (b * f - c * e) / denom
        t = (a * f - b * c) / denom
        if -eps <= s <= 1 + eps and -eps <= t <= 1 + eps:
            # 关键：s,t 落在 [0,1] 只说明两条"无限直线"的最近点在线段范围内。
            # 对异面直线，最近点距离仍可能 >0，必须验证距离≈0 才算相交。
            sc = min(1.0, max(0.0, s))
            tc = min(1.0, max(0.0, t))
            c1 = (p1[0] + d1[0] * sc, p1[1] + d1[1] * sc, p1[2] + d1[2] * sc)
            c2 = (q1[0] + d2[0] * tc, q1[1] + d2[1] * tc, q1[2] + d2[2] * tc)
            diff = _sub(c1, c2)
            return _dot(diff, diff) <= eps * eps
    # 平行：检查端点是否落在另一条线段上
    return (
        _point_on_segment(q1, p1, p2, eps)
        or _point_on_segment(q2, p1, p2, eps)
        or _point_on_segment(p1, q1, q2, eps)
        or _point_on_segment(p2, q1, q2, eps)
    )


def _point_on_segment(p, a, b, eps=1e-7) -> bool:
    ab = _sub(b, a)
    ap = _sub(p, a)
    ab2 = _dot(ab, ab)
    if ab2 < 1e-18:
        return _dot(ap, ap) <= eps * eps
    t = _dot(ap, ab) / ab2
    if t < -eps or t > 1 + eps:
        return False
    proj = (a[0] + ab[0] * t, a[1] + ab[1] * t, a[2] + ab[2] * t)
    return _dot(_sub(p, proj), _sub(p, proj)) <= eps * eps


def triangles_intersect(t1, t2, eps=1e-9):
    """
    两个三角形是否相交（接触/穿透均算相交）。
    t1, t2: 各为 ((x,y,z),(x,y,z),(x,y,z))。
    返回 (bool, 近似接触点)。
    """
    a1, b1, c1 = t1
    a2, b2, c2 = t2

    n1 = _normalize(_cross(_sub(b1, a1), _sub(c1, a1)))
    n2 = _normalize(_cross(_sub(b2, a2), _sub(c2, a2)))

    # 1) 顶点-对面判定：任一顶点落在另一三角形所在平面上且在其内部
    d1 = [_dot(n1, _sub(v, a1)) for v in (a2, b2, c2)]
    if all(abs(d) <= eps for d in d1):
        # 共面 -> 用 2D SAT 细化
        if _coplanar_triangles_intersect(t1, t2, n1, eps):
            center = (
                (a1[0] + b1[0] + c1[0] + a2[0] + b2[0] + c2[0]) / 6.0,
                (a1[1] + b1[1] + c1[1] + a2[1] + b2[1] + c2[1]) / 6.0,
                (a1[2] + b1[2] + c1[2] + a2[2] + b2[2] + c2[2]) / 6.0,
            )
            return True, center
    d2 = [_dot(n2, _sub(v, a2)) for v in (a1, b1, c1)]
    if all(abs(d) <= eps for d in d2):
        if _coplanar_triangles_intersect(t2, t1, n2, eps):
            center = (
                (a1[0] + b1[0] + c1[0] + a2[0] + b2[0] + c2[0]) / 6.0,
                (a1[1] + b1[1] + c1[1] + a2[1] + b2[1] + c2[1]) / 6.0,
                (a1[2] + b1[2] + c1[2] + a2[2] + b2[2] + c2[2]) / 6.0,
            )
            return True, center

    # 2) 面与三角形相交（边穿过对面）-> 线段相交测试
    for p, q in ((a1, b1), (b1, c1), (c1, a1)):
        for r, s in ((a2, b2), (b2, c2), (c2, a2)):
            if _segments_intersect_3d(p, q, r, s, eps):
                mid = ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0, (p[2] + q[2]) / 2.0)
                return True, mid

    # 3) 边-面穿越：任一三角形的边真实穿过对方三角形（可能穿过内部而
    #    不与对方任何边相交，因此边-边测试不足以覆盖）。
    for p, q in ((a1, b1), (b1, c1), (c1, a1)):
        if _segment_triangle_intersect(p, q, a2, b2, c2, eps):
            return True, ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0,
                          (p[2] + q[2]) / 2.0)
    for p, q in ((a2, b2), (b2, c2), (c2, a2)):
        if _segment_triangle_intersect(p, q, a1, b1, c1, eps):
            return True, ((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0,
                          (p[2] + q[2]) / 2.0)

    return False, (0.0, 0.0, 0.0)


def _segment_triangle_intersect(p, q, a, b, c, eps=1e-9):
    """
    线段 pq 是否穿过三角形 abc（Möller–Trumbore 射线-三角形求交，
    射线参数限制 t∈[0,1]）。

    这比"两个三角形各自跨越对方平面"严格得多：后者只检查平面两侧分布，
    不检查交线区间是否重叠，会把空间上分离的两三角形误判为相交。
    共面退化情形由调用方的 2D SAT 分支处理，此处只处理真实穿越。
    """
    d = _sub(q, p)
    e1 = _sub(b, a)
    e2 = _sub(c, a)
    h = _cross(d, e2)
    det = _dot(e1, h)
    # 用相对尺度阈值判断"平行"：绝对阈值 1e-12 在微米级模型上会把
    # 真实穿越误判为平行而漏检。标量三重积的量级为 |e1|·|d|·|e2|。
    mag = (_dot(e1, e1) * _dot(d, d) * _dot(e2, e2)) ** 0.5
    if mag < 1e-30 or abs(det) < 1e-12 * mag:  # 退化或平行（含共面）
        return False
    inv_det = 1.0 / det
    s = _sub(p, a)
    u = _dot(s, h) * inv_det
    if u < -eps or u > 1.0 + eps:
        return False
    qv = _cross(s, e1)
    v = _dot(d, qv) * inv_det
    if v < -eps or u + v > 1.0 + eps:
        return False
    t = _dot(e2, qv) * inv_det
    return -eps <= t <= 1.0 + eps


def _coplanar_triangles_intersect(t1, t2, normal, eps=1e-9) -> bool:
    """共面三角形相交：在投影到主轴平面后做 2D SAT。"""
    ax = max(range(3), key=lambda i: abs(normal[i]))
    u = (ax + 1) % 3
    v = (ax + 2) % 3
    p1 = [(tri[u], tri[v]) for tri in t1]
    p2 = [(tri[u], tri[v]) for tri in t2]
    return _sat_2d(p1, p2, eps)


def _sat_2d(poly1, poly2, eps=1e-9) -> bool:
    """二维凸多边形分离轴定理。"""
    for poly in (poly1, poly2):
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            nx, ny = -(y2 - y1), (x2 - x1)
            proj1 = [nx * x + ny * y for x, y in poly1]
            proj2 = [nx * x + ny * y for x, y in poly2]
            if max(proj1) < min(proj2) - eps or max(proj2) < min(proj1) - eps:
                return False
    return True


# ---------------------------------------------------------------------------
# 包含关系检测（一个网格完全处于另一个网格内部）
# ---------------------------------------------------------------------------

def point_in_mesh(point, st: SubToolMesh, ray_dir=(0.5773502691896257,
                                                   0.5773502691896257,
                                                   0.5773502691896257)) -> bool:
    """
    射线奇偶法判断点是否位于封闭网格内部。

    从 point 沿 ray_dir 发出射线，统计与网格三角面的交点数：
    奇数次 = 内部，偶数次 = 外部。射线方向取体对角线方向以避免
    恰好命中顶点/边的退化情况。

    注意：该方法要求网格近似封闭。手办分件若存在开口，结果可能不准，
    但它作为"面面相交"之外的补充判定，能有效捕获"完全穿入"的情形。
    """
    _prepare_triangles(st)
    px, py, pz = point
    dx, dy, dz = ray_dir
    inside = False
    verts = st.vertices
    for (i, j, k) in st.faces:
        a, b, c = verts[i], verts[j], verts[k]
        # Möller–Trumbore 射线-三角形求交
        e1 = _sub(b, a)
        e2 = _sub(c, a)
        pvec = _cross((dx, dy, dz), e2)
        det = _dot(e1, pvec)
        if abs(det) < 1e-12:
            continue
        inv_det = 1.0 / det
        tvec = (px - a[0], py - a[1], pz - a[2])
        u = _dot(tvec, pvec) * inv_det
        if u < 0.0 or u > 1.0:
            continue
        qvec = _cross(tvec, e1)
        v = _dot((dx, dy, dz), qvec) * inv_det
        if v < 0.0 or u + v > 1.0:
            continue
        t = _dot(e2, qvec) * inv_det
        if t > 1e-9:
            inside = not inside
    return inside


def containment_hit(st_container: SubToolMesh, st_probe: SubToolMesh):
    """
    判断 st_probe 是否整体位于 st_container 内部，返回代表顶点坐标或 None。
    取探针网格中若干代表性顶点（含质心近似点）做射线测试，任一在内即判定包含。
    """
    verts = st_probe.vertices
    if not verts:
        return None
    n = len(verts)
    # 采样点：质心 + 若干分布顶点，兼顾效率与鲁棒性
    cx = sum(v[0] for v in verts) / n
    cy = sum(v[1] for v in verts) / n
    cz = sum(v[2] for v in verts) / n
    sample_indices = set()
    step = max(1, n // 16)
    for idx in range(0, n, step):
        sample_indices.add(idx)
    points = [(cx, cy, cz)] + [verts[i] for i in sorted(sample_indices)]

    for p in points:
        if point_in_mesh(p, st_container):
            return p
    return None


# ---------------------------------------------------------------------------
# AABB 与宽相
# ---------------------------------------------------------------------------

def mesh_aabb(verts) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    if _HAS_NUMPY:
        arr = np.asarray(verts, dtype=np.float64)
        lo = tuple(float(x) for x in arr.min(axis=0))
        hi = tuple(float(x) for x in arr.max(axis=0))
        return lo, hi
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for v in verts:
        for i in range(3):
            if v[i] < lo[i]:
                lo[i] = v[i]
            if v[i] > hi[i]:
                hi[i] = v[i]
    return tuple(lo), tuple(hi)


def cached_aabb(st: SubToolMesh):
    """缓存网格 AABB：两两检测时每个网格只需计算一次，而非每对重复计算。"""
    if getattr(st, "_aabb", None) is None:
        st._aabb = mesh_aabb(st.vertices)
    return st._aabb


def aabb_overlap(a_lo, a_hi, b_lo, b_hi, eps=1e-7) -> bool:
    return (
        a_lo[0] <= b_hi[0] + eps and a_hi[0] >= b_lo[0] - eps
        and a_lo[1] <= b_hi[1] + eps and a_hi[1] >= b_lo[1] - eps
        and a_lo[2] <= b_hi[2] + eps and a_hi[2] >= b_lo[2] - eps
    )


# ---------------------------------------------------------------------------
# 窄相检测（含三角面级 AABB 预筛）
# ---------------------------------------------------------------------------

def _prepare_triangles(st: SubToolMesh):
    """预计算每个三角面的顶点与 AABB，供窄相快速剪枝（numpy 向量化加速）。"""
    if getattr(st, "_tri_a", None) is not None:
        return
    if _HAS_NUMPY:
        V = np.asarray(st.vertices, dtype=np.float64)
        F = np.asarray(st.faces, dtype=np.int64)
        # 每个三角面的三个顶点坐标：形状 (面数, 3)
        tri_a = V[F[:, 0]]
        tri_b = V[F[:, 1]]
        tri_c = V[F[:, 2]]
        stacked = np.stack([tri_a, tri_b, tri_c], axis=1)  # (面数,3,3)
        tri_min = stacked.min(axis=1)
        tri_max = stacked.max(axis=1)
        st._tri_a, st._tri_b, st._tri_c = tri_a, tri_b, tri_c
        st._tri_min, st._tri_max = tri_min, tri_max
        return
    verts = st.vertices
    tris_a, tris_b, tris_c = [], [], []
    tris_min, tris_max = [], []
    for (i, j, k) in st.faces:
        a, b, c = verts[i], verts[j], verts[k]
        tris_a.append(a)
        tris_b.append(b)
        tris_c.append(c)
        lo = (min(a[0], b[0], c[0]), min(a[1], b[1], c[1]), min(a[2], b[2], c[2]))
        hi = (max(a[0], b[0], c[0]), max(a[1], b[1], c[1]), max(a[2], b[2], c[2]))
        tris_min.append(lo)
        tris_max.append(hi)
    st._tri_a, st._tri_b, st._tri_c = tris_a, tris_b, tris_c
    st._tri_min, st._tri_max = tris_min, tris_max


def broad_phase_triangle_candidates(st_a: SubToolMesh, st_b: SubToolMesh):
    """
    在两网格的 AABB 相交区域上，做三角面级候选筛选。
    numpy 版本用广播向量化求交，得到候选三角面对索引；
    纯 Python 版本退化为逐面对比（仅在小模型时可用）。
    """
    _prepare_triangles(st_a)
    _prepare_triangles(st_b)

    if _HAS_NUMPY:
        amin = np.asarray(st_a._tri_min, dtype=np.float64)
        amax = np.asarray(st_a._tri_max, dtype=np.float64)
        bmin = np.asarray(st_b._tri_min, dtype=np.float64)
        bmax = np.asarray(st_b._tri_max, dtype=np.float64)
        # 分块处理，避免 O(n*m) 内存爆炸
        CHUNK = 256
        for start in range(0, len(amin), CHUNK):
            end = min(start + CHUNK, len(amin))
            a0 = amin[start:end, None, :]
            a1 = amax[start:end, None, :]
            overlap = (
                (a0 <= bmax[None, :, :]) & (a1 >= bmin[None, :, :])
            ).all(axis=2)
            rows, cols = np.nonzero(overlap)
            for r, c in zip(rows, cols):
                yield int(start + r), int(c)
    else:
        for ia in range(len(st_a._tri_min)):
            for ib in range(len(st_b._tri_min)):
                if aabb_overlap(st_a._tri_min[ia], st_a._tri_max[ia],
                                st_b._tri_min[ib], st_b._tri_max[ib]):
                    yield ia, ib


def detect_pair(st_a: SubToolMesh, st_b: SubToolMesh,
                mode: str = "first", max_hits: int = 2000) -> PairResult:
    """精确检测一对 SubTool 是否相交。

    mode="first"（快速）：找到第一处相交即返回，性能最优。
    mode="all"（完整）：收集全部相交点，受 max_hits 上限保护，
                       超出时 hits_truncated=True 且 total_hits 记录实际总数。
    """
    result = PairResult(name_a=st_a.name, name_b=st_b.name, intersecting=False)
    collect_all = (mode == "all")

    a_lo, a_hi = cached_aabb(st_a)
    b_lo, b_hi = cached_aabb(st_b)
    if not aabb_overlap(a_lo, a_hi, b_lo, b_hi):
        return result

    tried = 0
    for ia, ib in broad_phase_triangle_candidates(st_a, st_b):
        tried += 1
        ta = (st_a._tri_a[ia], st_a._tri_b[ia], st_a._tri_c[ia])
        tb = (st_b._tri_a[ib], st_b._tri_b[ib], st_b._tri_c[ib])
        hit, point = triangles_intersect(ta, tb)
        if hit:
            info = HitInfo(
                subtool_a=st_a.name, subtool_b=st_b.name,
                face_a=ia, face_b=ib, contact_point=tuple(float(x) for x in point),
            )
            if collect_all:
                result.total_hits += 1
                if len(result.all_hits) < max_hits:
                    result.all_hits.append(info)
                else:
                    result.hits_truncated = True
            else:
                result.hit = info
                result.total_hits = 1
                result.intersecting = True
                result.triangles_tested = tried
                return result  # 快速模式：命中即早退

    # 完整模式下若已有面面相交点，设置代表点后继续做包含检测，
    # 以便把"包含"这类相交也纳入 all_hits（此前提前 return 会漏掉）。
    if collect_all and result.all_hits:
        result.intersecting = True
        result.hit = result.all_hits[0]
    # 快速模式命中即已在循环内返回；若走到这里说明无面面相交，
    # 进入包含检测。

    # 判定包含关系（一个整体穿入另一个内部，无面面交叉）
    # 仅当 A 的包围盒完全落在 B 内（或反之）时才做射线测试，避免无谓开销
    try:
        # AABB 已在函数开头取得，直接复用（不再重复遍历顶点）
        def bbox_contains(lo1, hi1, lo2, hi2, eps=1e-6):
            return all(lo2[i] - eps <= lo1[i] and hi1[i] <= hi2[i] + eps
                       for i in range(3))

        contain_info = None
        if bbox_contains(a_lo, a_hi, b_lo, b_hi):
            p = containment_hit(st_b, st_a)
            if p is not None:
                contain_info = HitInfo(st_a.name, st_b.name, -1, -1,
                                       tuple(float(x) for x in p))
        if contain_info is None and bbox_contains(b_lo, b_hi, a_lo, a_hi):
            p = containment_hit(st_a, st_b)
            if p is not None:
                contain_info = HitInfo(st_a.name, st_b.name, -1, -1,
                                       tuple(float(x) for x in p))
        if contain_info is not None:
            result.intersecting = True
            if result.hit is None:
                result.hit = contain_info  # 无面面相交时，用包含点作代表
            result.total_hits += 1
            if collect_all:
                if len(result.all_hits) < max_hits:
                    result.all_hits.append(contain_info)
                else:
                    result.hits_truncated = True
    except Exception:
        # 包含判定失败不应影响主流程，保持"未发现面面相交"的结论
        pass

    result.triangles_tested = tried
    return result


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def run_detection(obj_path: str, zbrush_version: str = T("未知", "unknown"),
                  progress_callback=None, mode: str = "first") -> Report:
    """对 OBJ 中所有 SubTool 两两检测，返回汇总报告。

    mode="first" 快速模式（默认）：每对只记录第一处相交点。
    mode="all" 完整模式：记录所有相交点（每对最多 MAX_HITS_PER_PAIR 处）。
    """
    started = time.time()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if mode not in ("first", "all"):
        mode = "first"
    report = Report(obj_path=obj_path, timestamp=stamp,
                    zbrush_version=zbrush_version, mode=mode,
                    has_numpy=_HAS_NUMPY)

    # 优先使用"逐个导出"模式（sub_0.obj, sub_1.obj ...），这是唯一能
    # 可靠区分 SubTool 的方式（整体导出的 g GroupNNN 只是 Polygroup）。
    work_dir = os.path.dirname(os.path.abspath(obj_path))
    manifest = os.path.join(work_dir, "subtools_names.txt")
    multi_subs, multi_warn = parse_obj_multi(work_dir, "sub_", manifest)
    if len(multi_subs) >= 2:
        subtools, warnings = multi_subs, multi_warn
        report.obj_path = work_dir + T(" (逐个导出模式，%d 个子工具)", " (per-SubTool export, %d SubTools)") % len(multi_subs)
    else:
        # 边界：目录里确有 sub_*.obj 但有效数不足（导出失败/count 写错/残留），
        # 此时静默回退会把某个残片当唯一子工具，得出误导性的"无相交"。
        import glob as _glob
        sub_files = _glob.glob(os.path.join(work_dir, "sub_*.obj"))
        fallback = parse_obj(obj_path)
        if sub_files:
            subtools, warnings = fallback
            warnings.insert(0, (
                T("注意：发现 %d 个 sub 导出文件，但仅 %d 个有效（应为连续编号），导出可能不完整（缺失或失败）；本次结果不可信，请重新检测。", "Note: found %d sub export files but only %d are valid (they should be numbered consecutively). The export may be incomplete (missing or failed); this result is unreliable, please check again.") % (
                    len(sub_files), len(multi_subs))))
        else:
            subtools, warnings = fallback

    report.warnings = warnings
    report.subtools = [st.name for st in subtools]

    # 关键防护：若只解析出 1 个"未命名"子工具，极可能是 ZBrush 把所有
    # SubTool 合并导出到一个 group，此时两两检测会失效，必须显式警告，
    # 避免用户误以为"没有相交"而直接去打印。
    if len(subtools) == 1 and subtools[0].name.startswith(T("未命名子工具", "Unnamed SubTool")):
        report.warnings.append(
            T("注意：OBJ 中未检测到子工具分组，可能被合并导出为单个对象，此时无法区分各个子工具。请在 ZBrush 导出前确认按 SubTool 分组，或改用逐个导出。本次结果不能作为无相交的依据。", "Note: no SubTool groups found in the OBJ; it may have been exported merged into one object, so SubTools cannot be told apart. Group by SubTool before exporting, or export one by one. This result is not proof that nothing intersects.")
        )

    total = len(subtools)
    pair_total = total * (total - 1) // 2
    done = 0
    for i in range(total):
        for j in range(i + 1, total):
            pair = detect_pair(subtools[i], subtools[j], mode=mode)
            report.pairs.append(pair)
            done += 1
            if progress_callback:
                progress_callback(done, pair_total, pair)

    report.elapsed_sec = time.time() - started
    return report


# ---------------------------------------------------------------------------
# 中文报告输出
# ---------------------------------------------------------------------------

def format_summary(report: Report) -> str:
    """
    单行中文摘要（供 result.txt 与结果窗口标题复用，保证措辞一致）。

    强制单行且无制表符：ZBrush 侧若需要读回，MemReadString 只取一行。
    导出不完整时把"不可信"置于开头，避免用户只看首屏误判为安全。
    """
    hits = report.intersecting_pairs
    # 同时认中英文措辞（英文界面下警告是英文）
    incomplete = any(("缺失" in w) or ("没有有效面" in w) or ("合并导出" in w)
                     or ("不可信" in w)
                     or ("missing" in w) or ("no valid faces" in w)
                     or ("merged into one object" in w) or ("unreliable" in w)
                     for w in report.warnings)
    if incomplete:
        text = T("【重要】部分子工具导出不完整，本次结果不可信！详情见 完整报告.txt", "[IMPORTANT] Some SubTools were not exported completely, so this result is unreliable! See report.txt for details.")
    elif hits:
        pairs = [T("「%s」与「%s」", '"%s" and "%s"') % (p.name_a, p.name_b) for p in hits]
        detail = T("；", "; ").join(pairs)
        if len(detail) > 240:
            detail = detail[:240] + "…"
        text = T("【检测结果】发现 %d 处相交：%s。详情见 完整报告.txt", "[Result] Found %d intersection(s): %s. See report.txt for details.") % (len(hits), detail)
    else:
        text = (T("【检测结果】未发现任何相交，所有 %d 个子工具彼此分离。详情见 完整报告.txt", "[Result] No intersections found; all %d SubTools are separate. See report.txt for details.") % len(report.subtools))
    return text.replace("\n", " ").replace("\r", " ").replace("\t", " ")


def format_text_report(report: Report) -> str:
    lines: List[str] = []
    lines.append("=" * 56)
    lines.append(T("ZBrush 分件碰撞检测报告", "ZBrush SubTool Collision Check Report"))
    lines.append("=" * 56)
    lines.append(T("检测时间：%s", "Checked at: %s") % report.timestamp)
    lines.append(T("ZBrush 版本：%s", "ZBrush version: %s") % report.zbrush_version)
    lines.append(T("输入文件：%s", "Input: %s") % report.obj_path)
    lines.append(T("子工具数量：%d", "SubTools: %d") % len(report.subtools))
    lines.append(T("检测配对数：%d", "Pairs checked: %d") % report.pair_count)
    lines.append(T("耗时：%.2f 秒", "Time: %.2f s") % report.elapsed_sec)
    lines.append(T("检测模式：%s", "Mode: %s") % (T("完整模式（列出全部相交点）", "Full (all intersection points)")
                                  if report.mode == "all" else T("快速模式（每对仅首个相交点）", "Quick (first intersection point per pair)")))
    lines.append(T("计算后端：%s", "Backend: %s") % (T("NumPy 加速", "NumPy accelerated") if report.has_numpy else T("纯 Python", "pure Python")))
    lines.append("-" * 56)
    lines.append(T("子工具清单：", "SubTools:"))
    for idx, name in enumerate(report.subtools, 1):
        lines.append("  %d. %s" % (idx, name))
    lines.append("-" * 56)

    hits = report.intersecting_pairs
    if hits:
        lines.append(T("【检测结果】发现 %d 对子工具相交：", "[Result] %d SubTool pair(s) intersect:") % len(hits))
        for n, pair in enumerate(hits, 1):
            lines.append(T("  %d. 「%s」  <->  「%s」", '  %d. "%s"  <->  "%s"') % (
                n, pair.name_a, pair.name_b))
            if report.mode == "all" and pair.all_hits:
                # 完整模式：列出该对的全部相交点（含上限截断说明）
                lines.append(T("     相交点数：%d%s", "     Intersection points: %d%s") % (
                    pair.total_hits, T("（列表已截断）", " (list truncated)") if pair.hits_truncated else ""))
                for k, info in enumerate(pair.all_hits, 1):
                    px, py, pz = info.contact_point
                    lines.append(T("       #%d 面#%d×#%d 坐标(%.4f, %.4f, %.4f)", "       #%d faces #%d x #%d at (%.4f, %.4f, %.4f)") % (
                        k, info.face_a, info.face_b, px, py, pz))
            elif pair.hit:
                px, py, pz = pair.hit.contact_point
                lines.append(T("     相交面：#%d 与 #%d；参考坐标：(%.4f, %.4f, %.4f)", "     Faces #%d and #%d; reference point: (%.4f, %.4f, %.4f)") % (
                    pair.hit.face_a, pair.hit.face_b, px, py, pz))
    else:
        lines.append(T("【检测结果】未发现任何相交。所有子工具彼此分离。", "[Result] No intersections found. All SubTools are separate."))

    if report.warnings:
        lines.append("-" * 56)
        lines.append(T("提示：", "Notes:"))
        for w in report.warnings:
            lines.append("  - %s" % w)

    lines.append("=" * 56)
    return "\n".join(lines)


def report_to_dict(report: Report) -> dict:
    return {
        "检测时间": report.timestamp,
        "ZBrush版本": report.zbrush_version,
        "输入文件": report.obj_path,
        "子工具数量": len(report.subtools),
        "检测配对数": report.pair_count,
        "耗时秒": round(report.elapsed_sec, 3),
        "检测模式": report.mode,
        "计算后端": "NumPy加速" if report.has_numpy else "纯Python",
        "子工具清单": report.subtools,
        "相交对": [
            {
                "子工具A": p.name_a,
                "子工具B": p.name_b,
                "相交点数": p.total_hits,
                "列表已截断": p.hits_truncated,
                "相交面A": p.hit.face_a if p.hit else None,
                "相交面B": p.hit.face_b if p.hit else None,
                "参考坐标": list(p.hit.contact_point) if p.hit else None,
                "全部相交点": (
                    [
                        {"面A": h.face_a, "面B": h.face_b,
                         "坐标": list(h.contact_point)}
                        for h in p.all_hits
                    ] if report.mode == "all" else []
                ),
            }
            for p in report.intersecting_pairs
        ],
        "提示": report.warnings,
    }


def append_history(report: Report, history_path: str):
    """把本次结果追加到持久化记录文件（中文，带时间戳）。"""
    os.makedirs(os.path.dirname(os.path.abspath(history_path)), exist_ok=True)
    hits = report.intersecting_pairs
    if hits:
        summary = T("；", "; ").join(T("「%s」×「%s」", '"%s" x "%s"') % (p.name_a, p.name_b) for p in hits)
        verdict = T("发现 %d 处相交：%s", "%d intersection(s): %s") % (len(hits), summary)
    else:
        verdict = T("未发现相交", "no intersections")
    entry = (
        T("[%s] 版本=%s | 子工具=%d | 配对=%d | %s | 耗时=%.2fs | 文件=%s\n", "[%s] version=%s | SubTools=%d | pairs=%d | %s | time=%.2fs | file=%s\n")
        % (
            report.timestamp, report.zbrush_version, len(report.subtools),
            report.pair_count, verdict, report.elapsed_sec,
            os.path.basename(report.obj_path),
        )
    )
    is_new = not os.path.exists(history_path)
    with open(history_path, "a", encoding="utf-8") as fh:
        if is_new:
            fh.write(T("ZBrush 分件碰撞检测记录（每次检测追加一行）\n", "ZBrush SubTool collision check log (one line per check)\n"))
            fh.write("=" * 56 + "\n")
        fh.write(entry)


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="ZBrush 分件碰撞检测引擎")
    parser.add_argument("--obj", required=True, help="ZBrush 导出的 OBJ 文件路径")
    parser.add_argument("--out-dir", default=None, help="输出目录（默认与 OBJ 同目录）")
    parser.add_argument("--json", default=None, help="结果 JSON 路径")
    parser.add_argument("--version", default=T("未知", "unknown"), help="ZBrush 版本标识")
    parser.add_argument("--history", default=None, help="检测记录文件路径")
    parser.add_argument("--mode", choices=("first", "all"), default="first",
                        help="first=快速(每对首个相交点)；all=完整(列出全部)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.obj):
        print(T("错误：找不到 OBJ 文件：%s", "Error: OBJ file not found: %s") % args.obj, file=sys.stderr)
        return 2

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.obj))
    os.makedirs(out_dir, exist_ok=True)

    def progress(done, total, pair):
        if total and (done % max(1, total // 20) == 0 or done == total):
            print(T("检测进度：%d/%d", "Progress: %d/%d") % (done, total))

    report = run_detection(args.obj, zbrush_version=args.version,
                           progress_callback=progress, mode=args.mode)

    # 文本报告
    report_path = os.path.join(out_dir, T("碰撞检测结果.txt", "collision_result.txt"))
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(format_text_report(report))

    # JSON 结果
    json_path = args.json or os.path.join(out_dir, T("碰撞检测结果.json", "collision_result.json"))
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report_to_dict(report), fh, ensure_ascii=False, indent=2)

    # 持久化记录
    history_path = args.history or os.path.join(out_dir, history_name())
    append_history(report, history_path)

    print(format_text_report(report))
    print(T("\n[完成] 结果文件：%s", "\n[Done] Report: %s") % report_path)
    print(T("[完成] 结果数据：%s", "[Done] Data: %s") % json_path)
    print(T("[完成] 检测记录：%s", "[Done] Log: %s") % history_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
