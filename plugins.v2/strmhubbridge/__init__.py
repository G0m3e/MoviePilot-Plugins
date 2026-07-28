"""
StrmHub 联动：MoviePilot 整理完成后直接写 STRM
"""

from __future__ import annotations

import json
import urllib.request
from threading import Lock, Thread, Timer
from time import sleep
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas import Event
from app.schemas.types import EventType
from app.helper.mediaserver import MediaServerHelper

from .mediaserver_refresh import MediaServerRefresh
from .paths import map_strmhub_path_to_mp, parse_path_mappings
from .scrape import media_scrape_metadata

_ALLOWED_STORAGES = frozenset({"u115", "115网盘Plus"})


class StrmHubBridge(_PluginBase):
    """
    MoviePilot 整理完成后调用 StrmHub 直接写 STRM
    """

    plugin_name = "StrmHub 联动"
    plugin_desc = "整理完成后调用 StrmHub 直接写 STRM"
    plugin_icon = "https://raw.githubusercontent.com/G0m3e/MoviePilot-Plugins/main/icons/strmhub.png?v=2"
    plugin_version = "1.5.1"
    plugin_author = "G0m3e"
    author_url = "https://github.com/G0m3e/StrmHub"
    plugin_config_prefix = "strmhubbridge_"
    plugin_order = 120
    auth_level = 1

    _enabled = False
    _base_url = ""
    _api_token = ""
    _batch_debounce_seconds = 5
    _event_delay_seconds = 10
    _listen_mode = "single_file"
    _notify_on_strm_result = False
    _scrape_metadata_after_strm = True
    _scrape_overwrite = True
    _media_server_refresh_enabled = False
    _media_server_refresh_delay = 5
    _mediaservers: List[str] = []
    _mp_mediaserver_paths = ""
    _path_mappings: List[Tuple[str, str]] = []
    _last_status = "尚未触发"
    _batch_timer: Optional[Timer] = None
    _debounce_lock = Lock()
    _pending_source = "mp.metadata_scrape"
    _pending_paths: Set[str] = set()

    def init_plugin(self, config: dict = None):
        """
        加载配置并生效
        """
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._base_url = (config.get("base_url") or "").strip().rstrip("/")
        self._api_token = (config.get("api_token") or "").strip()
        self._batch_debounce_seconds = max(int(config.get("batch_debounce_seconds") or 5), 1)
        delay_raw = config.get("event_delay_seconds")
        self._event_delay_seconds = max(
            int(delay_raw if delay_raw is not None else 10), 0
        )
        self._listen_mode = self._resolve_listen_mode(config)
        self._notify_on_strm_result = bool(config.get("notify_on_strm_result", False))
        self._scrape_metadata_after_strm = bool(
            config.get("scrape_metadata_after_strm", True)
        )
        self._scrape_overwrite = bool(config.get("scrape_overwrite", True))
        self._media_server_refresh_enabled = bool(
            config.get("media_server_refresh_enabled", False)
        )
        self._media_server_refresh_delay = max(
            int(config.get("media_server_refresh_delay") or 5), 0
        )
        raw_servers = config.get("mediaservers") or []
        if isinstance(raw_servers, str):
            raw_servers = [s.strip() for s in raw_servers.split(",") if s.strip()]
        self._mediaservers = [str(s).strip() for s in raw_servers if str(s).strip()]
        self._mp_mediaserver_paths = str(config.get("mp_mediaserver_paths") or "")
        self._path_mappings = parse_path_mappings(config.get("path_mappings") or "")
        saved = self.get_data("last_trigger") or {}
        if saved.get("status") == "ok":
            self._last_status = saved.get("summary") or f"最近成功 ({saved.get('source', '')})"
        elif saved.get("status") == "failed":
            self._last_status = "最近失败"

    def get_state(self) -> bool:
        """
        插件是否启用
        """
        return self._enabled and bool(self._base_url) and bool(self._api_token)

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return [
            {
                "path": "/test_notify",
                "endpoint": self.test_notify,
                "methods": ["GET"],
                "summary": "发送测试通知",
                "description": "向已配置的 MoviePilot 通知渠道发送一条测试消息",
            }
        ]

    def test_notify(self, apikey: str = "") -> schemas.Response:
        """
        发送一条测试通知，用于验证 MP 通知渠道是否可用
        """
        if apikey != settings.API_TOKEN:
            return schemas.Response(success=False, message="API密钥错误")
        Thread(target=self._send_test_notify, daemon=True).start()
        return schemas.Response(success=True, message="测试通知已发送")

    def _send_test_notify(self) -> None:
        """
        后台发送测试通知，避免阻塞详情页 API 导致「正在处理」弹窗无法关闭
        """
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title="StrmHub 测试通知",
                text="这是一条 StrmHub 联动插件的测试通知。若通知渠道已配置，应能收到此消息。",
            )
        except Exception as exc:
            logger.warning(f"[StrmHubBridge] 测试通知失败: {exc}")

    @staticmethod
    def _test_notify_click_event() -> Dict[str, Any]:
        """
        详情页测试通知按钮事件（与豆瓣想看等插件一致，apikey 走 params）
        """
        return {
            "api": "plugin/StrmHubBridge/test_notify",
            "method": "get",
            "params": {
                "apikey": settings.API_TOKEN,
            },
        }

    @staticmethod
    def _resolve_listen_mode(config: dict) -> str:
        """
        监听模式：single_file（单文件整理）或 batch（批量整理）
        兼容旧版 listen_transfer_complete / listen_metadata_scrape 双开关
        """
        mode = str(config.get("listen_mode") or "").strip()
        if mode in ("single_file", "batch"):
            return mode
        if bool(config.get("listen_metadata_scrape")) and not bool(
            config.get("listen_transfer_complete", True)
        ):
            return "batch"
        return "single_file"

    @staticmethod
    def _mediaserver_select_items() -> List[Dict[str, str]]:
        services = MediaServerHelper().get_services() or {}
        return [{"title": name, "value": name} for name in sorted(services.keys())]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        配置页
        """
        mediaserver_items = self._mediaserver_select_items()
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {"model": "enabled", "label": "启用联动"},
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 8},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "base_url",
                                            "label": "StrmHub API 地址",
                                            "placeholder": "http://192.168.0.36:5800",
                                            "autocomplete": "off",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "api_token",
                                            "label": "Webhook Token",
                                            "type": "{{ show_api_token ? 'text' : 'password' }}",
                                            "append-inner-icon": "{{ show_api_token ? 'mdi-eye-off' : 'mdi-eye' }}",
                                            "placeholder": "在 StrmHub 系统 → Token 管理 中创建后粘贴",
                                            "autocomplete": "off",
                                            "onClick:appendInner": "function() { show_api_token = !show_api_token }",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "listen_mode",
                                            "label": "监听模式",
                                            "items": [
                                                {
                                                    "title": "单文件整理",
                                                    "value": "single_file",
                                                },
                                                {
                                                    "title": "批量整理",
                                                    "value": "batch",
                                                },
                                            ],
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "scrape_metadata_after_strm",
                                            "label": "写 STRM 后刮削元数据（NFO/海报）",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "scrape_overwrite",
                                            "label": "刮削时覆盖已有 NFO/图片",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "media_server_refresh_enabled",
                                            "label": "刮削后刷新媒体库（Emby 等）",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "path_mappings",
                                            "label": "目录映射（刮削）",
                                            "rows": 3,
                                            "auto-grow": True,
                                            "placeholder": "/media/strmhub:/media/strm",
                                            "hint": "格式：MoviePilot目录:StrmHub目录，每行一条；将 Webhook 返回的 strm 路径转为 MP 可访问路径",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSelect",
                                        "props": {
                                            "model": "mediaservers",
                                            "label": "刷新的媒体服务器",
                                            "items": mediaserver_items,
                                            "multiple": True,
                                            "chips": True,
                                            "clearable": True,
                                            "hint": "需在 MP 设置 → 媒体服务器 中预先配置 Emby/Jellyfin",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "media_server_refresh_delay",
                                            "label": "刷新媒体库延迟（秒）",
                                            "type": "number",
                                            "placeholder": "刮削完成后延迟再刷新，默认 5",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12},
                                "content": [
                                    {
                                        "component": "VTextarea",
                                        "props": {
                                            "model": "mp_mediaserver_paths",
                                            "label": "媒体库路径映射",
                                            "rows": 3,
                                            "auto-grow": True,
                                            "placeholder": "/media/emby/strm#/media/strmhub",
                                            "hint": "格式：媒体库目录#MoviePilot目录，每行一条；MP 与 Emby 挂载路径不一致时必填",
                                            "persistent-hint": True,
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "notify_on_strm_result",
                                            "label": "直写 STRM 完成后推送 MP 通知",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "event_delay_seconds",
                                            "label": "触发延时（秒）",
                                            "type": "number",
                                            "placeholder": "收到整理事件后延迟再写 STRM，默认 10",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "batch_debounce_seconds",
                                            "label": "批量合并等待（秒）",
                                            "type": "number",
                                            "placeholder": "仅批量整理：路径合并后再写，默认 5",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                ],
            }
        ], {
            "enabled": False,
            "base_url": "",
            "api_token": "",
            "show_api_token": True,
            "notify_on_strm_result": False,
            "scrape_metadata_after_strm": True,
            "scrape_overwrite": True,
            "media_server_refresh_enabled": False,
            "media_server_refresh_delay": 5,
            "mediaservers": [],
            "mp_mediaserver_paths": "",
            "path_mappings": "",
            "listen_mode": "single_file",
            "batch_debounce_seconds": 5,
            "event_delay_seconds": 10,
        }

    def get_page(self) -> List[dict]:
        saved = self.get_data("last_trigger") or {}
        text = self._last_status
        detail = (saved.get("detail") or "")[:300]
        if detail:
            text = f"{text}\n{detail}"
        return [
            {
                "component": "VAlert",
                "props": {"type": "info", "variant": "tonal", "text": text},
            },
            {
                "component": "VRow",
                "props": {"class": "mt-3"},
                "content": [
                    {
                        "component": "VCol",
                        "props": {"cols": 12, "md": 6},
                        "content": [
                            {
                                "component": "VBtn",
                                "text": "测试通知",
                                "props": {
                                    "color": "secondary",
                                    "variant": "tonal",
                                    "prepend-icon": "mdi-bell-ring-outline",
                                    "block": True,
                                },
                                "events": {
                                    "click": self._test_notify_click_event(),
                                },
                            }
                        ],
                    }
                ],
            },
        ]

    @staticmethod
    def get_actions() -> List[Dict[str, Any]]:
        return []

    @eventmanager.register(EventType.MetadataScrape)
    def on_metadata_scrape(self, event: Event):
        if not self.get_state() or self._listen_mode != "batch":
            return
        paths = self._paths_from_scrape_event(event)
        if not paths:
            return
        self._schedule_batch_write(paths, "mp.metadata_scrape")

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        if not self.get_state() or self._listen_mode != "single_file":
            return
        file_item = self._file_from_transfer_event(event)
        if not file_item:
            return
        scrape_context = self._scrape_context_from_event(event)
        Thread(
            target=self._post_strm_write,
            args=([file_item], "mp.transfer_complete"),
            kwargs={"scrape_context": scrape_context},
            daemon=True,
        ).start()

    @staticmethod
    def _scrape_context_from_event(event: Event) -> Dict[str, Any]:
        """
        从 MP 整理事件提取刮削上下文（mediainfo / meta）
        """
        data = event.event_data or {}
        return {
            "mediainfo": data.get("mediainfo"),
            "meta": data.get("meta"),
        }

    @staticmethod
    def _file_from_transfer_event(event: Event) -> Optional[dict]:
        data = event.event_data or {}
        transferinfo = data.get("transferinfo")
        target = None
        if isinstance(transferinfo, dict):
            target = transferinfo.get("target_item")
        elif transferinfo is not None:
            target = getattr(transferinfo, "target_item", None)
            if target is not None and hasattr(target, "model_dump"):
                target = target.model_dump()
        if not isinstance(target, dict):
            return None
        storage = str(target.get("storage") or "")
        if storage and storage not in _ALLOWED_STORAGES:
            return None
        path = str(target.get("path") or "").strip()
        if not path:
            return None
        return {
            "pan_path": path,
            "pickcode": str(target.get("pickcode") or "").strip(),
            "size": int(target.get("size") or 0),
        }

    @staticmethod
    def _paths_from_scrape_event(event: Event) -> List[str]:
        data = event.event_data or {}
        raw_list = data.get("file_list") or []
        paths: List[str] = []
        for item in raw_list:
            if isinstance(item, str) and item.strip():
                paths.append(item.strip())
            elif isinstance(item, dict) and item.get("path"):
                paths.append(str(item["path"]))
        return paths

    def _schedule_batch_write(self, paths: List[str], source: str) -> None:
        with self._debounce_lock:
            self._pending_paths.update(paths)
            self._pending_source = source
            if self._batch_timer:
                self._batch_timer.cancel()
            self._batch_timer = Timer(
                float(self._batch_debounce_seconds),
                self._on_batch_fire,
            )
            self._batch_timer.daemon = True
            self._batch_timer.start()

    def _on_batch_fire(self) -> None:
        with self._debounce_lock:
            paths = list(self._pending_paths)
            self._pending_paths.clear()
            source = self._pending_source
        if not paths:
            return
        files = [{"pan_path": p} for p in paths]
        Thread(target=self._post_strm_write, args=(files, source), daemon=True).start()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_token}",
            "Content-Type": "application/json",
        }

    def _notify_strm_result(
        self, *, ok: bool, source: str, summary: str, detail: str = ""
    ) -> None:
        """
        直写 STRM 完成后向 MoviePilot 通知渠道推送结果（成功或失败均发送，需开启配置）
        """
        if not self._notify_on_strm_result:
            return
        title = "StrmHub STRM 生成成功" if ok else "StrmHub STRM 生成失败"
        text = summary
        if detail:
            text = f"{summary}\n{detail[:400]}"
        try:
            self.post_message(
                mtype=NotificationType.Plugin,
                title=title,
                text=text,
            )
        except Exception as exc:
            logger.warning(f"[StrmHubBridge] 发送 MP 通知失败: {exc}")

    def _apply_event_delay(self) -> None:
        delay = max(int(self._event_delay_seconds or 0), 0)
        if delay:
            logger.info(f"[StrmHubBridge] 事件延时 {delay}s 后调用 Webhook")
            sleep(delay)

    def _scrape_created_strms(
        self,
        data: dict,
        scrape_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        对本次新写入的 STRM 触发 MP 元数据刮削（仅写插件日志，不发通知）
        """
        if not self._scrape_metadata_after_strm:
            return
        context = scrape_context or {}
        mediainfo = context.get("mediainfo")
        meta = context.get("meta")
        results = data.get("results") or []
        ok_count = 0
        skip_count = 0
        for row in results:
            if row.get("outcome") != "created":
                continue
            strm_path = row.get("strm_path")
            if not strm_path:
                skip_count += 1
                continue
            scrape_path = map_strmhub_path_to_mp(strm_path, self._path_mappings)
            if scrape_path != strm_path:
                logger.info(
                    f"[StrmHubBridge] 路径映射: {strm_path} -> {scrape_path}"
                )
            try:
                if media_scrape_metadata(
                    path=scrape_path,
                    mediainfo=mediainfo,
                    meta=meta,
                    overwrite=self._scrape_overwrite,
                ):
                    ok_count += 1
                else:
                    skip_count += 1
            except Exception as exc:
                skip_count += 1
                logger.warning(
                    f"[StrmHubBridge] 刮削失败 {scrape_path}: {exc}"
                )
        if ok_count or skip_count:
            logger.info(
                f"[StrmHubBridge] 刮削汇总: 已触发={ok_count} 跳过/失败={skip_count}"
            )

    def _refresh_created_strms(
        self,
        data: dict,
        scrape_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        对本次新写入的 STRM 刷新 MP 已配置的媒体服务器
        """
        if not self._media_server_refresh_enabled:
            return
        context = scrape_context or {}
        mediainfo = context.get("mediainfo")
        results = data.get("results") or []
        refresh_paths: List[Tuple[str, str]] = []
        for row in results:
            if row.get("outcome") != "created":
                continue
            strm_path = row.get("strm_path")
            if not strm_path:
                continue
            mp_path = map_strmhub_path_to_mp(strm_path, self._path_mappings)
            refresh_paths.append((mp_path, Path(mp_path).name))
        if not refresh_paths:
            return
        helper = MediaServerRefresh(
            "[StrmHubBridge] ",
            enabled=True,
            mediaservers=self._mediaservers,
            mp_mediaserver_paths=self._mp_mediaserver_paths,
            delay_seconds=self._media_server_refresh_delay,
        )
        helper.refresh_batch(refresh_paths, mediainfo=mediainfo)

    def _post_strm_write(
        self,
        files: List[dict],
        source: str,
        scrape_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        base = (self._base_url or "").rstrip("/")
        if not base or not self._api_token:
            return
        self._apply_event_delay()
        url = f"{base}/api/hooks/strm/write"
        body = json.dumps(
            {"source": source, "files": files, "resolve_pickcode": True}
        ).encode("utf-8")
        last_error = ""
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(
                    url, data=body, headers=self._headers(), method="POST"
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    detail = resp.read().decode("utf-8", errors="replace")
                data = json.loads(detail) if detail else {}
                summary = (
                    f"写 STRM: 生成={data.get('created', 0)} "
                    f"跳过={data.get('skipped', 0)} 失败={data.get('failed', 0)}"
                )
                failed = int(data.get("failed") or 0)
                ok = failed == 0
                if failed > 0 and int(data.get("created") or 0) > 0:
                    summary = f"{summary}（部分失败）"
                self._last_status = summary
                self.save_data(
                    "last_trigger",
                    {
                        "status": "ok" if ok else "failed",
                        "source": source,
                        "summary": summary,
                        "detail": detail[:500],
                    },
                )
                logger.info(f"[StrmHubBridge] {summary}")
                self._scrape_created_strms(data, scrape_context)
                self._refresh_created_strms(data, scrape_context)
                self._notify_strm_result(
                    ok=ok,
                    source=source,
                    summary=summary,
                    detail=detail[:400],
                )
                return
            except Exception as exc:
                last_error = str(exc)
            if attempt < 3:
                sleep(2)
        self._last_status = f"直写失败: {last_error[:120]}"
        self.save_data(
            "last_trigger",
            {"status": "failed", "source": source, "detail": last_error},
        )
        logger.error(f"[StrmHubBridge] 直写 STRM 失败: {last_error}")
        self._notify_strm_result(
            ok=False,
            source=source,
            summary=self._last_status,
            detail=last_error,
        )

    def stop_service(self):
        with self._debounce_lock:
            if self._batch_timer:
                self._batch_timer.cancel()
            self._batch_timer = None
