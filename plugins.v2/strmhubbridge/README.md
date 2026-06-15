# StrmHub 联动插件

监听 MoviePilot 网盘整理完成事件，调用 StrmHub `POST /api/hooks/increment` 触发增量同步（基于 115 生活事件写 STRM）。

## 前置条件

1. StrmHub 已配置 115 Cookie、STRM 输出路径、**增量监控目录**
2. 115 App 已开启「最近记录 / 生活事件」
3. MoviePilot 与 StrmHub 网络互通

## StrmHub 侧配置

在 StrmHub 环境变量中设置专用 Webhook 密钥（推荐）：

```bash
STRMHUB_WEBHOOK_SECRET=你的随机密钥
```

未设置时，插件可使用管理员密码或 `ADMIN_TOKEN` 作为 Bearer Token。

## 插件配置

| 项 | 说明 |
|----|------|
| StrmHub API 地址 | 如 `http://192.168.0.36:8080` |
| Webhook Token | 与 `STRMHUB_WEBHOOK_SECRET` 一致 |
| 去抖秒数 | 连续整理时合并为一次触发，默认 30 |
| 触发前等待 | 等待 115 写入生活事件，默认 8 秒 |
| metadata.scrape | **推荐开启**，整批整理完成后触发一次 |
| transfer.complete | 默认关闭，每文件触发易叠加 405 |

## 注意事项

- **勿**与 p115strmhelper `transfer_monitor` 对同一目录双开写 STRM
- StrmHub Cron 增量仍建议保留，作为无人整理时的兜底
- 409 表示 StrmHub 已有同步任务，属正常互斥

## 工作流（可选）

也可不用事件监听，在 MP 工作流中：

1. 触发：`metadata.scrape`
2. 动作：调用插件 → `StrmHubBridge` → `trigger_increment`
