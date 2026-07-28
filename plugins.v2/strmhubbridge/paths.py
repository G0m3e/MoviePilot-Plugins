"""
Webhook 返回的 StrmHub 容器路径 → MoviePilot 容器路径
"""

from __future__ import annotations


def _norm_prefix(path: str) -> str:
    p = (path or "").strip().replace("\\", "/").rstrip("/")
    return p or "/"


def parse_path_mappings(text: str) -> list[tuple[str, str]]:
    """
    解析配置：MoviePilot目录:StrmHub目录（每行一条）
  :return: [(mp_prefix, strmhub_prefix), ...]
    """
    mappings: list[tuple[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        mp_part, sh_part = line.split(":", 1)
        mp_prefix = _norm_prefix(mp_part)
        sh_prefix = _norm_prefix(sh_part)
        if mp_prefix and sh_prefix:
            mappings.append((mp_prefix, sh_prefix))
    return mappings


def map_strmhub_path_to_mp(path: str, mappings: list[tuple[str, str]]) -> str:
    """
    将 Webhook 返回的 StrmHub 路径转换为 MP 可访问路径
    """
    if not path or not mappings:
        return path
    norm = path.replace("\\", "/")
    for mp_prefix, sh_prefix in sorted(mappings, key=lambda item: len(item[1]), reverse=True):
        if norm == sh_prefix:
            return mp_prefix
        prefix = sh_prefix + "/"
        if norm.startswith(prefix):
            return mp_prefix + norm[len(sh_prefix) :]
    return path
