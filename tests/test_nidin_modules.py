"""Contracts across the split integration and its legacy patch namespace."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import nidin
from app.nidin import builder, client, normalizer
from tests.fixtures import RAW_GROUP_MENU


def test_public_types_and_helpers_keep_their_identity():
    assert nidin.NidinClient is client.NidinClient
    assert nidin.MenuChangedError is builder.MenuChangedError
    assert nidin.MenuDataError is builder.MenuDataError
    assert nidin.normalize_menu_from_api is normalizer.normalize_menu_from_api
    assert nidin.build_group_buy_cart_payload is builder.build_group_buy_cart_payload
    assert nidin._decode_map is normalizer._decode_map
    assert nidin.NidinClient.__module__ == "app.nidin.client"
    assert "normalize_menu_from_api" in dir(nidin)


@pytest.mark.anyio
async def test_existing_class_method_patch_reaches_the_new_client():
    expected = object()
    with patch(
        "app.dependencies.NidinClient.fetch_group_buy_official_info",
        new=AsyncMock(return_value=expected),
    ) as fetch:
        assert await client.NidinClient().fetch_group_buy_official_info(
            "code", "token", "user",
        ) is expected
        fetch.assert_awaited_once_with("code", "token", "user")


def test_normalizer_and_builder_have_no_transport_dependency():
    assert not hasattr(normalizer, "httpx")
    assert not hasattr(builder, "httpx")
    assert builder.normalize_menu_from_api is normalizer.normalize_menu_from_api
    assert client.build_group_buy_cart_payload is builder.build_group_buy_cart_payload


@pytest.mark.anyio
async def test_client_fetch_flows_through_normalizer():
    def upstream(request):
        if request.url.path.endswith("/productImages"):
            return httpx.Response(200, json={"product_list": {
                "1950362347": {"main_image": {"url": "https://example.test/tea"}},
            }})
        return httpx.Response(200, json=RAW_GROUP_MENU)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(upstream),
    ) as transport:
        menu = await client.NidinClient(client=transport).fetch_menu("1717")
    assert menu.store_id == "1717"
    assert menu.items[0].image_url == "https://example.test/tea"
    assert menu.model_dump(exclude={"items"}) == normalizer.normalize_menu_from_api(
        RAW_GROUP_MENU, "1717",
    ).model_dump(exclude={"items"})


def test_builder_wraps_invalid_normalizer_data():
    with pytest.raises(nidin.MenuDataError) as raised:
        builder.validated_order_menu({}, "1717")
    assert isinstance(raised.value.__cause__, KeyError)
