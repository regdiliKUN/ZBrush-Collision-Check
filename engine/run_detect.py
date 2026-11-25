# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 无界面批处理器（由 run_detect.bat 调用）

流程：
  1. 读取工作目录下的 subtools.obj（ZBrush 导出的全部子工具）
  2. 调用 collision_engine 做两两相交检测
  3. 把中文结果写入 result.txt（供 ZScript 读回并用 Note 弹窗）
  4. 写 done.flag 标记完成（ZScript 轮询此文件）
  5. 同时把检测记录追加到 碰撞检测记录.txt

全部面向用户的文本均为中文。
"""

import os
import sys
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import i18n
i18n.load(BASE)
from i18n import T, report_name, history_name
import collision_engine as ce

OBJ_PATH = os.path.join(BASE, "subtools.obj")
RESULT_PATH = os.path.join(BASE, "result.txt")       # 供 ZBrush 读回：仅一行紧凑中文摘要
FULLREPORT_PATH = os.path.join(BASE, report_name())  # 完整中文报告
DONE_FLAG = os.path.join(BASE, "done.flag")
HISTORY_PATH = os.path.join(BASE, history_name())
VERSION_FILE = os.path.join(BASE, "version.txt")


def read_version() -> str:
    """读取版本标识文件（安装脚本写入，便于记录）。"""
    try:
        with open(VERSION_FILE, "r", encoding="utf-8") as fh:
            return fh.read().strip() or T("未知", "unknown")
    except Exception:
        return T("未知", "unknown")


def build_note_summary(report) -> str:
    """单行中文摘要，统一由引擎生成以保证措辞一致。"""
    return ce.format_summary(report)


def write_done():
    try:
        with open(DONE_FLAG, "w", encoding="ascii") as fh:
            fh.write("done")
    except Exception:
        pass


def main():
    # 清理旧标记
    for p in (RESULT_PATH, DONE_FLAG):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    import glob as _glob
    sub_files = _glob.glob(os.path.join(BASE, "sub_*.obj"))
    if not os.path.isfile(OBJ_PATH) and not sub_files:
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            fh.write(T("检测失败：未找到导出的 OBJ，请确认 ZBrush 已逐个子工具导出。", "Check failed: no exported OBJ found. Make sure ZBrush exported the SubTools one by one."))
        write_done()
        return 2

    # 逐个导出模式下 OBJ_PATH 可能不存在，传一个占位路径；
    # run_detection 会自动优先读取目录下的 sub_*.obj。
    detect_target = OBJ_PATH if os.path.isfile(OBJ_PATH) else (
        sub_files[0] if sub_files else OBJ_PATH)

    try:
        report = ce.run_detection(detect_target, zbrush_version=read_version())
        # result.txt 供 ZBrush 读回，必须只有一行摘要（MemReadString 读单行）
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            fh.write(build_note_summary(report))
        # 完整报告单独保存
        with open(FULLREPORT_PATH, "w", encoding="utf-8") as fh:
            fh.write(ce.format_text_report(report))
        ce.append_history(report, HISTORY_PATH)
        # 单独保存 JSON 结果，便于后续查证
        import json
        with open(os.path.join(BASE, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(ce.report_to_dict(report), fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        with open(RESULT_PATH, "w", encoding="utf-8") as fh:
            fh.write(T("检测出错：%s", "Check error: %s") % exc)
    finally:
        write_done()

    return 0


if __name__ == "__main__":
    sys.exit(main())
