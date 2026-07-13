# StrmHub 联动插件

整理完成后调用 StrmHub 直接写 STRM。

## 前置条件

1. StrmHub 已配置 115 Cookie、STRM 输出路径、**目录映射**
2. 映射需覆盖整理目标路径
3. 宿主与 StrmHub 网络互通

## StrmHub 侧配置

在 StrmHub **系统 → Token 管理** 中新建 Webhook Token，完整值仅创建时显示一次。

## 插件配置

| 项 | 说明 |
|----|------|
| StrmHub API 地址 | 如 `http://192.168.0.36:5800` |
| Webhook Token | 与 Token 管理中创建的值一致 |
| 事件延时（秒） | 收到 MP 事件后延迟再调 Webhook，默认 10 |
| 批写入去抖秒数 | metadata.scrape 路径合并，默认 5 |
| metadata.scrape | 整批整理+刮削完成后触发一次（含路径列表） |
| transfer.complete | 每个文件整理到网盘完成时触发一次 |

## 注意事项

- **勿**与其他 STRM 写入插件对同一目录双开
- StrmHub Cron 增量仍建议保留，作为无人整理时的兜底
