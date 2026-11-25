# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 带中文结果窗口的主入口（由 bat 用 pythonw 启动）

流程：
  1. 读取工作目录下 sub_*.obj（逐个导出的子工具，数量由 count.txt 界定）
  2. 调用 collision_engine 检测
  3. 写 result.txt（单行摘要）、完整报告.txt、result.json、检测记录.txt
  4. 弹出中文结果窗口（tkinter 渲染，无编码风险）
  5. 写 done.flag 作为完成标记（保留给外部工具/自检使用；
     ZBrush 宏本身不轮询，弹窗由本程序直接完成）

用 pythonw 启动，无控制台黑框；未捕获异常会写入 error.txt 以便排查。
"""

import os
import sys
import json
import traceback

if getattr(sys, "frozen", False):
    # PyInstaller 打包版：exe 位于 <工作目录>/bin/，数据文件在上一层
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    if BASE not in sys.path:
        sys.path.insert(0, BASE)

import i18n
i18n.load(BASE)
from i18n import T, report_name, history_name
import collision_engine as ce
import result_window

DONE_FLAG = os.path.join(BASE, "done.flag")


def write_done():
    try:
        with open(DONE_FLAG, "w", encoding="ascii") as fh:
            fh.write("done")
    except Exception:
        pass


def build_summary(report):
    """单行中文摘要，统一由引擎生成以保证措辞一致。"""
    return ce.format_summary(report)


def read_mode():
    """从工作目录 mode.txt 读取检测模式（first/all），默认 first。"""
    path = os.path.join(BASE, "mode.txt")
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as fh:
            value = fh.read().strip().lower()
        return value if value in ("first", "all") else "first"
    except Exception:
        return "first"


def read_version():
    try:
        with open(os.path.join(BASE, "version.txt"), "r", encoding="utf-8") as fh:
            return fh.read().strip() or T("未知", "unknown")
    except Exception:
        return T("未知", "unknown")


DETACH_FLAG = "--detached"


def _relaunch_detached() -> bool:
    """以脱离父进程的方式重新启动自身；成功返回 True（调用方应立即退出）。"""
    if os.name != "nt":
        return False
    import subprocess
    if getattr(sys, "frozen", False):
        args = [sys.executable, DETACH_FLAG]
    else:
        exe = sys.executable
        # 优先用 pythonw，避免分离后的子进程弹控制台
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(cand):
            exe = cand
        args = [exe, os.path.abspath(__file__), DETACH_FLAG]
    flags = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(args, cwd=BASE, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False   # 分离失败就在当前进程继续跑（最差情况同旧行为）


def main():
    # 关键：ZBrush 的 [ShellExecute] 实际执行 "cmd /c <程序>" 并【阻塞等待其退出】。
    # 若本进程一直开着结果窗口，ZBrush 会卡死并被 Windows 判定为无响应关闭
    # （实测事件日志 AppHangXProcB1，ZBrush 等待 cmd.exe）。
    # 因此首次启动时立即以分离进程重新拉起自己并马上退出，让 ZBrush 立刻返回。
    if DETACH_FLAG not in sys.argv and _relaunch_detached():
        return
    # pythonw 无控制台：任何未捕获异常都会静默丢失，必须落盘以便排查
    try:
        _run()
    except Exception as exc:
        try:
            with open(os.path.join(BASE, "error.txt"), "w", encoding="utf-8") as fh:
                fh.write(T("检测入口异常：%s\n\n", "Startup error: %s\n\n") % exc)
                fh.write(traceback.format_exc())
        except Exception:
            pass
        write_done()
        try:
            import tkinter.messagebox as mb
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            mb.showerror(T("检测异常", "Check error"), T("检测过程中出现异常：%s\n详情见 error.txt", "An error occurred during the check: %s\nSee error.txt for details.") % exc)
            root.destroy()
        except Exception:
            pass


def _run():
    # 清理旧标记
    for p in (DONE_FLAG, os.path.join(BASE, "result.txt")):
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    report = None
    error_text = None
    try:
        import glob, re
        subs = glob.glob(os.path.join(BASE, "sub_*.obj"))
        # 按序号排序；具体只取本次导出的 N 个由引擎读取 count.txt 处理，
        # 这样即使上次运行留下更高序号的残留文件也不会混入本次检测。
        def idx_of(p):
            m = re.findall(r"(\d+)", os.path.basename(p))
            return int(m[-1]) if m else 999999
        subs.sort(key=idx_of)
        if not subs:
            error_text = T("未找到本次导出的子工具 OBJ 文件（sub_0.obj 等）。", "The exported SubTool OBJ files (sub_0.obj, ...) were not found.")
        else:
            report = ce.run_detection(subs[0], zbrush_version=read_version(),
                                      mode=read_mode())
    except Exception as exc:
        error_text = T("检测出错：%s", "Check error: %s") % exc

    if report is not None:
        with open(os.path.join(BASE, "result.txt"), "w", encoding="utf-8") as fh:
            fh.write(build_summary(report))
        with open(os.path.join(BASE, report_name()), "w", encoding="utf-8") as fh:
            fh.write(ce.format_text_report(report))
        with open(os.path.join(BASE, "result.json"), "w", encoding="utf-8") as fh:
            json.dump(ce.report_to_dict(report), fh, ensure_ascii=False, indent=2)
        try:
            ce.append_history(report, os.path.join(BASE, history_name()))
        except Exception:
            pass
    else:
        with open(os.path.join(BASE, "result.txt"), "w", encoding="utf-8") as fh:
            fh.write(error_text or T("检测失败。", "Check failed."))

    # 先写完成标记，再弹窗（ZBrush 轮询到标记即可继续，不必等窗口关闭）
    write_done()

    if report is not None:
        result_window.show_result_window(report, ce.format_text_report(report))
    else:
        # 错误也用中文窗口显示
        import tkinter.messagebox as mb
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        mb.showerror(T("检测失败", "Check failed"), error_text or T("检测失败，请查看工作目录的 result.txt", "Check failed. See result.txt in the working directory."))
        root.destroy()


if __name__ == "__main__":
    main()
