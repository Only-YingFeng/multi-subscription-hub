# 离线回归测试

在已安装 Python、PyYAML、Node 的环境执行：

```powershell
python -B tools/clash-zeroomega-app/tests/run-tests.py
```

测试使用合成订阅、节点与配置，全部写入系统临时目录并在结束时清理。Clash API、核心校验、热重载、监听端口及进程查询均由 mock 代替；只启动本地 JavaScript 路由转换器，不安装或操作真实 Clash。

- `regression-tests.js`：复用既有路由回归，包括顺序、单节点绑定、失效拒绝、UDP 防止回落到直连、输入校验与幂等。
- `mapping-update-tests.py`：稳定端口、订阅身份隔离、增删改名、确定性与冲突阻止。
- `manager-transaction-tests.py`：显式安装/回滚的合成事务、外部编辑保护、异常恢复和需手动恢复时的记录。
- `portable-interface-tests.py`：只读探测、生成与应用分离、过期探测拒绝、临时文件锁冲突、版本和状态不明时停止、混合代理组必须明确选择分流动作及同订阅缓存复用；应用/回滚仅验证 mock 调用。
- `connectivity-tests.py`：指定真实节点的独立 204 连通性测试，模拟超时、失败、错误响应、并发上限及配置变化；无真实 API 或网络调用。

最后的安全回归还覆盖私有目录和导出目录边界、模板字段与本地 SOCKS5 限制、已生成模板重复使用，以及 PowerShell 子进程模块路径隔离；不会修改全局环境或真实目录权限。

通过仅代表离线行为，不代表真实 Clash 热重载、ZeroOmega 导入或浏览器验收。

Qt 显示层和线程控制器使用项目 `.venv-qt` 环境执行 `qt-view-tests.py` 与 `qt-controller-tests.py`，验证一屏布局、动作权限、真实 Qt 事件派发、忙碌状态、过期结果、脱敏和确认门槛。这些测试使用合成数据，不连接生产 API。实际 EXE 视觉验收使用 Windows 窗口截图与选定样板同尺度对比，报告见 `../design-qa.md`。

真实测试单独执行：`real-node-checks.py --output <验证目录>` 会向当前订阅的每条指定节点发小流量测试请求，并生成备份，检查生产文件、选择和核心保持原样；不回写或重载。`real-core-transaction.py` 使用临时合成配置和独立复制的 Mihomo 核心验证持久扩展写回、热重载和回滚，控制接口写入只允许自己的测试进程。
