# 合成测试与验收边界

在仓库根目录、已安装 requirements-build.txt 的 Windows Python 3.13 环境执行：

```powershell
.\.venv\Scripts\python.exe -B tests/test_backend_contract.py
.\.venv\Scripts\python.exe -B tests/test_controller.py
.\.venv\Scripts\python.exe -B qt_view_tests.py
.\.venv\Scripts\python.exe -B original-source/tests/test_feiniao_bridge.py
```

后台合约共有 71 项，其中核心语法测试仅在提供自己的可信 Mihomo 时运行，否则明确跳过 1 项：

```powershell
$env:MSH_TEST_CORE = 'C:\Path\To\verge-mihomo.exe'
.\.venv\Scripts\python.exe -B tests/test_backend_contract.py
Remove-Item Env:\MSH_TEST_CORE
```

该可选检查只执行合成配置的 `-t`，不启动监听器或重载主核心。其他测试使用虚构订阅、节点、临时目录、模拟后台和 Qt offscreen 窗口。它们覆盖固定身份/端口、失败拒绝、进程归属、未应用配置禁止导出、错误脱敏和可点击状态/布局，但不证明真实机场、其他电脑或浏览器恢复成功。

`live_*_acceptance.py` 是原开发期间的特定本机验收脚本，需要已经运行且具有相应前提的独立后台与人工授权；不是通用测试命令，不应在不理解条件时自动运行。不会在本次公开上传期间运行这些脚本。

规则依据见 Mihomo 官方 [listeners](https://wiki.metacubex.one/en/config/inbound/listeners/)、[规则](https://wiki.metacubex.one/en/config/rules/) 与 [sub-rules](https://wiki.metacubex.one/en/config/sub-rule/) 文档。
