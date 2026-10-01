import asyncio
import json
import re
from pathlib import Path

import httpx

if __package__:
    from .reindex_menu_snapshots import rebuild_index
else:
    from reindex_menu_snapshots import rebuild_index

OUTPUT_DIR = Path("data/menus")

TARGET_BRANDS = [
    # 經典手搖
    "50嵐", "清心福全", "迷客夏", "得正", "大茗本位製茶堂", "五桐號", "龜記茗品", 
    "約翰紅茶公司", "茶之魔手", "茶湯會", "萬波島嶼紅茶", "先喝道", "UNOCHA 烏弄原茶", 
    "老賴茶棧", "春水堂", "春陽茶事", "七盞茶", "康青龍", "珍煮丹", "八曜和茶",
    "鶴茶樓", "叮哥茶坊", "麻古茶坊", "可不可熟成紅茶", "再睡5分鐘", "CoCo都可",
    # 咖啡輕食
    "路易莎", "cama", "黑沃咖啡", "成真咖啡", "彼得好咖啡",
    # 便當與主食
    "八方雲集", "梁社漢排骨", "悟饕池上飯包", "鬍鬚張", "金仙", "正忠排骨",
    # 早餐甜點
    "早安美芝城", "麥味登", "Q Burger", "弘爺漢堡", "拉亞漢堡", "晚安早餐",
]

def sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff\-]", "_", name)

async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=20.0) as client:
        print("1. 抓取 Nidin 品牌列表...")
        res = await client.get("https://loctw-service-api.nidin.shop/shopper/v2/brands", headers={"User-Agent": "Mozilla/5.0"})
        data = res.json()
        all_brands = data.get("brands", [])
        print(f"   共取得 {len(all_brands)} 個品牌。")

        # 匹配目標品牌
        matched_brands = []
        for target in TARGET_BRANDS:
            found = False
            for b in all_brands:
                bname = b.get("name", "")
                if target.lower() in bname.lower() or bname.lower() in target.lower():
                    matched_brands.append((target, b))
                    found = True
                    break
            if not found:
                # 嘗試模糊比對
                for b in all_brands:
                    bname = b.get("name", "")
                    if any(part in bname for part in target.split() if len(part) >= 2):
                        matched_brands.append((target, b))
                        break

        # 去重
        unique_brands = {}
        for target, b in matched_brands:
            unique_brands[b["id"]] = (target, b)
        
        print(f"2. 匹配到 {len(unique_brands)} 個知名品牌，開始抓取分店菜單...\n")

        saved_count = 0

        for brand_id, (target, brand) in unique_brands.items():
            bname = brand.get("name", "")
            try:
                stores_res = await client.get(
                    f"https://loctw-service-api.nidin.shop/shopper/v2/brand/{brand_id}/stores",
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                if stores_res.status_code != 200:
                    continue
                stores = stores_res.json().get("stores", [])
                if not stores:
                    continue

                # 挑選一間門市（若有多間分店，挑第一間或營業中的）
                store = stores[0]
                store_id = str(store.get("id"))
                store_name = store.get("name", "")

                # 抓取該門市菜單與資訊
                menu_res, info_res = await asyncio.gather(
                    client.get(f"https://loctw-service-api.nidin.shop/shopper/v2/store/{store_id}/onShelfMenu"),
                    client.get(f"https://loctw-service-api.nidin.shop/shopper/v2/store/{store_id}/info"),
                    return_exceptions=True
                )

                if isinstance(menu_res, Exception) or menu_res.status_code != 200:
                    print(f"   [跳過] {bname} - {store_name} ({store_id}): 菜單抓取失敗")
                    continue

                menu_data = menu_res.json()
                info_data = info_res.json() if not isinstance(info_res, Exception) and info_res.status_code == 200 else {}

                record = {
                    "brand_id": brand_id,
                    "brand_name": bname,
                    "target_keyword": target,
                    "store_id": store_id,
                    "store_name": store_name,
                    "address": store.get("address", ""),
                    "info": info_data,
                    "raw_menu": menu_data,
                }

                fname = f"{sanitize_filename(bname)}_{store_id}.json"
                out_path = OUTPUT_DIR / fname
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)

                saved_count += 1
                prod_count = len(menu_data.get("menu", {}).get("product_list_json", {}).get("data", []))
                print(f"   [成功 {saved_count:02d}] 【{bname}】 {store_name} (store_id: {store_id}) -> {prod_count} 品項 -> 存入 {fname}")
            except Exception as e:
                print(f"   [錯誤] {bname}: {e}")

        # Include earlier successful samples, not only this download batch.
        entries = rebuild_index(OUTPUT_DIR)
        print(f"索引已更新，共 {len(entries)} 份菜單快照。")

        print(f"\n全部完成！共下載並儲存 {saved_count} 家真實店家的完整 JSON 到 data/menus/。")

if __name__ == "__main__":
    asyncio.run(main())
