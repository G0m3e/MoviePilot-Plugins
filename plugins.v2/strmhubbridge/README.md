# StrmHub 联动插件

整理完成后调用 StrmHub 直接写 STRM（推荐）或触发生活事件增量。

## 前置条件

1. StrmHub 已配置 115 Cookie、STRM 输出路径、**目录映射**
2. 直写模式：映射需覆盖整理目标路径
3. 增量模式：115 App 已开启「最近记录 / 生活事件」
4. 宿主与 StrmHub 网络互通

## StrmHub 侧配置

在 StrmHub **系统 → Token 管理** 中新建 Webhook Token，完整值仅创建时显示一次。

## 插件配置

| 项 | 说明 |
|----|------|
| StrmHub API 地址 | 如 `http://192.168.0.36:5800` |
| Webhook Token | 与 Token 管理中创建的值一致 |
| 同步模式 | **直接写 STRM**（推荐）或触发生活事件增量 |
| 去抖秒数 | 连续整理时合并触发，默认 30 |
| metadata.scrape | 整批整理完成后触发（推荐开启） |
| transfer.complete | 每文件触发，默认按插件配置 |

## 注意事项

- **勿**与其他 STRM 写入插件对同一目录双开
- StrmHub Cron 增量仍建议保留，作为无人整理时的兜底
- 409 表示 StrmHub 已有同步任务，属正常互斥

## 工作流（可选）

也可不用事件监听，在工作流中：

1. 触发：`metadata.scrape`
2. 动作：调用插件 → `StrmHubBridge` → `trigger_increment` 或直写
