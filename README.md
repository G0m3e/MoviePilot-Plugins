# StrmHub MoviePilot 插件

[G0m3e](https://github.com/G0m3e) 维护的 MoviePilot 插件仓库，目前仅包含 **StrmHubBridge**（StrmHub 联动）。

Fork 自 [jxxghp/MoviePilot-Plugins](https://github.com/jxxghp/MoviePilot-Plugins) 骨架；插件开发规范见官方 [V2 插件开发指南](https://github.com/jxxghp/MoviePilot-Plugins/blob/main/docs/V2_Plugin_Development.md)。

## 安装

在 MoviePilot **插件市场** 添加本仓库地址：

```
https://github.com/G0m3e/MoviePilot-Plugins
```

安装 **StrmHub 联动**（`StrmHubBridge`）。

## 插件说明

| 插件 ID | 目录 | 说明 |
|---------|------|------|
| `StrmHubBridge` | `plugins.v2/strmhubbridge/` | MP 整理完成 → 调用 StrmHub Webhook → 增量生成 STRM |

详细配置与前置条件见 [plugins.v2/strmhubbridge/README.md](plugins.v2/strmhubbridge/README.md)。

StrmHub 主项目：[G0m3e/StrmHub](https://github.com/G0m3e/StrmHub)

## 目录结构

```text
MoviePilot-Plugins/
├── plugins.v2/
│   └── strmhubbridge/     # StrmHub 联动插件
├── package.v2.json        # V2 插件市场索引
└── package.json           # 空（无 V1 插件）
```

## 许可证

GPL-3.0（与 MoviePilot 官方插件仓库一致，见 [LICENSE](LICENSE)）。
