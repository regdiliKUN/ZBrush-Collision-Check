# -*- coding: utf-8 -*-
"""
ZBrush 分件碰撞检测 —— 一键安装程序
====================================

作用：
  1. 把 Python 检测引擎安装到无空格工作目录 C:\\ZBCollision\\
  2. 把中文 ZScript 宏安装到两个 ZBrush 版本的 Macros 目录
  3. 写入版本标识，生成开始菜单/桌面快捷方式（可选）

兼容：ZBrush 2022 与 2025（以及其它含 ZStartup\\Macros 的版本）
用法：
    python install.py
    python install.py --workdir D:\\ZBCollision
    python install.py --zbrush-path "D:\\Program Files\\ZBrush 2025"
    python install.py --zbrush-path "..." --zbrush-path "..."   (可指定多个)
    python install.py --config install_config.json

ZBrush 安装目录发现优先级：
    1. --zbrush-path 命令行参数
    2. 配置文件 zbrush_paths
    3. Windows 注册表（Pixologic / Maxon）
    4. 常见安装根目录扫描
    5. 环境变量 ZBRUSH_PATH
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import sys

if getattr(sys, "frozen", False):
    # 打包成 安装程序.exe 时，exe 放在工程/发布包根目录
    BASE = os.path.dirname(os.path.abspath(sys.executable))
else:
    # install.py 位于 installer/ 子目录，工程根目录在上一层
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_SRC = os.path.join(BASE, "engine")
ENGINE_EXE_SRC = os.path.join(BASE, "dist", "ZBCollisionEngine")  # 免 Python 引擎
MACRO_SRC_DIR = os.path.join(BASE, "zscript", "out")
MACRO_FOLDER = "Collision Check"            # ZStartup/Macros/ 下的文件夹（ASCII）
MACRO_FILES = ["Quick Check.TXT", "Full Check.TXT"]
LEGACY_MACRO_FOLDER = "分件碰撞检测"          # 旧版中文名宏文件夹，安装时清理
CONFIG_PATH = os.path.join(BASE, "installer", "install_config.json")

ENGINE_FILES = [
    "collision_engine.py",
    "result_window.py",
    "detect_and_show.pyw",
    "run_detect.py",
    "run_detect.bat",
    "launcher.pyw",
]


def _dir_has_macro_root(path: str) -> bool:
    """判断一个目录是否为有效的 ZBrush 安装根（含 ZBrush.exe 与 ZStartup\\Macros）。

    要求 ZBrush.exe 存在，避免把宏装进 ZBrush 的备份/解压残留目录。
    """
    return (os.path.isdir(os.path.join(path, "ZStartup", "Macros"))
            and os.path.isfile(os.path.join(path, "ZBrush.exe")))


def _from_registry():
    """从 Windows 注册表查找 ZBrush 安装路径（只读，失败静默忽略）。"""
    found = []
    if sys.platform != "win32":
        return found
    try:
        import winreg
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Pixologic\ZBrush"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Pixologic\ZBrush"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Maxon\ZBrush"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Pixologic\ZBrush"),
        ]
        for hive, sub in keys:
            try:
                with winreg.OpenKey(hive, sub) as key:
                    i = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(key, i)
                        except OSError:
                            break
                        if isinstance(value, str) and os.path.isdir(value):
                            found.append(value)
                        i += 1
            except OSError:
                continue
            # 也检查 InstallLocation / InstallDir 命名值
            for value_name in ("InstallLocation", "InstallDir", "Path"):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        value, _ = winreg.QueryValueEx(key, value_name)
                    if isinstance(value, str) and os.path.isdir(value):
                        found.append(value)
                except OSError:
                    pass
    except Exception:
        pass
    return found


def _from_common_locations():
    """扫描常见安装位置（Program Files / 用户常用目录 / 盘符根目录）。"""
    candidates = []
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)",
                                       r"C:\Program Files (x86)")
    for base in (program_files, program_files_x86):
        if not base:
            continue
        candidates.extend(glob.glob(os.path.join(base, "ZBrush*")))
        candidates.extend(glob.glob(os.path.join(base, "Maxon",
                                                  "ZBrush*")))
    local_app = os.environ.get("LOCALAPPDATA")
    if local_app:
        candidates.extend(glob.glob(os.path.join(local_app, "ZBrush*")))
    # 各盘符根目录下名为 ZBrush* 的文件夹（覆盖 "D:\\软件\\ZBrush*" 这类二级目录布局）
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        candidates.extend(glob.glob("%s:\\ZBrush*" % drive))
        candidates.extend(glob.glob("%s:\\*\\ZBrush*" % drive))
    return candidates


def _from_env():
    raw = os.environ.get("ZBRUSH_PATH", "")
    return [p for p in raw.split(os.pathsep) if p]


def _from_config(config_path=CONFIG_PATH):
    if not os.path.isfile(config_path):
        return []
    try:
        with open(config_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        paths = data.get("zbrush_paths", [])
        return [p for p in paths if isinstance(p, str) and p]
    except Exception:
        return []


def discover_zbrush_paths(explicit=None, config_path=CONFIG_PATH):
    """按优先级合并发现所有 ZBrush 安装目录，去重并过滤有效目录。

    返回 [(label, path), ...]，label 由路径中的版本数字推断。
    """
    raw = []
    if explicit:
        raw.extend(explicit)
    raw.extend(_from_config(config_path))
    raw.extend(_from_registry())
    raw.extend(_from_common_locations())
    raw.extend(_from_env())

    seen = set()
    results = []
    for p in raw:
        p = os.path.abspath(os.path.expanduser(os.path.normpath(p)))
        key = p.lower()
        if key in seen or not _dir_has_macro_root(p):
            continue
        seen.add(key)
        label = _version_label(p)
        results.append((label, p))
    return results


def _version_label(path: str) -> str:
    """从目录名推断版本标签，用于日志与 IConfig 决策。"""
    import re
    m = re.search(r"(20\d{2})(?:\.(\d+))?", os.path.basename(path))
    if m:
        return "ZBrush %s.%s" % (m.group(1), m.group(2) or "0")
    return "ZBrush (%s)" % os.path.basename(path)


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def install_engine(workdir: str):
    """把引擎安装到无空格工作目录。"""
    ensure_dir(workdir)
    for name in ENGINE_FILES:
        src = os.path.join(ENGINE_SRC, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(workdir, name))
            print("  [复制] 引擎文件：%s" % name)
    # 免 Python 的打包引擎（ZBrush 宏优先启动它；缺失时回退 bat + Python）
    exe_dir = os.path.join(workdir, "bin")
    if os.path.isfile(os.path.join(ENGINE_EXE_SRC, "ZBCollisionEngine.exe")):
        if os.path.isdir(exe_dir):
            shutil.rmtree(exe_dir, ignore_errors=True)
        shutil.copytree(ENGINE_EXE_SRC, exe_dir)
        print("  [复制] 免 Python 引擎：bin/ZBCollisionEngine.exe")
    else:
        print("  [提示] 未找到打包引擎 dist/ZBCollisionEngine，将依赖本机 Python 3 运行。")
    ensure_dir(os.path.join(workdir, "导出数据"))
    with open(os.path.join(workdir, "version.txt"), "w", encoding="utf-8") as fh:
        fh.write("多版本通用（2022.0.5 / 2025.0.1）")
    # 静态中文错误说明页：bat 找不到 Python 时用 start 打开它，
    # 让用户一定能看到明确提示（bat 本身不能可靠写中文）。
    help_page = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>检测失败：未找到 Python</title></head>
<body style="font-family:Microsoft YaHei,sans-serif;padding:28px">
<h2 style="color:#C0392B">检测失败：未找到 Python 3</h2>
<p>碰撞检测引擎需要 Python 3 才能运行，但当前系统没有找到它。</p>
<p><b>解决办法：</b></p>
<ol>
<li>安装 Python 3（官网 python.org，安装时勾选 “Add Python to PATH”）。</li>
<li>安装完成后，重新点击 ZBrush 里的「一键检测」按钮即可，无需重装本插件。</li>
</ol>
<p style="color:#555">如果不确定是否装好，打开命令行输入 <code>py --version</code> 能看到版本号即可。</p>
</body></html>"""
    with open(os.path.join(workdir, "help_no_python.html"),
              "w", encoding="utf-8") as fh:
        fh.write(help_page)
    # 默认检测模式：first=快速（每对首个相交点）；用户可改为 all=完整
    mode_path = os.path.join(workdir, "mode.txt")
    if not os.path.isfile(mode_path):
        with open(mode_path, "w", encoding="ascii") as fh:
            fh.write("first")
    print("  [完成] 引擎工作目录：%s" % workdir)


def install_macro(zb_path: str, version_label: str):
    """把一个 ZBrush 版本的宏安装到位。"""
    macro_root = os.path.join(zb_path, "ZStartup", "Macros")
    if not os.path.isdir(zb_path):
        print("  [跳过] 未找到 ZBrush 安装目录：%s" % zb_path)
        return False
    legacy = os.path.join(macro_root, LEGACY_MACRO_FOLDER)
    if os.path.isdir(legacy):
        shutil.rmtree(legacy, ignore_errors=True)
        print("  [清理] 旧版宏文件夹：%s" % legacy)
    target_dir = os.path.join(macro_root, MACRO_FOLDER)
    ensure_dir(target_dir)
    for name in MACRO_FILES:
        src = os.path.join(MACRO_SRC_DIR, name)
        if not os.path.isfile(src):
            raise SystemExit("缺少宏源文件：%s（先运行 zscript/generate_macro.py）" % src)
        target = os.path.join(target_dir, name)
        shutil.copy2(src, target)
        # 删除旧的编译缓存，确保 ZBrush 重新编译最新宏
        zsc = os.path.splitext(target)[0] + ".zsc"
        if os.path.isfile(zsc):
            os.remove(zsc)
        print("  [安装] %s -> %s" % (version_label, target))
    return True


def patch_macro_for_version(zb_path: str, workdir: str, label: str):
    """按目标版本替换宏里的工作目录与 IConfig 版本标记。

    IConfig 声明脚本所需最低版本，必须与目标 ZBrush 版本匹配：
    已在两版实测的第三方插件写法：2025 用 IConfig 2025、2022 用 IConfig 2021
    （注意 2022 版对应的是 2021，而非 2022）。声明过高会导致脚本被拒绝加载。
    """
    for name in MACRO_FILES:
        target = os.path.join(zb_path, "ZStartup", "Macros", MACRO_FOLDER, name)
        if os.path.isfile(target):
            _patch_one(target, workdir, label, zb_path)


def _patch_one(target: str, workdir: str, label: str, zb_path: str):
    with open(target, "r", encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    normalized = workdir.replace("\\", "/").rstrip("/") + "/"
    # 从标签/路径提取年份，>=2025 用 2025，其余用 2021（对齐官方插件）
    import re
    years = re.findall(r"20\d{2}", label + " " + zb_path)
    year = int(years[0]) if years else 2021
    icfg = "2025" if year >= 2025 else "2021"
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[VarDef,gWorkDir,"):
            lines.append('[VarDef,gWorkDir,"%s"]' % normalized)
        elif stripped.startswith("[IConfig,"):
            lines.append('[IConfig,%s]' % icfg)
        else:
            lines.append(line)
    # 以 utf-8（无 BOM）写回，保证宏源码保持纯 ASCII（ZScript 编译器限制）
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    print("    [版本适配] %s -> IConfig %s, 工作目录 %s（%s）"
          % (label, icfg, normalized, os.path.basename(target)))


def detect_workdir_has_space(workdir: str) -> bool:
    return " " in workdir


def main(argv=None):
    parser = argparse.ArgumentParser(description="安装 ZBrush 分件碰撞检测插件")
    parser.add_argument("--workdir", default=r"C:\ZBCollision",
                        help="无空格纯英文工作目录（默认 C:\\ZBCollision）")
    parser.add_argument("--zbrush-path", action="append", default=[],
                        help="ZBrush 安装根目录，可重复指定；不指定则自动发现")
    parser.add_argument("--config", default=CONFIG_PATH,
                        help="含 zbrush_paths 数组的 JSON 配置文件路径")
    args = parser.parse_args(argv)

    print("=" * 56)
    print("ZBrush 分件碰撞检测插件 —— 安装程序")
    print("=" * 56)

    if detect_workdir_has_space(args.workdir):
        print("[错误] 工作目录不能包含空格：%s" % args.workdir)
        print("       请使用类似 C:\\ZBCollision 的纯英文无空格路径。")
        return 2

    print("\n[1/3] 安装检测引擎...")
    install_engine(args.workdir)

    print("\n[2/3] 发现并安装 ZBrush 宏...")
    zb_paths = discover_zbrush_paths(args.zbrush_path, args.config)
    if zb_paths:
        print("  发现 %d 个 ZBrush 安装：" % len(zb_paths))
        for label, path in zb_paths:
            print("    - %s : %s" % (label, path))
    else:
        print("  未自动发现 ZBrush 安装目录。")

    installed = 0
    for label, path in zb_paths:
        if install_macro(path, label):
            patch_macro_for_version(path, args.workdir, label)
            installed += 1

    print("\n[3/3] 完成")
    if installed == 0:
        print("[警告] 没有检测到任何 ZBrush 安装目录，仅安装了引擎。")
        print("       请用以下任一方式指定 ZBrush 安装根目录后重跑：")
        print("         python install.py --zbrush-path \"D:\\...\\ZBrush 2025\"")
        print("         或创建 installer/install_config.json：")
        print('           {"zbrush_paths": ["D:\\\\...\\\\ZBrush 2025"]}')
    else:
        print("已安装到 %d 个 ZBrush 版本。" % installed)
    print("\n使用方法：重启 ZBrush -> Macro 面板 -> Collision Check -> Quick Check（快速）/ Full Check（完整）")
    print("详细中文说明见 docs/使用说明.html")
    return 0


if __name__ == "__main__":
    code = main()
    if getattr(sys, "frozen", False):
        # 双击 exe 运行时窗口会立刻关闭，停一下让用户看到结果
        try:
            print()
            input("按回车键关闭窗口...")
        except Exception:
            pass
    sys.exit(code)
