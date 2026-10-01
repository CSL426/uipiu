import asyncio
import sys

from app.agent_parser import AgentParser
from app.nidin import normalize_menu_from_api
from tests.fixtures import RAW_GROUP_MENU


async def main():
    user_input = sys.argv[1] if len(sys.argv) > 1 else "小明 +2 大杯奶茶 半糖加珍珠"
    print(f"=== 測試輸入: {user_input} ===")

    menu = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
    parser = AgentParser(use_agy=True)

    print("正在呼叫 agy 進行推理與決策...")
    response = await parser.parse(user_input, menu)

    print("\n=== Agent 決策結果 ===")
    print(f"行動 (Action): {response.action}")
    if response.action == "add_to_cart":
        print(f"回覆訊息: {response.reply_message}")
        print("加入品項:")
        for item in response.items:
            print(f"  - 點單人: {item.customer_name}")
            print(f"  - 品項: {item.item_name} (ID: {item.item_id}) x {item.quantity}")
            print(f"  - 選項: {item.selection_option_names} (IDs: {item.selection_option_ids})")
    else:
        print(f"回覆訊息: {response.reply_message}")


if __name__ == "__main__":
    asyncio.run(main())
