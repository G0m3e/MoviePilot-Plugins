"""
StrmHub 联动：监听 MoviePilot 整理完成事件，触发 StrmHub 增量同步
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from threading import Lock, Thread, Timer
from time import sleep
from typing import Any, Dict, List, Optional, Tuple

from app.core.event import eventmanager
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import Event
from app.schemas.types import EventType
from app.schemas.workflow import ActionContext


class StrmHubBridge(_PluginBase):
    """
    MoviePilot 整理完成后通知 StrmHub 拉取 115 生活事件并生成 STRM
    """

    plugin_name = "StrmHub 联动"
    plugin_desc = "监听整理完成事件，调用 StrmHub 增量同步 API 自动生成 STRM"
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/cloud.png"
    plugin_version = "1.0.0"
    plugin_author = "G0m3e"
    author_url = "https://github.com/G0m3e/StrmHub"
    plugin_config_prefix = "strmhubbridge_"
    plugin_order = 120
    auth_level = 1

    _enabled = False
    _base_url = ""
    _api_token = ""
    _debounce_seconds = 30
    _event_delay_seconds = 8
    _listen_metadata_scrape = True
    _listen_transfer_complete = False
    _last_status = "尚未触发"
    _debounce_timer: Optional[Timer] = None
    _debounce_lock = Lock()
    _pending_source = "mp.metadata_scrape"

    def init_plugin(self, config: dict = None):
        """
        加载配置并生效
        """
        config = config or {}
        self._enabled = bool(config.get("enabled"))
        self._base_url = (config.get("base_url") or "").strip().rstrip("/")
        self._api_token = (config.get("api_token") or "").strip()
        self._debounce_seconds = max(int(config.get("debounce_seconds") or 30), 5)
        self._event_delay_seconds = max(int(config.get("event_delay_seconds") or 8), 0)
        self._listen_metadata_scrape = bool(config.get("listen_metadata_scrape", True))
        self._listen_transfer_complete = bool(config.get("listen_transfer_complete", False))
        saved = self.get_data("last_trigger") or {}
        if saved.get("status") == "ok":
            self._last_status = f"最近成功 ({saved.get('source', '')})"
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
        return []

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
                                        "props": {
                                            "model": "enabled",
                                            "label": "启用联动",
                                        },
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
                                            "placeholder": "http://192.168.0.36:8080",
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
                                            "type": "password",
                                            "placeholder": "与 StrmHub STRMHUB_WEBHOOK_SECRET 或管理员密码一致",
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
                                            "model": "debounce_seconds",
                                            "label": "去抖秒数",
                                            "type": "number",
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
                                            "model": "event_delay_seconds",
                                            "label": "触发前等待秒数",
                                            "type": "number",
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
                                            "model": "listen_metadata_scrape",
                                            "label": "监听 metadata.scrape（推荐）",
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
                                            "model": "listen_transfer_complete",
                                            "label": "监听 transfer.complete（单文件，易频繁）",
                                        },
                                    }
                                ],
                            },
                        ],
                    },
                    {
                        "component": "VAlert",
                        "props": {
                            "type": "warning",
                            "variant": "tonal",
                            "text": "勿与 p115strmhelper transfer_monitor 同目录双开；StrmHub 侧需已配置监控目录与 115 Cookie",
                        },
                    },
                ],
            }
        ], {
            "enabled": False,
            "base_url": "",
            "api_token": "",
            "debounce_seconds": 30,
            "event_delay_seconds": 8,
            "listen_metadata_scrape": True,
            "listen_transfer_complete": False,
        }

    def get_page(self) -> List[dict]:
        """
        详情页
        """
        saved = self.get_data("last_trigger") or {}
        detail = (saved.get("detail") or "")[:200]
        text = self._last_status
        if detail:
            text = f"{text}\n{detail}"
        return [
            {
                "component": "VAlert",
                "props": {
                    "type": "info",
                    "variant": "tonal",
                    "text": text,
                },
            },
        ]

    def get_actions(self) -> List[Dict[str, Any]]:
        """
        工作流可调用动作
        """
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
        """
        工作流动作：调度一次 StrmHub 增量
        """
        if not self.get_state():
            logger.warning("[StrmHubBridge] 插件未启用或配置不完整，跳过工作流动作")
            return False, context
        self._schedule_trigger(source)
        return True, context

    @eventmanager.register(EventType.MetadataScrape)
    def on_metadata_scrape(self, event: Event):
        """
        整批整理刮削完成（推荐触发点）
        """
        if not self.get_state() or not self._listen_metadata_scrape:
            return
        self._schedule_trigger("mp.metadata_scrape")

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event):
        """
        单文件整理完成（默认关闭，避免频繁触发）
        """
        if not self.get_state() or not self._listen_transfer_complete:
            return
        self._schedule_trigger("mp.transfer_complete")

    def _schedule_trigger(self, source: str) -> None:
        """
        去抖后异步调用 StrmHub
        """
        with self._debounce_lock:
            self._pending_source = source
            if self._debounce_timer:
                self._debounce_timer.cancel()
            self._debounce_timer = Timer(
                float(self._debounce_seconds),
                self._on_debounce_fire,
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()
        logger.info(
            f"[StrmHubBridge] 已调度 StrmHub 增量 ({source})，{self._debounce_seconds}s 后执行"
        )

    def _on_debounce_fire(self) -> None:
        source = self._pending_source
        Thread(target=self._call_strmhub, args=(source,), daemon=True).start()

    def _call_strmhub(self, source: str) -> None:
        """
        调用 StrmHub Webhook
        """
        base = (self._base_url or "").rstrip("/")
        token = (self._api_token or "").strip()
        if not base or not token:
            logger.warning("[StrmHubBridge] 未配置 base_url 或 api_token")
            return

        delay = max(int(self._event_delay_seconds or 0), 0)
        if delay:
            sleep(delay)

        url = f"{base}/api/hooks/increment"
        body = json.dumps({"source": source}).encode("utf-8")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        last_error = ""
        for attempt in range(1, 4):
            try:
                req = urllib.request.Request(url, data=body, headers=headers, method="POST")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    detail = resp.read().decode("utf-8", errors="replace")[:500]
                self._last_status = f"成功 ({source})"
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
                    logger.info("[StrmHubBridge] StrmHub 已有同步任务运行，跳过")
                    return
                last_error = f"HTTP {exc.code}: {detail}"
            except Exception as exc:
                last_error = str(exc)
            if attempt < 3:
                sleep(3)

        self._last_status = f"失败: {last_error[:120]}"
        self.save_data(
            "last_trigger",
            {"status": "failed", "source": source, "detail": last_error},
        )
        logger.error(f"[StrmHubBridge] 触发 StrmHub 失败: {last_error}")

    def stop_service(self):
        """
        停止去抖定时器
        """
        with self._debounce_lock:
            if self._debounce_timer:
                self._debounce_timer.cancel()
                self._debounce_timer = None
