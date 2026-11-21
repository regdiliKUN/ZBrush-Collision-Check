# ZBrush 碰撞检测插件（ZBrush Collision Check）

一键检查 ZBrush 里所有子工具（SubTool）两两之间有没有穿插、相交——手办分件 3D 打印前的干涉自检，再也不用靠肉眼一个个转着看。

- ✅ 支持 **ZBrush 2025.0.1** 和 **ZBrush 2022.0.5**（两版均已实机测试）
- ✅ **不用装 Python**：下载安装包，双击 `安装.bat` 就能用，换电脑也一样
- ✅ 中文结果窗口：直接列出“哪个部件和哪个部件撞了”
- ✅ 两种模式：**快速检测**（只判断撞没撞，速度快）/ **完整检测**（列出每一对的全部相交点和坐标）
- ✅ 只读检测：只导出、不修改你的模型；检测完自动恢复你原来的选中和显示状态

## 下载安装

1. 打开 [Releases 页面](../../releases/latest)，下载 `ZBrush-Collision-Check-v*.zip` 并解压。
2. **先关闭 ZBrush**，双击解压目录里的 `安装.bat`。
   安装程序会自动找到电脑上的 ZBrush（注册表 + 常见安装目录），并把检测程序装到 `C:\ZBCollision`。
3. 重新打开 ZBrush。

> ZBrush 装在比较特别的位置、没被自动找到？用命令行运行：
> `安装.bat --zbrush-path "你的ZBrush安装目录"`（就是有 ZBrush.exe 的那一层）。

## 使用方法

1. 打开模型，按 **T** 进入 Edit 编辑模式（至少要有 2 个子工具）。
2. 顶部菜单 **宏（Macro）** → **Collision Check** 文件夹：
   - **Quick Check**：快速检测，每对只记第一处相交点；
   - **Full Check**：完整检测，列出每对的全部相交点。
3. 稍等几秒，会弹出中文结果窗口。结果同时保存在 `C:\ZBCollision\`：
   `完整报告.txt`、`result.json`，以及累积的 `碰撞检测记录.txt`。

子工具在报告里按面板顺序显示为「子工具 1、子工具 2……」（1 = 子工具列表最上面那个）。

## 工作原理

ZBrush 的脚本拿不到网格的三角面数据，所以插件分两步走：

1. **ZScript 宏**：逐个选中子工具 → 只显示它 → 导出为 `sub_N.obj`，导出完恢复原状态；
2. **检测引擎**（Python + NumPy，已打包成 exe）：包围盒粗筛 + 三角面精确相交判定（Möller–Trumbore 边-面求交）+ 射线法判断“一个部件整个藏在另一个里面”的情况。

说明：一个部件**完全包在另一个里面**（表面不接触）也算相交，这是有意为之。

## 从源码构建（开发者）

需要 Python 3 + numpy + pyinstaller：

```
installer\build_exe.bat          重新生成宏、打包检测引擎与安装程序
py -3 tests\回归测试.py           回归测试
py -3 tests\宏逻辑模拟.py         宏流程模拟
py -3 installer\自检.py           安装后自检
```

目录：`engine/` 检测引擎 · `zscript/` 宏生成器与生成的宏 · `installer/` 安装程序 · `tests/` 测试 · `docs/使用说明.html` 图文说明。

## 许可

[MIT](LICENSE)
