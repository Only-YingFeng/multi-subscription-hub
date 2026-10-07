# Qt 显示层 — 第三方许可、版权与重建说明

本目录可随 Clash 节点备份助手的 Windows 发布 ZIP 一同提供。它覆盖新增 Qt 显示层及 QtAwesome 随包字体；Python、Node.js、PyInstaller、PyYAML 的既有许可仍须保留在发布包的其他许可目录。本说明不把第三方组件的许可改成应用自身许可，也不声称本目录每种许可对应的组件都实际链接进 Windows EXE。

## 本次核验版本与使用方式

| 组件 | 实际安装版本 | 本次使用的许可路径 / 版权归属 |
|---|---|---|
| Qt 运行库 | 6.11.2，Windows x64，MSVC 2022，shared/dynamic release | 符合 LGPL 条件的运行库按 LGPL-3.0 使用；Qt Company Ltd. 与 Qt 项目贡献者。具体其他作者和年份见版本化源码及 `qt-attributions/` |
| PySide6-Essentials | 6.11.2 | 元数据为 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`；此次选择 LGPL-3.0 路径。Qt for Python Team / Qt Company Ltd. 与贡献者 |
| shiboken6 | 6.11.2 | 同上；此次选择 LGPL-3.0 路径。Qt Company Ltd. 与贡献者 |
| QtAwesome | 1.4.2 | MIT；Copyright (c) 2015 The Spyder development team |
| QtPy | 2.4.3 | MIT；Copyright (c) 2011- QtPy contributors and others；作者资料见 `QtPy-AUTHORS.md` |

版本来自发布构建环境中已安装分发包的元数据；Qt 版本和动态库构建信息来自 `QtCore.qVersion()` 与 `QLibraryInfo.build()`。本应用没有修改 Qt、PySide6、Shiboken6 的上游源码或库文件，也没有修改随包字体。应用对 Qt 的使用限于显示层，订阅、配置和私有状态不是本目录内容。

LGPL 路径不自动适用于所有 Qt 模块。发布方必须使用最终二进制清单确认所打包模块的具体许可；若增加仅 GPL 或另有许可的模块，应在发布前重新处理该模块的条款。`Qt-Commercial-Alternative.txt` 是 wheel 原有的商业替代许可材料，保留它不表示本应用持有商业授权；此次不是以商业许可替代开源义务。

## Qt / PySide / Shiboken 许可原文与来源

- `LGPL-3.0.txt` 与 `GPL-3.0.txt`：完整许可文本，来自 Qt 6.11.2 的官方版本化源码副本。LGPLv3 是 GPLv3 的附加许可，故同时提供两份原文。
- `qtbase/`：Qt Base **v6.11.2** 官方 `LICENSES` 目录的原始文本，包括 LGPL、GPL、Apache、BSD、MIT、Unicode、FreeType、libpng 等许可。目录保存全部标准许可以保留第三方条款，不能据此推断应用整体使用 GPL。
- `pyside/`：Qt for Python / Shiboken **v6.11.2** 官方 `LICENSES` 原始文本；GPL 替代许可及 Qt GPL exception 原文亦保留。
- `qt-attributions/`：QtCore、QtGui、QtWidgets 官方模块许可说明，以及 QtCore / QtGui 第三方组件的完整版权与条款存档。包括针对其他操作系统的说明；这些额外说明不代表本 Windows 包分发了相应平台实现。
- `Qt-For-Python-Third-Party-Licenses.html`：Qt for Python 官方第三方许可说明，包括其签名支持代码所采用的 Python 原始许可资料。
- `Qt-6.11-Licensing.html`：Qt 官方许可说明存档。
- `provenance.json`：每份原文的确切来源、字节数与 SHA-256，以及实际安装版本和 12 个字体文件的 SHA-256。HTML 文件是许可/版权条款存档，未下载其中的远端图片、样式表或脚本。
- `FILELIST.txt`：本目录的固定文件名清单，供发布白名单核验；不允许按目录递归自动收录不明文件。

官方版本化源码位置：

- [Qt Base v6.11.2 LICENSES](https://code.qt.io/cgit/qt/qtbase.git/tree/LICENSES?h=v6.11.2)
- [Qt for Python / Shiboken v6.11.2 LICENSES](https://code.qt.io/cgit/pyside/pyside-setup.git/tree/LICENSES?h=v6.11.2)
- [Qt 许可说明](https://doc.qt.io/qt-6/licensing.html)
- [Qt for Python 第三方许可说明](https://doc.qt.io/qtforpython-6/licenses.html)

Qt Base 的 `qglobal.h` 保留 Qt Company Ltd. 2020 与 Intel Corporation 2019 的版权声明；PySide 的 `pyside.h` 保留 Qt Company Ltd. 2016 声明，Shiboken 的 `basewrapper.h` 保留 Qt Company Ltd. 2019 声明。这些只是代表性源码声明；其他文件的权利人和年份仍由其原始源码与附带 attribution 声明确定，未以单个统一年份替换。

## QtAwesome 的全部随包字体

QtAwesome 1.4.2 默认初始化会加载其全部内置字体，发布构建也收集该包的数据。因此，即使界面实际请求的图标前缀是 `mdi6`，下面全部字体仍随包提供，不能只附 `mdi6` 的许可。

| 随包字体 / 前缀 | 字体版本 | 字体许可与版权 / attribution | 原文 |
|---|---|---|---|
| Font Awesome Regular / Solid / Brands，`fa5`、`fa5s`、`fa5b` | 5.15.4 | SIL OFL 1.1；字体内置声明 `Copyright (c) Font Awesome`；Fonticons, Inc. / Font Awesome | `fonts/FontAwesome-5.15.4-LICENSE.txt`，`OFL-1.1.txt` |
| Font Awesome Regular / Solid / Brands，`fa6`、`fa6s`、`fa6b` | 6.7.2 | SIL OFL 1.1；上游完整许可声明 Copyright (c) 2024 Fonticons, Inc.，Reserved Font Name: Font Awesome | `fonts/FontAwesome-6.7.2-LICENSE.txt` |
| Elusive Icons，`ei` | 2.0 | SIL OFL 1.1；Elusive Icons by Team Redux，项目同时保留 Dave Gandy attribution | `OFL-1.1.txt`，`fonts/Elusive-Icons-Upstream-Attribution.md` |
| Material Design Icons，`mdi` | 5.9.55 | Apache 2.0；Pictogrammers / Material Design Icons 项目及贡献者。保留上游对原始图标版权与各自许可的说明 | `fonts/MaterialDesign-5.9.55-LICENSE.txt`，`qtbase/Apache-2.0.txt` |
| Material Design Icons，**`mdi6`** | **6.9.96** | **Apache 2.0**；Pictogrammers / Material Design Icons 项目及贡献者，字体原样分发 | `fonts/MaterialDesign-6.9.96-LICENSE.txt`，`qtbase/Apache-2.0.txt` |
| Phosphor，`ph` | 1.3.0 | MIT；Copyright (c) 2020 Phosphor Icons；字体内置许可记录为 MIT | `fonts/Phosphor-1.3.0-MIT.txt` |
| Remix Icon，`ri` | 2.5.0 | Apache 2.0；Remix Design / Remix Icon 项目。使用该旧版本原始许可，而非后来当前版本的不同许可 | `fonts/Remix-2.5.0-Apache-2.0.txt` |
| Microsoft Codicons，`msc` | 0.0.36 | Creative Commons Attribution 4.0 International；Codicons by Microsoft / Visual Studio Code icons project | `fonts/Codicon-0.0.36-CC-BY-4.0.txt` |

QtAwesome、字体及其许可原文未作修改。品牌图标中的商标属于各自权利人；包含这些字体不表示权利人认可或赞助本应用。字体可随软件一并再分发，仍需保留其版权与许可；修改 OFL 字体时还应遵守 Reserved Font Name 等条款。

Elusive 的字体使用 OFL；附带的上游 README 是 attribution 来源，其项目文档另按 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) 提供。此 README 保留原文及 [Team Redux 项目来源](https://github.com/ReduxFramework/Elusive-Icons)，未改写为本应用自身创作。

确切来源包括 [QtAwesome v1.4.2 的许可与字体清单](https://github.com/spyder-ide/qtawesome/tree/v1.4.2)、[Material Design Webfont v6.9.96](https://github.com/Templarian/MaterialDesign-Webfont/tree/v6.9.96)、[Font Awesome 6.7.2](https://github.com/FortAwesome/Font-Awesome/tree/6.7.2)、[Remix Icon v2.5.0](https://github.com/Remix-Design/RemixIcon/tree/v2.5.0) 与 [Codicons 0.0.36](https://github.com/microsoft/vscode-codicons/tree/0.0.36)。逐文件获取 URL 与哈希以 `provenance.json` 为准。Phosphor 与 Elusive attribution 原文使用其各自官方仓库来源，取得的具体内容已固定哈希，不以未核验的版本标签代替。

## 获取对应源码、替换依赖与重建

接收者可以为修改、替换 Qt / PySide / Shiboken 依赖及调试这些修改而检查、反向工程、重建和运行本应用；本应用不施加与 LGPLv3 相反的限制，不要求许可签名或设备密钥来重建版本。该说明不缩减第三方许可授予的权利。

发布包应保留与 EXE 对应的应用公开源码、`build.py`、构建/发布清单、UI 资源及本许可目录，使用户可以在独立环境中重新组合自己的兼容依赖。PyInstaller 单文件 EXE 会在运行时解包动态库；直接修改随 EXE 临时解包的文件并非持久替换方案。推荐使用所附应用源码运行或重建，避免覆盖正在使用的原版本。

Qt 与 Qt for Python / Shiboken 的对应 **6.11.2** 上游源码可以免费取得：

- [Qt 6.11.2 完整源码下载目录](https://download.qt.io/archive/qt/6.11/6.11.2/single/)：`qt-everywhere-src-6.11.2.tar.xz` / `.zip`。
- [Qt for Python / Shiboken 6.11.2 对应源码下载目录](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)：`pyside-setup-everywhere-src-6.11.2.tar.xz` / `.zip`。
- [Qt for Python 源码构建指南](https://doc.qt.io/qtforpython-6/building_from_source/index.html)。Qt、PySide、Shiboken 自建时需保持 Windows x64、Python ABI 与相互兼容的 Qt 版本，并使用官方指南要求的 CMake / 编译器等工具。

在配套源码中含 `app.py` 和 `build.py` 的目录，使用 CPython 3.13 x64 建立独立重建环境。以下命令只创建用户自选的本地重建环境，不是应用自动执行的行为：

```powershell
python -m venv .venv-ui-rebuild
.\.venv-ui-rebuild\Scripts\python.exe -m pip install PySide6-Essentials==6.11.2 shiboken6==6.11.2 QtAwesome==1.4.2 QtPy==2.4.3 PyYAML==6.0.3 PyInstaller==6.20.0
.\.venv-ui-rebuild\Scripts\python.exe -B .\app.py
```

上面的固定版本复现此次依赖组合；要替换 LGPL 依赖，可在此独立环境中安装自己取得或按对应源码构建的兼容 Qt / PySide / Shiboken，再从源码运行。修改后的依赖版本应与本应用接口及平台 ABI 兼容，需自行验证行为。

构建独立 EXE 的入口为：

```powershell
.\.venv-ui-rebuild\Scripts\python.exe -B .\build.py --node "可信 Node.js 25.2.1 的 node.exe 路径" --output ".\dist-rebuild"
```

如果所附构建/发布清单使用了许可文件哈希保护，它只用于验证原发布材料的完整性；使用自建或替换依赖时可以更新自己重建版的材料与清单，不构成禁止依赖替换。重建程序需要应用公开源码与 UI 资源，不需要本机订阅、私有备份、账户凭据或原用户状态。

发布方须将这些源码获取入口及重建材料与二进制一起保留，并按相应条款持续提供对应源码访问。该目录不代替对最终发行包内容的核验；增加依赖、修改库或改变源码提供方式时，应同步适用许可、attribution 和重建材料。
