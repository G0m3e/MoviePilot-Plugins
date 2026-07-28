"""
写 STRM / 刮削后刷新 MoviePilot 已配置的媒体服务器
"""

from __future__ import annotations

from pathlib import Path
from threading import Timer
from typing import Dict, List, Optional

from app.chain.media import MediaChain
from app.core.metainfo import MetaInfoPath
from app.helper.mediaserver import MediaServerHelper
from app.log import logger
from app.schemas import RefreshMediaItem, ServiceInfo
from app.utils.http import RequestUtils

from .paths import map_mp_path_to_mediaserver


class _EmbyOperate:
    """Emby 按路径触发刷新"""

    def __init__(self, func_name: str):
        self.func_name = func_name
        self._helper = MediaServerHelper()

    def _get_emby_info(self, name: str) -> tuple[str, str, str]:
        emby_server = self._helper.get_service(name=name, type_filter="emby")
        emby_user = emby_server.instance.get_user()
        config = emby_server.config.config
        emby_apikey = config.get("apikey")
        emby_host = config.get("host") or ""
        if not emby_host:
            return "", "", ""
        if not emby_host.endswith("/"):
            emby_host += "/"
        if not emby_host.startswith("http"):
            emby_host = "http://" + emby_host
        return emby_host, emby_user, emby_apikey

    def _get_item_id_by_path(self, name: str, path: str) -> Optional[str]:
        emby_host, _, emby_apikey = self._get_emby_info(name)
        if not emby_host:
            return None
        req_url = f"{emby_host}emby/Items"
        params = {
            "Path": path,
            "Recursive": "true",
            "Fields": "Path",
            "IncludeItemTypes": "Movie,Episode,Folder,Series",
            "api_key": emby_apikey,
        }
        try:
            with RequestUtils().get_res(url=req_url, params=params) as res:
                if not res:
                    return None
                for item in res.json().get("Items", []):
                    if item.get("Path") == path:
                        return item.get("Id")
        except Exception as exc:
            logger.error(
                f"{self.func_name}获取 Emby 项目 Id 异常 name={name!r} path={path!r}: {exc}"
            )
        return None

    def trigger_refresh_by_path(self, name: str, path: str) -> bool:
        path_obj = Path(path)
        for parent in path_obj.parents:
            if len(parent.parts) <= 1:
                break
            item_id = self._get_item_id_by_path(name, parent.as_posix())
            if not item_id:
                continue
            return self._trigger_refresh_by_id(name, item_id)
        return False

    def _trigger_refresh_by_id(self, name: str, item_id: str) -> bool:
        emby_host, _, emby_apikey = self._get_emby_info(name)
        if not emby_host:
            return False
        req_url = f"{emby_host}emby/Items/{item_id}/Refresh"
        params = {
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
            "api_key": emby_apikey,
        }
        try:
            with RequestUtils().post_res(url=req_url, params=params) as res:
                return bool(res and res.status_code in {200, 204})
        except Exception as exc:
            logger.error(
                f"{self.func_name}触发 Emby 刷新异常 name={name!r} item_id={item_id!r}: {exc}"
            )
            return False


class MediaServerRefresh:
    """媒体服务器刷新（Emby / Jellyfin 等）"""

    def __init__(
        self,
        func_name: str,
        *,
        enabled: bool = False,
        mediaservers: Optional[List[str]] = None,
        mp_mediaserver_paths: Optional[str] = None,
        delay_seconds: int = 0,
    ):
        self.func_name = func_name
        self.enabled = enabled
        self.media_servers = mediaservers or []
        self.mp_mediaserver_paths = mp_mediaserver_paths or ""
        self.delay_seconds = max(0, int(delay_seconds or 0))
        self._helper = MediaServerHelper()

    @property
    def service_infos(self) -> Optional[Dict[str, ServiceInfo]]:
        if not self.media_servers:
            logger.warning(f"{self.func_name}尚未配置媒体服务器，请检查配置")
            return None
        services = self._helper.get_services(name_filters=self.media_servers)
        if not services:
            logger.warning(f"{self.func_name}获取媒体服务器实例失败，请检查配置")
            return None
        active = {
            name: info
            for name, info in services.items()
            if not info.instance.is_inactive()
        }
        if not active:
            logger.warning(f"{self.func_name}没有已连接的媒体服务器，请检查配置")
            return None
        for name in services:
            if name not in active:
                logger.warning(f"{self.func_name}媒体服务器 {name} 未连接，已跳过")
        return active or None

    def refresh_mediaserver(
        self,
        file_path: Optional[str] = None,
        file_name: Optional[str] = None,
        mediainfo=None,
    ) -> bool:
        if not self.enabled:
            return True
        if not self.service_infos:
            return False
        if self.delay_seconds > 0:
            logger.info(
                f"{self.func_name}{file_name} 延迟 {self.delay_seconds} 秒后刷新媒体服务器"
            )
            Timer(
                self.delay_seconds,
                self._do_refresh,
                args=(file_path, file_name, mediainfo),
            ).start()
            return True
        return self._do_refresh(file_path, file_name, mediainfo)

    def refresh_batch(
        self,
        paths: List[tuple[str, str]],
        mediainfo=None,
    ) -> None:
        """
        批量刷新；若配置了延迟，整批延迟后依次刷新
        """
        if not self.enabled or not paths:
            return

        def do_batch() -> None:
            for file_path, file_name in paths:
                self._do_refresh(file_path, file_name, mediainfo)

        if self.delay_seconds > 0:
            logger.info(
                f"{self.func_name} 延迟 {self.delay_seconds} 秒后刷新 {len(paths)} 个路径"
            )
            Timer(self.delay_seconds, do_batch).start()
        else:
            do_batch()

    def _do_refresh(
        self,
        file_path: Optional[str] = None,
        file_name: Optional[str] = None,
        mediainfo=None,
    ) -> bool:
        logger.info(f"{self.func_name}{file_name} 开始刷新媒体服务器")
        services = self.service_infos
        if not services:
            return False

        refresh_path = file_path or ""
        if self.mp_mediaserver_paths and refresh_path:
            mapped = map_mp_path_to_mediaserver(
                refresh_path, self.mp_mediaserver_paths
            )
            if mapped != refresh_path:
                logger.info(
                    f"{self.func_name}刷新媒体库路径: {refresh_path} -> {mapped}"
                )
                refresh_path = mapped

        if not mediainfo and refresh_path:
            media_chain = MediaChain()
            meta = MetaInfoPath(path=Path(refresh_path))
            mediainfo = media_chain.recognize_media(meta=meta)

        if not mediainfo:
            emby_services = {
                name: service
                for name, service in services.items()
                if service.type == "emby"
            }
            if emby_services and refresh_path:
                emby_operate = _EmbyOperate(self.func_name)
                ok = False
                for name in emby_services:
                    if emby_operate.trigger_refresh_by_path(name, refresh_path):
                        logger.info(f"{self.func_name}{file_name} Emby 刷新成功")
                        ok = True
                    else:
                        logger.warning(f"{self.func_name}{file_name} Emby 刷新失败")
                return ok
            logger.warning(f"{self.func_name}{file_name} 无法识别媒体信息，跳过刷新")
            return False

        items = [
            RefreshMediaItem(
                title=mediainfo.title,
                year=mediainfo.year,
                type=mediainfo.type,
                category=mediainfo.category,
                target_path=Path(refresh_path or file_path or "."),
            )
        ]
        for name, service in services.items():
            if hasattr(service.instance, "refresh_library_by_items"):
                service.instance.refresh_library_by_items(items)
            else:
                logger.warning(f"{self.func_name}{file_name} {name} 不支持按条目刷新")
        return True
