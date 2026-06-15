# StrmHub 联动插件

## 同步模式

| 模式 | 说明 |
|------|------|
| **直接写 STRM（推荐）** | 整理完成 → `POST /api/hooks/strm/write`，几乎实时 |
| 触发生活事件增量 | 旧行为 → `POST /api/hooks/increment`，依赖 115 生活事件 |

## 直写模式事件

| 事件 | 默认 | 行为 |
|------|------|------|
| `transfer.complete` | 开 | 每文件带 pickcode，立即写 STRM |
| `metadata.scrape` | 关 | 整批 `file_list` 路径，短去抖后批量写（服务端解析 pickcode） |

## StrmHub 前置条件

1. 增量页已配置并启用 **监控目录**（路径映射）
2. 核心配置：Cookie、STRM 源站、输出路径
3. Webhook Token：`STRMHUB_WEBHOOK_SECRET` 或管理员密码

## API

- `POST /api/hooks/strm/write` — 直写（本插件默认）
- `POST /api/hooks/increment` — 增量（旧模式）
