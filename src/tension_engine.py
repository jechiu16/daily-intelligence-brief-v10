"""TensionEngine — 跨資產張力描述生成。

v10.1: 從 data_watcher.py 提取。在 QuantEngine 之後執行，
此時有完整跨資產 context 可供解讀。

設計原則：
- 不重複數字（Narrator 已顯示）
- 聚焦「這個數字在今天的語境裡意味著什麼」
- 優先呈現跨資產矛盾或確認關係
- 無跨資產信號時，給一個基於絕對水準的解讀
"""

import logging

logger = logging.getLogger(__name__)


def generate_tension_note(key: str, item: dict, data_package: dict) -> str:
    """生成一句帶脈絡的張力描述。"""
    change = item.get("change_pct")
    try:
        change = float(change)
    except (TypeError, ValueError):
        change = None

    # 無 change_pct 的資產，直接跳到各資產邏輯
    if change is None and key not in ("nfci", "tw_foreign_net", "bdi",
                                       "cot_gold", "tw_leading", "tw_export",
                                       "caixin_pmi", "korea_export",
                                       "eia_crude_inventory"):
        return "數據缺失，無法判讀"

    abs_change = abs(change) if change is not None else 0

    # 收集跨資產數據
    def _chg(k):
        v = data_package.get(k, {}).get("change_pct", 0)
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    def _price(k):
        v = data_package.get(k, {}).get("price") or data_package.get(k, {}).get("value")
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            return 0.0

    spx_chg  = _chg("spx")
    vix_chg  = _chg("vix")
    dxy_chg  = _chg("dxy")
    gold_chg = _chg("gold")
    brent_chg = _chg("brent")
    wti_chg  = _chg("wti")
    vix_px   = _price("vix")
    us10y_px = _price("us10y")
    tips_px  = _price("tips_10y")

    # ── 各資產專屬邏輯 ───────────────────────────────────────────────────────

    if key == "vix":
        if vix_px > 30:
            if change < 0:
                return "高位緩降，市場情緒邊際改善，但壓力閾值未解除"
            else:
                return "壓力區內繼續走高，恐慌情緒尚未見頂"
        elif vix_px > 20:
            if change < -3:
                return "從警戒區快速退潮，但尚未回到平靜市場定義（<20）"
            elif spx_chg > 0:
                return "股漲 VIX 也高——市場一邊做多一邊買保護，方向不確定"
            else:
                return "維持在警戒水準，市場尚未給出明確方向"
        else:  # < 20
            if change > 5:
                return "從低位急升，情緒突然轉向，需確認是否有突發事件"
            else:
                return "處於低恐慌區，市場情緒偏樂觀"

    elif key == "gold":
        if spx_chg > 1 and change > 1:
            return "股金同漲——流動性驅動溢出效應，而非純粹避險買盤"
        elif spx_chg < -1 and change > 1:
            return "股跌金漲，避險邏輯激活，資金轉向保值資產"
        elif dxy_chg > 0.5 and change > 0:
            return "美元走強下仍上漲，顯示黃金的自主性買盤不依賴弱美元"
        elif change > 2:
            return "單日漲幅超過正常波動帶，動能強勁但需警惕均值回歸"
        elif change < -2:
            return "顯著回落，短線獲利了結或實質利率反彈壓制"
        else:
            return "在關鍵水準附近整理，方向待確認"

    elif key == "spx":
        if change > 1 and vix_chg > 0:
            return "漲勢下 VIX 同步走高——市場內部存在空頭對沖，上行動能有疑"
        elif change > 1.5 and gold_chg > 1:
            return "股金齊漲，流動性驅動而非風險偏好切換"
        elif change < -1.5 and vix_px > 25:
            return "在已偏高的 VIX 水準上再下跌，恐慌疊加，需觀察是否觸發止損連鎖"
        elif change > 0 and brent_chg < -5:
            return "油價大跌中股市走強，市場把能源成本下降視為利多"
        elif abs_change < 0.3:
            return "在高位震盪整理，方向性突破尚未出現"
        else:
            return "跟隨整體風險情緒移動"

    elif key == "brent":
        wti_spread = change - wti_chg
        if abs_change > 10:
            if abs(wti_spread) > 5:
                return f"暴跌且與 WTI 嚴重分化（乖離 {wti_spread:+.1f}pp），地區供給事件而非全球需求崩潰"
            else:
                return "WTI 同步大跌，全球需求崩跌信號，需追蹤宏觀觸發因素"
        elif abs_change > 4:
            if abs(wti_spread) > 2:
                return f"Brent-WTI 乖離擴大（{wti_spread:+.1f}pp），歐洲/中東供給端有特定事件"
            else:
                return "顯著波動，需確認供給事件還是風險情緒驅動"
        elif change < 0 and spx_chg > 0:
            return "油跌股漲，市場解讀為成本下降利多，而非需求衰退警示"
        elif change > 0 and vix_px > 25:
            return "高恐慌環境中油價仍上漲，供給端因素主導"
        else:
            return "在現有區間內移動，等待催化劑"

    elif key == "wti":
        if abs(_chg("brent") - change) > 4:
            return "與 Brent 走勢分化，美國本土供給因素影響更大"
        elif change < -5:
            return "庫欣庫存或管道數據主導，需對照 EIA 週報確認方向"
        else:
            return "跟隨 Brent 整體走勢"

    elif key == "dxy":
        if change < -0.5 and gold_chg > 0:
            return "弱美元配合金價上漲，美元貶值預期強化"
        elif change < -0.5 and spx_chg > 1:
            return "美元弱勢伴隨股市走強，Risk-On 模式下資金流出美元"
        elif change > 0.5 and gold_chg > 0:
            return "美元走強下黃金仍漲，顯示有非美元資金的避險需求"
        elif change > 0.5 and spx_chg < 0:
            return "美元走強、股市走弱——典型的避險切換"
        elif abs_change < 0.2:
            return "窄幅整理，方向等待聯準會信號或非農"
        else:
            return "反映市場對聯準會路徑的重新定價"

    elif key == "usdjpy":
        if change > 1:
            return "日圓大幅走弱，市場在押注 BOJ 不急於加息，需留意干預風險"
        elif change < -1:
            return "日圓快速升值，BOJ 政策預期轉變或避險買盤湧入"
        elif abs_change < 0.3:
            return "區間震盪，日圓方向待 BOJ 會議或 CPI 數據確認"
        else:
            return "短線跟隨風險情緒移動"

    elif key == "usdtwd":
        if change > 0.5:
            return "台幣走弱，外資淨賣超或美元走強壓力"
        elif change < -0.5:
            return "台幣走強，外資買盤或出口商拋匯"
        else:
            return "窄幅整理，等待台積電法說或外資方向確認"

    elif key == "us10y":
        if abs_change > 0.1:
            if change > 0:
                return "殖利率單日大幅走升，市場重新定價升息預期或通膨溢價"
            else:
                return "殖利率大幅下行，市場搶進避險資產或押注聯準會轉向"
        elif abs_change > 0.05:
            if change > 0 and tips_px > 2.0:
                return "名目與實質利率同步走高，通膨預期並未緩解"
            elif change < 0:
                return "殖利率下行，降息預期升溫"
        else:
            return "在現有區間鞏固，等待 CPI 或聯準會發言"

    elif key == "tips_10y":
        nominal_chg = _chg("us10y")
        if nominal_chg > 0 and change < 0:
            return "名目利率升、實質利率降——通膨預期擴大，不利實質購買力"
        elif change > 0.05:
            return "實質利率走升，壓制黃金和高估值股票的基本面"
        elif change < -0.05:
            return "實質利率下行，為黃金和成長股提供支撐"
        else:
            return "與名目利率同向移動，通膨預期相對穩定"

    elif key == "tw_foreign_net":
        price_val = item.get("price") or item.get("value") or 0
        try:
            net = float(price_val)
        except (TypeError, ValueError):
            net = 0
        if net > 200:
            return "外資大幅淨買超，台股支撐力道強"
        elif net > 0:
            return "外資小幅淨買，觀望氣氛濃厚"
        elif net < -200:
            return "外資大幅淨賣，需觀察是否持續流出"
        else:
            return "外資小幅淨賣，方向不明確"

    elif key == "nfci":
        price_val = item.get("price") or item.get("value") or 0
        try:
            nfci = float(price_val)
        except (TypeError, ValueError):
            nfci = 0
        if nfci > 0.5:
            return "金融條件顯著收緊，信用壓力上升"
        elif nfci > 0:
            return "金融條件略緊，處於中性偏緊區間"
        elif nfci > -0.5:
            return "金融條件寬鬆，流動性環境支持風險資產"
        else:
            return "金融條件極度寬鬆，系統性壓力幾乎不存在"

    # 其他資產：給簡短方向性描述
    if abs_change > 5:
        return "幅度超出正常日波動，需追蹤催化因素"
    elif change is not None and change > 0:
        return "跟隨整體風險偏好走強"
    elif change is not None and change < 0:
        return "受壓回落，等待方向確認"
    else:
        return "無明顯方向"


def apply_tension_notes(data_package: dict) -> dict:
    """對 data_package 中每個資產注入 tension_note。

    在 orchestrator 中，於 QuantEngine 之後呼叫。
    """
    for key, item in data_package.items():
        if not isinstance(item, dict):
            continue
        if key == "quality_scores":
            continue
        item["tension_note"] = generate_tension_note(key, item, data_package)
    return data_package
