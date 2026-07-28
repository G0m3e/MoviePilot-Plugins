"""
STRM 写入后触发 MoviePilot 元数据刮削
"""

from __future__ import annotations

from pathlib import Path

from app.chain.media import MediaChain
from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfoPath
from app.log import logger
from app.schemas import FileItem
from app.schemas.types import EventType, MediaType


def media_scrape_metadata(
    path: str | Path,
    item_name: str = "",
    mediainfo: MediaInfo | None = None,
    meta: MetaBase | None = None,
    overwrite: bool = True,
) -> bool:
    """
    对本地 STRM 路径触发 MP 元数据刮削（发送 MetadataScrape 事件）
    :return: 是否已发起刮削
    """
    path = Path(path)
    item_name = item_name or path.name
    mediachain = MediaChain()
    logger.info(f"[StrmHubBridge] 【媒体刮削】{item_name} 开始刮削元数据")
    if mediainfo:
        if mediainfo.type == MediaType.MOVIE:
            dir_path = path.parent
            fileitem = FileItem(
                storage="local",
                type="dir",
                path=dir_path.as_posix(),
                name=dir_path.name,
                basename=dir_path.stem,
                modify_time=dir_path.stat().st_mtime,
            )
        else:
            rename_format_level = len(settings.TV_RENAME_FORMAT.split("/")) - 1
            if rename_format_level < 1:
                fileitem = FileItem(
                    storage="local",
                    type="file",
                    path=path.as_posix(),
                    name=path.name,
                    basename=path.stem,
                    extension=path.suffix[1:].lower(),
                    size=path.stat().st_size,
                    modify_time=path.stat().st_mtime,
                )
            else:
                dir_path = Path(path.parents[rename_format_level - 1])
                fileitem = FileItem(
                    storage="local",
                    type="dir",
                    path=dir_path.as_posix(),
                    name=dir_path.name,
                    basename=dir_path.stem,
                    modify_time=dir_path.stat().st_mtime,
                )
        eventmanager.send_event(
            EventType.MetadataScrape,
            {
                "meta": meta,
                "mediainfo": mediainfo,
                "fileitem": fileitem,
                "overwrite": overwrite,
            },
        )
    else:
        meta = MetaInfoPath(path)
        mediainfo = mediachain.recognize_by_meta(meta)
        if not meta or not mediainfo:
            logger.info(f"[StrmHubBridge] 【媒体刮削】{item_name} 无法识别媒体信息，跳过")
            return False
        file_type = "dir"
        dir_path = path.parent
        tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(dir_path))
        if tem_mediainfo and tem_mediainfo.imdb_id == mediainfo.imdb_id:
            if mediainfo.type == MediaType.TV:
                dir_path = dir_path.parent
                tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(dir_path))
                if tem_mediainfo and tem_mediainfo.imdb_id == mediainfo.imdb_id:
                    finish_path = dir_path
                else:
                    logger.warning(
                        f"[StrmHubBridge] 【媒体刮削】{dir_path} 无法识别剧集媒体信息，使用上级目录"
                    )
                    finish_path = path.parent
            else:
                finish_path = dir_path
        else:
            logger.warning(
                f"[StrmHubBridge] 【媒体刮削】{dir_path} 无法识别上级媒体信息，使用文件路径"
            )
            finish_path = path
            file_type = "file"
        fileitem = FileItem(
            storage="local",
            type=file_type,
            path=str(finish_path),
            name=finish_path.name,
            basename=finish_path.stem,
            modify_time=finish_path.stat().st_mtime,
        )
        eventmanager.send_event(
            EventType.MetadataScrape,
            {
                "meta": meta,
                "mediainfo": mediainfo,
                "fileitem": fileitem,
                "overwrite": overwrite,
            },
        )

    logger.info(f"[StrmHubBridge] 【媒体刮削】{item_name} 刮削元数据完成")
    return True
