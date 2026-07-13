"""
StrmHub 联动：MoviePilot 整理完成后直接写 STRM 或触发增量同步
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from threading import Lock, Thread, Timer
from time import sleep
from typing import Any, Dict, List, Optional, Set, Tuple

from app import schemas
from app.core.config import settings
from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import NotificationType
from app.schemas import Event
from app.schemas.types import EventType
from app.schemas.workflow import ActionContext

_ALLOWED_STORAGES = frozenset({"u115", "115网盘Plus"})


class StrmHubBridge(_PluginBase):
    """
    MoviePilot 整理完成后调用 StrmHub 写 STRM 或触发增量
    """

    plugin_name = "StrmHub 联动"
    plugin_desc = "整理完成后调用 StrmHub 直接写 STRM（推荐）或触发增量同步"
    plugin_icon = "https://raw.githubusercontent.com/G0m3e/MoviePilot-Plugins/main/icons/strmhub.png?v=2"
    plugin_version = "1.2.8"
    plugin_author = "G0m3e"
    author_url = "https://github.com/G0m3e/StrmHub"
    plugin_config_prefix = "strmhubbridge_"
    plugin_order = 120
    auth_level = 1

    _enabled = False
    _base_url = ""
    _api_token = ""
    _sync_mode = "direct"
    _debounce_seconds = 30
    _batch_debounce_seconds = 5
    _event_delay_seconds = 10
    _listen_metadata_scrape = False
    _listen_transfer_complete = True
    _notify_on_strm_result = False
    _last_status = "尚未触发"
    _debounce_timer: Optional[Timer] = None
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
        mode = (config.get("sync_mode") or "direct").strip().lower()
        self._sync_mode = mode if mode in {"direct", "increment"} else "direct"
        self._debounce_seconds = max(int(config.get("debounce_seconds") or 30), 5)
        self._batch_debounce_seconds = max(int(config.get("batch_debounce_seconds") or 5), 1)
        delay_raw = config.get("event_delay_seconds")
        self._event_delay_seconds = max(
            int(delay_raw if delay_raw is not None else 10), 0
        )
        self._listen_metadata_scrape = bool(config.get("listen_metadata_scrape", False))
        self._listen_transfer_complete = bool(config.get("listen_transfer_complete", True))
        self._notify_on_strm_result = bool(config.get("notify_on_strm_result", False))
        if self._sync_mode == "increment":
            self._listen_metadata_scrape = bool(
                config.get("listen_metadata_scrape", True)
            )
            self._listen_transfer_complete = bool(
                config.get("listen_transfer_complete", False)
            )
        saved = self.get_data("last_trigger") or {}
        if saved.get("status") == "ok":
            self._last_status = saved.get("summary") or f"最近成功 ({saved.get('source', '')})"
        elif saved.get("status") == "skipped":
            self._last_status = "最近跳过 (409)"
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

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        配置页
        """
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
                                            "model": "sync_mode",
                                            "label": "同步模式",
                                            "items": [
                                                {"title": "直接写 STRM（推荐）", "value": "direct"},
                                                {"title": "触发生活事件增量", "value": "increment"},
                                            ],
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
                                        "component": "VSwitch",
                                        "props": {
                                            "model": "listen_transfer_complete",
                                            "label": "监听 transfer.complete（单文件整理完成）",
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
                                            "model": "listen_metadata_scrape",
                                            "label": "监听 metadata.scrape（整批刮削完成）",
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
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "event_delay_seconds",
                                            "label": "事件延时（秒）",
                                            "type": "number",
                                            "placeholder": "收到 MP 事件后延迟再调 Webhook，默认 10",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "debounce_seconds",
                                            "label": "增量去抖（秒）",
                                            "type": "number",
                                            "placeholder": "增量模式合并触发，默认 30",
                                        },
                                    }
                                ],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [
                                    {
                                        "component": "VTextField",
                                        "props": {
                                            "model": "batch_debounce_seconds",
                                            "label": "批写入去抖（秒）",
                                            "type": "number",
                                            "placeholder": "直写批路径合并，默认 5",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "info",
                            "variant": "tonal",
                            "text": "iOS 请用主屏幕 PWA 打开；测试通知请点右下角「查看数据」。",
                        },
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "text": "勿与其他 STRM 写入插件对同一目录双开；StrmHub 侧需已配置目录映射与 115 Cookie",
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "base_url": "",
            "api_token": "",
            "show_api_token": True,
            "sync_mode": "direct",
            "notify_on_strm_result": False,
            "listen_transfer_complete": True,
            "listen_metadata_scrape": False,
            "debounce_seconds": 30,
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

    def get_actions(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": "trigger_increment",
                "action_id": "trigger_increment",
                "name": "触发 StrmHub 增量",
                "func": self.run_workflow_action,
                "kwargs": {"source": "mp.action"},
            }
        ]

    def run_workflow_action(
        self, context: ActionContext, source: str = "mp.action", **_
    ) -> Tuple[bool, ActionContext]:
        if not self.get_state():
            return False, context
        if self._sync_mode == "direct":
            logger.warning("[StrmHubBridge] 工作流动作在直写模式下请使用整理事件触发")
            return False, context
        self._schedule_increment(source)
        return True, context

    @eventmanager.register(EventType.MetadataScrape)
    def on_metadata_scrape(self, event: Event):
        if not self.get_state() or not self._listen_metadata_scrape:
            return
        paths = self._paths_from_scrape_event(event)
        if not paths:
            return
        if self._sync_mode == "direct":
            self._schedule_batch_write(paths, "mp.metadata_scrape")
        else:
            self._schedule_increment("mp.metadata_scrape")

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        if not self.get_state() or not self._listen_transfer_complete:
            return
        file_item = self._file_from_transfer_event(event)
        if not file_item:
            return
        if self._sync_mode == "direct":
            Thread(
                target=self._post_strm_write,
                args=([file_item], "mp.transfer_complete"),
                daemon=True,
            ).start()
        else:
            self._schedule_increment("mp.transfer_complete")

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

    def _schedule_increment(self, source: str) -> None:
        with self._debounce_lock:
            self._pending_source = source
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = Timer(
                float(self._debounce_seconds),
                self._on_increment_fire,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()
        logger.info(
            f"[StrmHubBridge] 已调度 StrmHub 增量 ({source})，{self._debounce_seconds}s 后执行"
        )

    def _on_increment_fire(self) -> None:
        source = self._pending_source
        Thread(target=self._post_increment, args=(source,), daemon=True).start()

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

    def _post_strm_write(self, files: List[dict], source: str) -> None:
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

    def _post_increment(self, source: str) -> None:
        base = (self._base_url or "").rstrip("/")
        if not base or not self._api_token:
            return
        self._apply_event_delay()
        url = f"{base}/api/hooks/increment"
        body = json.dumps({"source": source}).encode("utf-8")
        last_error = ""
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(
                    url, data=body, headers=self._headers(), method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                self._last_status = f"增量触发成功 ({source})"
                self.save_data(
                    "last_trigger",
                    {"status": "ok", "source": source, "detail": detail},
                )
                logger.info(f"[StrmHubBridge] 已触发 StrmHub 增量: {source}")
                return
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code == 409:
                    self._last_status = "跳过 (409 已有任务)"
                    self.save_data(
                        "last_trigger",
                        {"status": "skipped", "source": source, "detail": detail},
                    )
                    return
                last_error = f"HTTP {exc.code}: {detail}"
            except Exception as exc:
                last_error = str(exc)
            if attempt < 3:
                sleep(3)
        self._last_status = f"增量失败: {last_error[:120]}"
        self.save_data(
            "last_trigger",
            {"status": "failed", "source": source, "detail": last_error},
        )
        logger.error(f"[StrmHubBridge] 触发增量失败: {last_error}")

    def stop_service(self):
        with self._debounce_lock:
            for timer in (self._debounce_timer, self._batch_timer):
                if timer:
                    timer.cancel()
            self._debounce_timer = None
            self._batch_timer = None
