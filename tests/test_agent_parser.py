import pytest
from unittest.mock import AsyncMock, patch

from app.agent_parser import AgentParser, format_menu_for_prompt
from app.models import NormalizedMenu
from app.nidin import normalize_menu_from_api
from tests.fixtures import RAW_GROUP_MENU


@pytest.fixture
def sample_menu() -> NormalizedMenu:
    return normalize_menu_from_api(RAW_GROUP_MENU, "1717")


def test_format_menu_for_prompt(sample_menu):
    prompt_text = format_menu_for_prompt(sample_menu)
    assert "中●阿薩姆紅茶" in prompt_text
    assert "大●奶茶" in prompt_text
    assert "甜度" in prompt_text
    assert "溫度" in prompt_text


@pytest.mark.asyncio
async def test_agent_parser_add_to_cart(sample_menu):
    mock_llm_json = """
    {
      "action": "add_to_cart",
      "items": [
        {
          "customer_name": "小明",
          "item_id": "1950362419",
          "item_name": "大●奶茶",
          "quantity": 2,
          "selection_option_ids": ["1950362845_202", "1950362863_301"],
          "selection_option_names": ["半糖", "+珍珠"]
        }
      ],
      "reply_message": "已幫小明加入 2 杯大杯奶茶（半糖、加珍珠）！"
    }
    """
    parser = AgentParser(enable_fast_graph=False)
    with patch.object(parser, "_run_llm", new=AsyncMock(return_value=mock_llm_json)):
        result = await parser.parse("小明 +2 大杯奶茶 半糖加珍珠", sample_menu)

    assert result.action == "add_to_cart"
    assert len(result.items) == 1
    assert result.items[0].customer_name == "小明"
    assert result.items[0].quantity == 2
    assert result.items[0].item_id == "1950362419"
    assert result.items[0].item_name == "大●奶茶"
    assert result.items[0].selection_option_ids == ["1950362845_202", "1950362863_301"]


@pytest.mark.asyncio
async def test_agent_parser_clarify_reply(sample_menu):
    mock_reply_json = """
    {
      "action": "reply",
      "items": [],
      "reply_message": "小美您好！請問您的阿薩姆紅茶需要正常冰還是熱的呢？"
    }
    """
    parser = AgentParser(enable_fast_graph=False)
    with patch.object(parser, "_run_llm", new=AsyncMock(return_value=mock_reply_json)):
        result = await parser.parse("小美 紅茶要半糖", sample_menu)

    assert result.action == "reply"
    assert len(result.items) == 0
    assert "正常冰還是熱的" in result.reply_message


@pytest.mark.asyncio
async def test_agent_parser_fast_graph_integrated(sample_menu):
    parser = AgentParser(enable_fast_graph=True)
    # RAW_GROUP_MENU 中 中●阿薩姆紅茶 有 溫度(正常冰/熱) 與 甜度(正常甜/半糖)
    result = await parser.parse("小明要一杯中杯阿薩姆紅茶 半糖正常冰", sample_menu)

    assert result.action == "add_to_cart"
    assert len(result.items) == 1
    assert result.items[0].customer_name == "小明"
    assert result.items[0].item_name == "中●阿薩姆紅茶"
    assert len(result.items[0].selection_option_ids) == 2
