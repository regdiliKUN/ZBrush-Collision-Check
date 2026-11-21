# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 一键启动器（中文图形界面）
==================================================

由 ZBrush 宏导出 OBJ 后调用。职责：
  1. 等待导出文件写入完成
  2. 调用 collision_engine 执行检测
  3. 用中文窗口显示相交子工具对
  4. 检测记录由引擎写入持久化文件

用法：
    pythonw launcher.pyw                       # 自动使用 导出数据/当前检测.obj
    pythonw launcher.pyw --obj "路径.obj"       # 指定 OBJ
    pythonw launcher.pyw --wait 3               # 自定义等待秒数
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext

BASE = os.path.dirname(os.path.abspath(__file__))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import collision_engine as ce

EXPORT_DIR = os.path.join(BASE, "导出数据")
DEFAULT_OBJ = os.path.join(EXPORT_DIR, "当前检测.obj")
HISTORY = os.path.join(BASE, "碰撞检测记录.txt")


def wait_for_stable_file(path: str, timeout: float = 20.0, stable: float = 0.8) -> bool:
    """等待文件出现且大小稳定（确认 ZBrush 已写完导出）。"""
    deadline = time.time() + timeout
    last_size, last_change = -1, time.time()
    while time.time() < deadline:
        if os.path.isfile(path):
            size = os.path.getsize(path)
            if size != last_size:
                last_size, last_change = size, time.time()
            elif time.time() - last_change >= stable and size > 0:
                return True
        time.sleep(0.15)
    return os.path.isfile(path) and os.path.getsize(path) > 0


def show_result_window(report: ce.Report):
    """用中文窗口展示检测报告。"""
    root = tk.Tk()
    root.title("分件碰撞检测结果")
    root.geometry("760x560")
    root.minsize(640, 440)

    # 中文字体（Windows）
    font_normal = ("Microsoft YaHei", 10)
    font_bold = ("Microsoft YaHei", 11, "bold")

    hits = report.intersecting_pairs
    header = tk.Frame(root, padx=16, pady=12)
    header.pack(fill="x")

    if hits:
        title = "⚠ 发现 %d 处子工具相交" % len(hits)
        color = "#C0392B"
    else:
        title = "✓ 未发现任何相交"
        color = "#1E8449"
    tk.Label(header, text=title, font=("Microsoft YaHei", 15, "bold"),
             fg=color).pack(anchor="w")

    meta = ("检测时间：%s    子工具：%d 个    配对：%d 组    耗时：%.2f 秒"
            % (report.timestamp, len(report.subtools), report.pair_count,
               report.elapsed_sec))
    tk.Label(header, text=meta, font=font_normal, fg="#555555").pack(anchor="w", pady=(6, 0))
    tk.Label(header, text="检测到相交的部件对：", font=font_bold).pack(anchor="w", pady=(14, 4))

    # 相交对列表
    list_frame = tk.Frame(root, padx=16)
    list_frame.pack(fill="both", expand=False)
    listbox = tk.Listbox(list_frame, height=min(10, max(4, len(hits) + 1)),
                         font=font_normal, activestyle="none")
    scroll = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.config(yscrollcommand=scroll.set)
    if hits:
        for n, p in enumerate(hits, 1):
            listbox.insert("end", "  %d.  「%s」   ×   「%s」" % (n, p.name_a, p.name_b))
    else:
        listbox.insert("end", "  所有子工具彼此分离，未发现穿透。")
    listbox.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    # 详细报告
    tk.Label(root, text="详细报告：", font=font_bold).pack(anchor="w", padx=16, pady=(14, 4))
    txt = scrolledtext.ScrolledText(root, font=("Consolas", 9), wrap="word")
    txt.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    txt.insert("1.0", ce.format_text_report(report))
    txt.config(state="disabled")

    footer = tk.Frame(root, padx=16, pady=(0, 14))
    footer.pack(fill="x")
    tk.Label(footer, text="检测结果已保存到插件目录，并追加到检测记录文件。",
             font=font_normal, fg="#555555").pack(side="left")
    tk.Button(footer, text="关闭", font=font_normal,
              command=root.destroy, width=10).pack(side="right")

    # 置顶提醒
    root.lift()
    root.attributes("-topmost", True)
    root.after(1200, lambda: root.attributes("-topmost", False))
    root.mainloop()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    obj_path = DEFAULT_OBJ
    wait_sec = 12.0
    version = "自动"

    if "--obj" in argv:
        i = argv.index("--obj")
        if i + 1 < len(argv):
            obj_path = argv[i + 1]
    if "--wait" in argv:
        i = argv.index("--wait")
        if i + 1 < len(argv):
            try:
                wait_sec = float(argv[i + 1])
            except ValueError:
                pass
    if "--version" in argv:
        i = argv.index("--version")
        if i + 1 < len(argv):
            version = argv[i + 1]

    # 等待导出文件就绪
    if not wait_for_stable_file(obj_path, timeout=max(wait_sec, 3.0)):
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "检测失败",
            "未找到可用的导出文件：\n%s\n\n请确认已在 ZBrush 中进入 Edit 模式并成功导出 OBJ。" % obj_path,
        )
        return 2

    try:
        report = ce.run_detection(obj_path, zbrush_version=version)
    except Exception as exc:  # 显示中文错误，不暴露英文堆栈
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("检测出错", "检测过程中出现错误：\n%s" % exc)
        return 3

    # 保存结果 + 追加记录
    os.makedirs(EXPORT_DIR, exist_ok=True)
    with open(os.path.join(EXPORT_DIR, "碰撞检测结果.txt"), "w", encoding="utf-8") as fh:
        fh.write(ce.format_text_report(report))
    ce.append_history(report, HISTORY)

    show_result_window(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
