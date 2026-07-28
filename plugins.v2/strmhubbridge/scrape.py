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


def _build_fileitem(path: Path, mediainfo: MediaInfo | None) -> FileItem:
    if mediainfo and mediainfo.type == MediaType.MOVIE:
        dir_path = path.parent
        return FileItem(
            storage="local",
            type="dir",
            path=dir_path.as_posix(),
            name=dir_path.name,
            basename=dir_path.stem,
            modify_time=dir_path.stat().st_mtime,
        )

    if mediainfo and mediainfo.type == MediaType.TV:
        rename_format_level = len(settings.TV_RENAME_FORMAT.split("/")) - 1
        if rename_format_level < 1:
            return FileItem(
                storage="local",
                type="file",
                path=path.as_posix(),
                name=path.name,
                basename=path.stem,
                extension=path.suffix[1:].lower(),
                size=path.stat().st_size,
                modify_time=path.stat().st_mtime,
            )
        dir_path = Path(path.parents[rename_format_level - 1])
        return FileItem(
            storage="local",
            type="dir",
            path=dir_path.as_posix(),
            name=dir_path.name,
            basename=dir_path.stem,
            modify_time=dir_path.stat().st_mtime,
        )

    file_type = "dir"
    dir_path = path.parent
    finish_path = dir_path
    meta = MetaInfoPath(path)
    mediachain = MediaChain()
    recognized = mediachain.recognize_by_meta(meta)
    if recognized:
        tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(dir_path))
        if tem_mediainfo and tem_mediainfo.imdb_id == recognized.imdb_id:
            if recognized.type == MediaType.TV:
                parent_path = dir_path.parent
                tem_mediainfo = mediachain.recognize_by_meta(MetaInfoPath(parent_path))
                if tem_mediainfo and tem_mediainfo.imdb_id == recognized.imdb_id:
                    finish_path = parent_path
                else:
                    logger.warning(
                        f"[StrmHubBridge] 【媒体刮削】{parent_path} 无法识别剧集媒体信息，使用上级目录"
                    )
                    finish_path = dir_path
            else:
                finish_path = dir_path
        else:
            logger.warning(
                f"[StrmHubBridge] 【媒体刮削】{dir_path} 无法识别上级媒体信息，使用文件路径"
            )
            finish_path = path
            file_type = "file"

    return FileItem(
        storage="local",
        type=file_type,
        path=str(finish_path),
        name=finish_path.name,
        basename=finish_path.stem,
        modify_time=finish_path.stat().st_mtime,
    )


def _refresh_scrape_context(
    mediachain: MediaChain,
    *,
    path: Path,
    fileitem: FileItem,
    meta: MetaBase | None,
    mediainfo: MediaInfo | None,
) -> tuple[MetaBase | None, MediaInfo | None]:
    """
    与 MP 手动刮削一致：按目录重新识别并拉取图片元数据
    """
    recognize_path = fileitem.path if fileitem.type == "dir" else str(path.parent)
    try:
        context = mediachain.recognize_by_path(recognize_path, obtain_images=True)
    except TypeError:
        context = mediachain.recognize_by_path(recognize_path)
        if context and context.media_info:
            mediachain.obtain_images(mediainfo=context.media_info)

    if context and context.media_info:
        return context.meta_info or meta, context.media_info

    if mediainfo:
        mediachain.obtain_images(mediainfo=mediainfo)
        return meta, mediainfo

    meta = meta or MetaInfoPath(path)
    mediainfo = mediachain.recognize_by_meta(meta)
    return meta, mediainfo


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

    fileitem = _build_fileitem(path, mediainfo)
    meta, mediainfo = _refresh_scrape_context(
        mediachain,
        path=path,
        fileitem=fileitem,
        meta=meta,
        mediainfo=mediainfo,
    )
    if not meta or not mediainfo:
        logger.info(f"[StrmHubBridge] 【媒体刮削】{item_name} 无法识别媒体信息，跳过")
        return False

    event_data = {
        "meta": meta,
        "mediainfo": mediainfo,
        "fileitem": fileitem,
        "overwrite": overwrite,
    }
    if fileitem.type == "dir":
        event_data["file_list"] = [path.as_posix()]

    eventmanager.send_event(EventType.MetadataScrape, event_data)
    logger.info(f"[StrmHubBridge] 【媒体刮削】{item_name} 刮削元数据完成")
    return True
