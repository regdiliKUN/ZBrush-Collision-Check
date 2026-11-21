# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 中文结果窗口模块

由 detect_and_show.pyw 调用。用 tkinter 渲染中文结果，
避免 ZScript Note 弹窗从 UTF-8 文件读回中文可能乱码的风险。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext


def show_result_window(report, detail_text=None, on_close=None):
    """用中文窗口展示检测报告。detail_text 为详细报告文本（由调用方生成）。"""
    root = tk.Tk()
    root.title("分件碰撞检测结果")
    root.geometry("780x580")
    # 多显示器：窗口出现在鼠标附近（即刚点按钮的 ZBrush 所在屏幕），而非总在主屏
    try:
        px, py = root.winfo_pointerxy()
        root.geometry("780x580%+d%+d" % (px - 390, py - 120))
    except Exception:
        pass
    root.minsize(640, 440)

    font_normal = ("Microsoft YaHei", 10)
    font_bold = ("Microsoft YaHei", 11, "bold")

    hits = report.intersecting_pairs
    header = tk.Frame(root, padx=16, pady=12)
    header.pack(fill="x")

    if hits:
        title = "发现 %d 处子工具相交" % len(hits)
        color = "#C0392B"
    else:
        title = "未发现任何相交"
        color = "#1E8449"
    tk.Label(header, text=title, font=("Microsoft YaHei", 15, "bold"),
             fg=color).pack(anchor="w")

    meta = ("检测时间：%s    子工具：%d 个    配对：%d 组    耗时：%.2f 秒"
            % (report.timestamp, len(report.subtools), report.pair_count,
               report.elapsed_sec))
    tk.Label(header, text=meta, font=font_normal, fg="#555555").pack(anchor="w", pady=(6, 0))
    mode_all = getattr(report, "mode", "first") == "all"
    list_title = "检测到相交的部件对（%s）：" % (
        "完整模式，含全部相交点" if mode_all else "每对首个相交点")
    tk.Label(header, text=list_title, font=font_bold).pack(anchor="w", pady=(14, 4))

    list_frame = tk.Frame(root, padx=16)
    list_frame.pack(fill="both", expand=False)
    listbox = tk.Listbox(list_frame, height=min(10, max(4, len(hits))),
                         font=font_normal, activestyle="none")
    scroll = tk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.config(yscrollcommand=scroll.set)
    if hits:
        for n, p in enumerate(hits, 1):
            count_note = ("  [共 %d 处%s]" % (
                p.total_hits, "，列表截断" if getattr(p, "hits_truncated", False) else ""
            )) if mode_all else ""
            listbox.insert("end", "  %d.  「%s」 × 「%s」%s" % (
                n, p.name_a, p.name_b, count_note))
    else:
        listbox.insert("end", "  所有子工具彼此分离，未发现穿透。")
    listbox.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    tk.Label(root, text="详细报告：", font=font_bold).pack(anchor="w", padx=16, pady=(14, 4))
    txt = scrolledtext.ScrolledText(root, font=("Consolas", 9), wrap="word")
    txt.pack(fill="both", expand=True, padx=16, pady=(0, 8))
    txt.insert("1.0", detail_text or "详细报告见工作目录的 完整报告.txt")
    txt.config(state="disabled")

    def close():
        root.destroy()
        if on_close:
            on_close()

    footer = tk.Frame(root)
    footer.pack(fill="x", padx=16, pady=(0, 14))
    tk.Label(footer, text="结果已保存到工作目录，并追加到检测记录。",
             font=font_normal, fg="#555555").pack(side="left")
    tk.Button(footer, text="关闭", font=font_normal,
              command=close, width=10).pack(side="right")

    root.lift()
    root.attributes("-topmost", True)
    root.after(1200, lambda: root.attributes("-topmost", False))
    root.mainloop()
