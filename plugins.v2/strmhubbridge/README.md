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
| 监听模式 | **单文件整理**：每个文件整理完即写 STRM；**批量整理**：整批结束后合并写一次 |
| 触发延时（秒） | 收到整理事件后延迟再写 STRM，默认 10 |
| 批量合并等待（秒） | 仅批量整理：路径合并后再写，默认 5 |
| 写 STRM 后刮削元数据 | 默认开启；STRM 落地后调用 MP 刮削 NFO/海报（日志可见，不发通知） |
| 目录映射 | 多行 `MoviePilot目录:StrmHub目录`；将 Webhook 返回的 `strm_path` 转为 MP 容器内路径后再刮削 |

示例（StrmHub 容器 `/media/strm`，MP/Emby 挂载 `/media/strmhub`）：

```
/media/strmhub:/media/strm
```

## 刮削说明

写 STRM 成功后，插件会像 p115strmhelper 一样通过 MP 内部 `MetadataScrape` 事件触发刮削：

- Webhook 返回的 `strm_path` 为 **StrmHub 容器内路径**，刮削前按「目录映射」转为 MP 路径

- **单文件整理**：优先使用整理事件自带的 `mediainfo` / `meta`
- **批量整理**：按本地 STRM 路径自动识别媒体信息
- 刮削过程仅写入 MoviePilot 插件日志（`【媒体刮削】`），不单独推送通知

## 注意事项

- **勿**与其他 STRM 写入插件对同一目录双开
- StrmHub Cron 增量仍建议保留，作为无人整理时的兜底
