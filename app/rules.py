"""Deterministic plans for unambiguous, low-cost CutPilot requests."""

from __future__ import annotations

import re
from typing import Any


_TIME = r"\d{1,2}(?:\.\d{2}){1,2}"
_RANGE = rf"({_TIME})\s*[-–]\s*({_TIME})"
_NOISE = re.compile(r"\b(?:сделай|сделать|обработай|обработать|сожми|сжать|обрез\w*|видео|файл|формат|разрешение|размер|до|примерно|и|в|на|по|для|мне|пожалуйста|без|наложи|наложить|склей|склеить|соедини|соединить|объедини|объединить|оставь|оставить|таймкод\w*|мегабайт\w*|гигабайт\w*|логотип\w*|лого|cut)\b", re.I)
_EDGE_CUT = re.compile(r"(?:вырежи|вырезать|обрезать|удали|удалить|убери|убрать|cut).*?(?:перв\w*|вначале|в\s+начале?)\s+(\d+)\s*(секунд\w*|минут\w*).*?(?:в\s*конц\w*|последн\w*)\s+(\d+)\s*(секунд\w*|минут\w*)", re.I)
_EDGE_CUT_ALT = re.compile(r"(?:вырежи|вырезать|обрезать|удали|удалить|убери|убрать|cut).*?вначале\s+(\d+)\s+(секунд\w*).*?(\d+)\s+(секунд\w*)\s+в\s+конце", re.I)


def simple_plan(task: str, duration_seconds: float | str | None = None) -> dict[str, Any] | None:
    """Return a plan only when every meaningful part of the request is known."""
    text = task.casefold().replace("ё", "е")
    commands: list[str] = []
    consumed = text

    edge_cut = _EDGE_CUT.search(text)
    if edge_cut is None:
        alternate = _EDGE_CUT_ALT.search(text)
        if alternate is not None:
            edge_cut = alternate
    if edge_cut:
        if duration_seconds is None:
            return None
        try:
            duration = int(float(duration_seconds))
            first = int(edge_cut.group(1)) * (60 if edge_cut.group(2).startswith("минут") else 1)
            last = int(edge_cut.group(3)) * (60 if edge_cut.group(4).startswith("минут") else 1)
        except (TypeError, ValueError):
            return None
        if duration <= first + last:
            return None
        def timestamp(value: int) -> str:
            hours, remainder = divmod(value, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}.{minutes:02d}.{seconds:02d}" if hours else f"{minutes:02d}.{seconds:02d}"
        commands.append(f"-crp-{timestamp(0)}-{timestamp(first)}+{timestamp(duration - last)}-{timestamp(duration)}")
        consumed = consumed[:edge_cut.start()] + " " + consumed[edge_cut.end():]

    if re.search(r"\b(?:mp4|mpeg[- ]?4)\b", text):
        commands.append("-mp4")
        consumed = re.sub(r"\b(?:mp4|mpeg[- ]?4)\b", " ", consumed)
    if re.search(r"\bmov\b", text):
        commands.append("-mov")
        consumed = re.sub(r"\bmov\b", " ", consumed)
    if re.search(r"\b(?:hevc|h\.265|h265)\b", text):
        commands.append("-hevc")
        consumed = re.sub(r"\b(?:hevc|h\.265|h265)\b", " ", consumed)

    for match in re.finditer(r"\b(360|480|720|1080)\s*p\b", text):
        commands.append(f"-{match.group(1)}p")
        consumed = consumed.replace(match.group(0), " ")
    for match in re.finditer(r"\b([1-5]?\d|60)\s*(?:fps|кадр(?:а|ов)?\s*/\s*с)\b", text):
        commands.append(f"-{match.group(1)}fps")
        consumed = consumed.replace(match.group(0), " ")
    for match in re.finditer(r"\b([1-9]\d*)\s*(mb|gb|мб|гб|мегабайт\w*|гигабайт\w*)\b", text):
        unit = "gb" if match.group(2).startswith(("gb", "гб", "гигабайт")) else "mb"
        commands.append(f"-{match.group(1)}{unit}")
        consumed = consumed.replace(match.group(0), " ")

    negative_logo = bool(re.search(r"(?:без|убер(?:и|ать)|удал(?:и|ить)|remove)\s+(?:логотип\w*|лого)", text))
    positive_logo = bool(re.search(r"(?:с|остав(?:ь|ить)|добав(?:ь|ить)|with)\s+(?:логотип\w*|лого)", text))
    overlay_logo = bool(re.search(r"(?:наложи|наложить|нанеси|нанести|поставь|поставить)\s+(?:логотип\w*|лого)", text))
    if negative_logo:
        commands.append("-nologo")
        consumed = re.sub(r"(?:без|убер(?:и|ать)|удал(?:и|ить)|remove)\s+(?:логотип\w*|лого)", " ", consumed)
    elif overlay_logo:
        commands.append("-nl")
        consumed = re.sub(r"(?:наложи|наложить|нанеси|нанести|поставь|поставить)\s+(?:логотип\w*|лого)", " ", consumed)
    elif positive_logo:
        consumed = re.sub(r"(?:с|остав(?:ь|ить)|добав(?:ь|ить)|with)\s+(?:логотип\w*|лого)", " ", consumed)

    ranges = list(re.finditer(_RANGE, text))
    if ranges:
        if re.search(r"(?:склей|соедини|объедини|concat)", text):
            command = "-crp+" + "+".join(f"{m.group(1)}-{m.group(2)}" for m in ranges)
        elif re.search(r"(?:оставь|оставить|только|keep)", text):
            if len(ranges) > 1:
                return None
            command = f"-crp={ranges[0].group(1)}-{ranges[0].group(2)}"
        elif re.search(r"(?:удали|удалить|вырежи|вырезать|обрезать|убери|убрать|remove|cut)", text):
            command = "-crp-" + "+".join(f"{m.group(1)}-{m.group(2)}" for m in ranges)
        else:
            return None
        commands.append(command)
        consumed = re.sub(_RANGE, " ", consumed)

    consumed = _NOISE.sub(" ", consumed)
    consumed = consumed.replace("без", " ")
    consumed = re.sub(r"[\s,.;:!?+–-]+", "", consumed)
    if consumed:
        return None
    return {"commands": commands, "summary": "Локальный план без запроса к AI"}
