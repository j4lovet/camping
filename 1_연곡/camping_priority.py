"""Validate and expand user-selected ranges while preserving priority order."""
import re


def parse_ranges(text, limits):
    text = re.sub(r'\s*([~\-])\s*', r'\1', text.upper())
    tokens = text.replace(',', ' ').split()
    canonical = []
    for token in tokens:
        match = re.fullmatch(r'([A-Z]?)(\d{1,3})[~\-]([A-Z]?)(\d{1,3})', token)
        if not match:
            raise ValueError(f"범위 형식을 확인하세요: {token} (예: B05~B12, A130~A145)")
        candidates = [zone for zone, (low, high, _) in limits.items() if low <= int(match[2]) <= high]
        zone = match[1] or (candidates[0] if len(candidates) == 1 else '')
        if zone not in limits or (match[3] and match[3] != zone):
            raise ValueError(f"같은 구역 안에서 범위를 지정하세요: {token}")
        low, high, width = limits[zone]
        start, end = int(match[2]), int(match[4])
        if not low <= start <= end <= high:
            raise ValueError(f"범위를 확인하세요: {token} ({zone}{low:0{width}d}~{zone}{high:0{width}d})")
        canonical.append(f'{zone}{start:0{width}d}~{zone}{end:0{width}d}')
    if not canonical:
        raise ValueError('범위를 한 개 이상 입력하세요')
    return list(dict.fromkeys(canonical))


def expand_ranges(ranges, limits):
    if not isinstance(ranges, list) or any(not isinstance(value, str) for value in ranges):
        raise ValueError('site_ranges는 범위 문자열 목록이어야 합니다')
    if not ranges:
        return []
    result = []
    for value in ranges:
        for item in parse_ranges(value, limits):
            zone, start, end = re.fullmatch(r'([A-Z])(\d+)~[A-Z](\d+)', item).groups()
            width = limits[zone][2]
            result.extend(f'{zone}{number:0{width}d}' for number in range(int(start), int(end) + 1))
    return list(dict.fromkeys(result))
