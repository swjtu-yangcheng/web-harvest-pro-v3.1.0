# 中国大陆网络与代理诊断

将网络路径视为运行环境配置；skill 不能保证某地区、VPN 节点或云端可达。

## 三种路线

| 配置 | 行为 | 使用条件 |
|---|---|---|
| `--proxy-mode env`，默认 | urllib 采用运行环境 HTTP(S) 代理／NO_PROXY | 宿主已经设置正确代理 |
| `--proxy-mode direct` | 显式关闭应用层环境代理 | 用户选择直连；系统 VPN/TUN 仍可能接管 |
| `--proxy-mode explicit` | 使用 `HARVEST_PROXY` 的 HTTP(S) 代理 | 指定已经运行的代理端口 |

环境变量：`HARVEST_PROXY_MODE`、`HARVEST_PROXY`、`HARVEST_CACHE_DIR`。代理 URL 不放在命令行、报告或配置样例中保存真实密码。不要假设端口一定为 7890。SOCKS-only 代理不由标准库直接支持；使用代理软件的 HTTP 混合端口、系统 TUN，或宿主原生连接器。

浏览器适配器显式传递发现的 HTTP(S) 代理；`env` 模式会考虑 NO_PROXY。`direct` 不等于关闭操作系统 VPN。不存在“在用户电脑开了 VPN，云端智能体也自动经过该 VPN”的保证。

## 一次诊断，复用结果

```bash
python "<skill>/scripts/harvest.py" doctor
python "<skill>/scripts/harvest.py" doctor --network --probe "https://api.crossref.org/works?rows=1"
```

只有需要对比出口时才显式加 `--compare-routes`。它增加一次直连探测，不修改系统代理、VPN 或持久配置。按实际目标判断，勿每个子任务重复跑全套联网检查。

| 观察 | 能确定什么 | 下一步 |
|---|---|---|
| dns_error | 本执行环境 DNS 未能解析 | 核对 DNS／执行环境／网络策略 |
| tls_error | 证书／TLS 握手失败 | 校验系统时间、证书链、代理；不关闭 TLS 验证 |
| tunnel／proxy 错误 | 代理通道建立异常 | 检查应用代理端口、节点和认证 |
| HTTP 502/503 | 收到服务错误；仅状态码无法确定是节点还是源站 | 比较已授权路线与不同站点，保留不确定性 |
| HTTP 401/403 | 认证或访问策略受限 | 现有授权连接器、公开替代源；停止原页升级 |
| HTTP 429 | 速率／额度受限 | Retry-After、降低并发、缓存／延期 |
| js_required | 静态响应正文不足、具有 JS 壳特征 | 单页浏览器试取，核对真正内容 |
| 空搜索结果 | 可能无匹配，也可能布局／地区／限流问题 | 更换检索表达与独立搜索渠道 |

代理错误绝不隐式转直连；403、验证码和付费墙不作为浏览器升级许可。HTML 难点和访问权限难点要分开处理。

## 运行边界

默认仅访问公共 HTTP(S) 地址；每次跳转复核目标及爬取范围。`--allow-private` 只用于明确授权的私有站点或本地测试，不用于一般公网任务。该检查是应用层防护，不能替代网络隔离；DNS 重绑定、代理的远端 DNS 与操作系统路由仍需部署环境控制。

每页有网络、字节和浏览器进程预算。urllib socket timeout 与循环时间检查不能完全接管操作系统的 DNS 解析时限；需要严格墙钟上限时由宿主对子进程设置总 timeout。外部 browser worker 的父进程设置超时，退出后操作系统中浏览器子进程的回收仍依平台而异。
