import math
from typing import Any

def largest_triangle_three_buckets(data: list[dict[str, Any]], threshold: int, x_key: str = "time", y_key: str = "close") -> list[dict[str, Any]]:
    """
    Downsample a time-series dataset using the Largest Triangle Three Buckets (LTTB) algorithm.
    :param data: List of dicts, typically OHLCV data.
    :param threshold: The target number of data points.
    :param x_key: The dictionary key for the X axis (e.g., 'time').
    :param y_key: The dictionary key for the Y axis (e.g., 'close').
    :return: A downsampled list of dicts.
    """
    if threshold >= len(data) or threshold <= 2:
        return data

    sampled = []
    
    # Bucket size. Leave room for start and end data points.
    every = (len(data) - 2) / (threshold - 2)
    
    a = 0
    next_a = 0

    sampled.append(data[a])  # Always include the first point

    for i in range(0, threshold - 2):
        # Calculate bucket ranges
        avg_x = 0
        avg_y = 0
        avg_range_start = math.floor((i + 1) * every) + 1
        avg_range_end = math.floor((i + 2) * every) + 1
        
        if avg_range_end >= len(data):
            avg_range_end = len(data)
            
        avg_range_length = avg_range_end - avg_range_start
        
        while avg_range_start < avg_range_end:
            avg_x += float(data[avg_range_start].get(x_key, 0))
            avg_y += float(data[avg_range_start].get(y_key, 0))
            avg_range_start += 1
            
        avg_x /= avg_range_length
        avg_y /= avg_range_length

        # Get the range for this bucket
        range_offs = math.floor((i + 0) * every) + 1
        range_to = math.floor((i + 1) * every) + 1
        
        point_a_x = float(data[a].get(x_key, 0))
        point_a_y = float(data[a].get(y_key, 0))

        max_area = -1
        max_area_point = None

        while range_offs < range_to:
            # Calculate triangle area over three buckets
            area = math.fabs(
                (point_a_x - avg_x) * (float(data[range_offs].get(y_key, 0)) - point_a_y) -
                (point_a_x - float(data[range_offs].get(x_key, 0))) * (avg_y - point_a_y)
            ) * 0.5
            
            if area > max_area:
                max_area = area
                max_area_point = data[range_offs]
                next_a = range_offs
                
            range_offs += 1

        sampled.append(max_area_point)
        a = next_a

    sampled.append(data[len(data) - 1])  # Always include the last point

    return sampled

def aggregate_candles(data: list[dict[str, Any]], target: int) -> list[dict[str, Any]]:
    """
    Downsamples candlestick data by aggregating buckets of candles into larger synthetic candles.
    This preserves the true 'high' and 'low' of the period, which LTTB might miss.
    """
    if target >= len(data) or target <= 0:
        return data

    chunk_size = max(1, len(data) // target)
    downsampled = []

    for i in range(0, len(data), chunk_size):
        chunk = data[i:i + chunk_size]
        if not chunk:
            continue
            
        downsampled.append({
            "time": chunk[0].get("time"),
            "open": chunk[0].get("open"),
            "high": max((c.get("high", 0) for c in chunk), default=0),
            "low": min((c.get("low", 0) for c in chunk), default=0),
            "close": chunk[-1].get("close"),
            "volume": sum((c.get("volume", 0) for c in chunk)),
            "turnover": sum((c.get("turnover", 0) for c in chunk))
        })

    return downsampled
