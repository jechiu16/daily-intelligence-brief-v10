from __future__ import annotations
"""
Daily Intelligence Brief — Periphery Signal System
v9 邊陲系統移植：32 個世界區域，md5 hash 每日輪替。
"""
import hashlib
from datetime import datetime

PERIPHERY_POOL = [
    # 地緣政治邊陲
    ("薩赫勒 + 西非",      "Sahel West Africa security governance"),
    ("高加索",             "Caucasus Armenia Azerbaijan Georgia conflict"),
    ("緬甸內戰",           "Myanmar civil war junta resistance"),
    ("葉門",               "Yemen Houthi Red Sea conflict ceasefire"),
    ("伊拉克 + 敘利亞",    "Iraq Syria border militia Iran proxy"),
    ("黎巴嫩重建",         "Lebanon reconstruction Hezbollah economy"),
    ("利比亞",             "Libya fragmentation warlords oil"),
    ("蘇丹內戰",           "Sudan civil war RSF SAF humanitarian"),
    ("索馬利亞",           "Somalia Horn of Africa al-Shabaab"),
    # 經濟轉型
    ("越南",               "Vietnam manufacturing FDI supply chain"),
    ("孟加拉",             "Bangladesh garment political transition"),
    ("衣索比亞",           "Ethiopia economy reconstruction growth"),
    ("哈薩克",             "Kazakhstan energy China Russia"),
    ("墨西哥",             "Mexico nearshoring manufacturing US trade"),
    ("印尼",               "Indonesia nickel EV supply chain"),
    ("奈及利亞",           "Nigeria oil currency reform economy"),
    # 資源地緣
    ("剛果銅鈷礦帶",       "DRC Congo copper cobalt mining China"),
    ("智利阿根廷鋰三角",   "Chile Argentina lithium EV battery"),
    ("哈薩克烏茲別克鈾礦", "Kazakhstan Uzbekistan uranium nuclear"),
    ("圭亞那",             "Guyana offshore oil ExxonMobil"),
    ("莫三比克 LNG",       "Mozambique LNG insurgency Cabo Delgado"),
    # 海峽 + 通道
    ("麻六甲海峽",         "Malacca Strait shipping chokepoint"),
    ("博斯普魯斯 + 黑海",  "Bosphorus Black Sea Turkey grain Ukraine"),
    ("巴拿馬運河",         "Panama Canal drought water shipping"),
    ("亞丁灣 + 紅海",     "Aden Gulf Red Sea Houthi shipping"),
    # 氣候 + 農業
    ("湄公河",             "Mekong River China dams downstream water"),
    ("東非農業帶",         "East Africa drought food security"),
    ("巴基斯坦",           "Pakistan climate economy IMF"),
    # 政治轉型
    ("泰國政治",           "Thailand politics judiciary military"),
    ("秘魯玻利維亞",       "Peru Bolivia resource nationalism mining"),
    ("塞爾維亞",           "Serbia EU Russia Balkans geopolitics"),
    ("喬治亞",             "Georgia democracy protests EU"),
]


def select_periphery(date: str | datetime = None) -> tuple[str, str]:
    """
    Deterministic periphery selection based on date hash.
    Same date always yields the same region. Cycles through all 32.

    Returns: (label, search_keywords)
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")
    elif isinstance(date, datetime):
        date = date.strftime("%Y-%m-%d")

    idx = int(hashlib.md5(date.encode()).hexdigest(), 16) % len(PERIPHERY_POOL)
    return PERIPHERY_POOL[idx]


def get_periphery_search_query(date: str = None) -> tuple[str, str, str]:
    """
    Returns (label, search_keywords, narrator_prompt_segment).
    """
    label, keywords = select_periphery(date)

    prompt = f"""## 今日邊陲：{label}

用 2-3 段，以「知識領路人」的身份帶讀者認識這個地方：

第一段（座標感）：這個地方在哪裡？多少人？為什麼歷史上重要？1-2 句定位。

第二段（現在發生什麼）：具體事件、趨勢或結構性變化，要有細節。

第三段（為什麼今天值得認識）：
若有真實宏觀傳導，說出來。若沒有，明確說「這條故事線暫時與今日市場主線平行」，
然後說為什麼它仍然值得五分鐘。

禁止硬湊因果。"""

    return label, keywords, prompt
