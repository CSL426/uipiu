"""點單即送 Nidin：送單、防重送、截止、逾時、取消權限、刷新節流。"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app import auth as account_auth
from app.cart_store import CartStore
from app.main import app
from app.models import GuestOrderReceipt
from app.nidin import NidinClient, normalize_menu_from_api, normalize_official_group_buy_info
from app.nidin.client import NidinApiError
from app.routers import live_orders
from tests.fixtures import RAW_GROUP_MENU
from tests.test_official_sync_real_shape import REAL_SHAPE

pytestmark = pytest.mark.usefixtures("authenticated_manager")

MENU = normalize_menu_from_api(RAW_GROUP_MENU, "1717")
LINE = {"item_id": "1950362419", "quantity": 1, "selection_option_ids": ["1950362926", "1950362950"]}
OPEN_BRIEF = {"group_buying_status": 10, "store_id": 1717}
RECEIPT = GuestOrderReceipt(
    order_id=300000001, order_status=31, store_id="1717", group_buy_code="ABCDEFGHIJ",
    guest_user_id=9000001, total_money=60, item_name="大●奶茶",
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr("app.dependencies.cart_store", CartStore(tmp_path / "cart.db"))
    live_orders._last_refresh.clear()


@pytest.fixture
def session_id(authenticated_manager, isolate):
    from app.dependencies import cart_store

    return cart_store.create_or_get_session(
        group_buy_url="https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ",
        store_id="1717",
        group_buy_code="ABCDEFGHIJ",
        session_name="測試",
        menu=MENU,
        delivery_summary=None,
        owner_id=account_auth.first_admin_id(),
    ).session_id


def _api():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


def _order(submission_id="sub-00000001", name="小明"):
    return {"submission_id": submission_id, "contributor_name": name, "lines": [LINE]}


def _nidin(**overrides):
    mocks = {
        "fetch_group_buy_brief": AsyncMock(return_value=OPEN_BRIEF),
        "fetch_menu": AsyncMock(return_value=MENU),
        "order_guest_cart_session": AsyncMock(return_value=RECEIPT),
    }
    mocks.update(overrides)
    return mocks, [patch.object(NidinClient, name, new=mock) for name, mock in mocks.items()]


async def _post(path, json=None, *, patches=(), headers=None, method="POST"):
    for p in patches:
        p.start()
    try:
        async with _api() as client:
            return await client.request(method, path, json=json, headers=headers)
    finally:
        for p in patches:
            p.stop()


@pytest.mark.anyio
async def test_submitting_sends_one_follower_order_to_nidin_and_records_it(session_id):
    mocks, patches = _nidin()
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 200
    body = res.json()
    assert body["official_order_id"] == "300000001"
    assert body["cancel_token"]
    lines = body["session"]["lines"]
    assert [(l["contributor_name"], l["official_order_id"]) for l in lines] == [("小明", "300000001")]
    call = mocks["order_guest_cart_session"].await_args.kwargs
    assert call["guest_name"] == "小明"
    # 送單網址來自本站紀錄，不接受前端指定
    assert call["group_buy_url"] == "https://order.nidin.shop/gb/menu/1717/ABCDEFGHIJ"


@pytest.mark.anyio
async def test_resending_the_same_submission_does_not_order_twice(session_id):
    mocks, patches = _nidin()
    first = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    mocks2, patches2 = _nidin()
    again = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)

    assert again.status_code == 200
    assert again.json()["already_submitted"] is True
    assert again.json()["cancel_token"] == first.json()["cancel_token"]
    mocks2["order_guest_cart_session"].assert_not_awaited()
    assert len(again.json()["session"]["lines"]) == 1


@pytest.mark.anyio
async def test_closed_group_is_refused_before_anything_is_sent(session_id):
    mocks, patches = _nidin(fetch_group_buy_brief=AsyncMock(return_value={"group_buying_status": 20}))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 409
    assert "截止" in res.json()["detail"]
    mocks["order_guest_cart_session"].assert_not_awaited()
    # 確定沒送出的可以用同一個 id 重試
    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 200


@pytest.mark.anyio
async def test_menu_fetch_failure_before_sending_can_be_retried(session_id):
    mocks, patches = _nidin(fetch_menu=AsyncMock(side_effect=httpx.ConnectError("down")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    assert res.status_code == 503
    mocks["order_guest_cart_session"].assert_not_awaited()

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 200


def _fails_during_commit(exc):
    """模擬已送出最後一步（productCouponFlow）才出事：先把 progress 標成 commit 再丟例外。"""
    async def fake(self, **kwargs):
        kwargs["progress"].update(stage="commit", cart_order_id="300000001", guest_user_id="9000001")
        raise exc
    return fake


@pytest.mark.anyio
async def test_timeout_after_sending_blocks_blind_resend(session_id):
    mocks, patches = _nidin()
    patches[-1] = patch.object(NidinClient, "order_guest_cart_session", new=_fails_during_commit(httpx.ReadTimeout("slow")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    assert res.status_code == 503
    assert "可能已經成立" in res.json()["detail"]

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 409
    mocks2["order_guest_cart_session"].assert_not_awaited()


@pytest.mark.anyio
async def test_nidin_5xx_on_the_final_step_is_uncertain_not_failed(session_id):
    response = httpx.Response(502, request=httpx.Request("POST", "https://api.test/order/productCouponFlow"))
    _, patches = _nidin()
    patches[-1] = patch.object(
        NidinClient, "order_guest_cart_session",
        new=_fails_during_commit(httpx.HTTPStatusError("bad gateway", request=response.request, response=response)),
    )
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 503
    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 409
    mocks2["order_guest_cart_session"].assert_not_awaited()


@pytest.mark.anyio
async def test_connection_refused_on_the_final_step_is_definitely_not_sent(session_id):
    _, patches = _nidin()
    patches[-1] = patch.object(NidinClient, "order_guest_cart_session", new=_fails_during_commit(httpx.ConnectError("refused")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    assert res.status_code == 503
    assert "還沒有送出" in res.json()["detail"]

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 200


@pytest.mark.anyio
async def test_timeout_before_the_final_step_can_be_retried(session_id):
    # 還在抓菜單就逾時（沒有進到 commit）：確定沒送出
    mocks, patches = _nidin(order_guest_cart_session=AsyncMock(side_effect=httpx.ReadTimeout("menu slow")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    assert res.status_code == 503
    assert "還沒有送出" in res.json()["detail"]

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 200


@pytest.mark.anyio
async def test_unexpected_error_before_sending_does_not_leave_the_submission_stuck(session_id):
    mocks, patches = _nidin(fetch_group_buy_brief=AsyncMock(side_effect=KeyError("group_buy_brief")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)
    assert res.status_code == 502

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches2)
    assert retry.status_code == 200


def _official_with_follower(order_id):
    shape = {**REAL_SHAPE, "group_buy_follower_order_list": [
        {**REAL_SHAPE["group_buy_follower_order_list"][0], "order_id": order_id}
    ]}
    return normalize_official_group_buy_info(shape, "ABCDEFGHIJ")


@pytest.mark.anyio
async def test_uncertain_order_found_on_nidin_becomes_submitted_and_returns_the_cancel_token(leader_bound):
    _, patches = _nidin()
    patches[-1] = patch.object(NidinClient, "order_guest_cart_session", new=_fails_during_commit(httpx.ReadTimeout("slow")))
    await _post(f"/cart-sessions/{leader_bound}/orders", _order(), patches=patches)

    sync = patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(return_value=_official_with_follower(300000001)))
    refreshed = await _post(f"/cart-sessions/{leader_bound}/refresh", patches=[sync])
    assert refreshed.json()["synced"] is True

    mocks2, patches2 = _nidin()
    again = await _post(f"/cart-sessions/{leader_bound}/orders", _order(), patches=patches2)
    assert again.status_code == 200
    assert again.json()["already_submitted"] is True
    assert again.json()["official_order_id"] == "300000001"
    mocks2["order_guest_cart_session"].assert_not_awaited()


@pytest.mark.anyio
async def test_uncertain_order_missing_on_nidin_after_the_grace_period_can_be_retried(leader_bound, monkeypatch):
    _, patches = _nidin()
    patches[-1] = patch.object(NidinClient, "order_guest_cart_session", new=_fails_during_commit(httpx.ReadTimeout("slow")))
    await _post(f"/cart-sessions/{leader_bound}/orders", _order(), patches=patches)

    monkeypatch.setattr(live_orders, "UNCERTAIN_GRACE_SECONDS", -1.0)
    sync = patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(return_value=_official_with_follower(1)))
    await _post(f"/cart-sessions/{leader_bound}/refresh", patches=[sync])

    mocks2, patches2 = _nidin()
    retry = await _post(f"/cart-sessions/{leader_bound}/orders", _order(), patches=patches2)
    assert retry.status_code == 200
    mocks2["order_guest_cart_session"].assert_awaited_once()


@pytest.mark.anyio
async def test_same_submission_id_with_different_content_is_refused(session_id):
    await _submitted(session_id)
    mocks, patches = _nidin()
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(name="小美"), patches=patches)

    assert res.status_code == 409
    mocks["order_guest_cart_session"].assert_not_awaited()


@pytest.mark.anyio
async def test_submissions_are_rate_limited_per_client(session_id, monkeypatch):
    monkeypatch.setattr(live_orders, "SUBMIT_LIMIT_PER_IP", 2)
    live_orders._submits_by_ip.clear()
    statuses = []
    for i in range(3):
        _, patches = _nidin()
        statuses.append((await _post(f"/cart-sessions/{session_id}/orders", _order(submission_id=f"sub-limit-{i}"), patches=patches)).status_code)
    live_orders._submits_by_ip.clear()

    assert statuses == [200, 200, 429]


@pytest.mark.anyio
async def test_nidin_refusal_is_reported_and_nothing_recorded(session_id):
    mocks, patches = _nidin(order_guest_cart_session=AsyncMock(side_effect=NidinApiError("團購已關閉")))
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)

    assert res.status_code == 409
    assert "團購已關閉" in res.json()["detail"]
    from app.dependencies import cart_store
    assert cart_store.get_session(session_id).lines == []


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["", "   ", "訪客"])
async def test_a_real_name_is_required(session_id, name):
    mocks, patches = _nidin()
    res = await _post(f"/cart-sessions/{session_id}/orders", _order(name=name), patches=patches)

    assert res.status_code == 422
    mocks["order_guest_cart_session"].assert_not_awaited()


async def _submitted(session_id):
    _, patches = _nidin()
    return (await _post(f"/cart-sessions/{session_id}/orders", _order(), patches=patches)).json()


def _cancel_patches(**overrides):
    mocks = {
        "fetch_head_order_id": AsyncMock(return_value="300000002"),
        "delete_follower_order": AsyncMock(return_value=None),
        "fetch_group_buy_official_info": AsyncMock(
            return_value=normalize_official_group_buy_info({**REAL_SHAPE, "group_buy_follower_order_list": []}, "ABCDEFGHIJ")
        ),
    }
    mocks.update(overrides)
    return mocks, [patch.object(NidinClient, name, new=mock) for name, mock in mocks.items()]


def _anonymous():
    app.dependency_overrides[account_auth.current_user] = lambda: None


@pytest.fixture
def leader_bound(session_id):
    account_auth.set_leader_credentials(account_auth.first_admin_id(), "tok-123456789", "777")
    return session_id


@pytest.mark.anyio
async def test_the_person_who_ordered_can_cancel_with_their_token(leader_bound):
    submitted = await _submitted(leader_bound)
    _anonymous()
    mocks, patches = _cancel_patches()
    res = await _post(
        f"/cart-sessions/{leader_bound}/orders/300000001",
        method="DELETE",
        headers={"X-Cancel-Token": submitted["cancel_token"]},
        patches=patches,
    )

    assert res.status_code == 200
    mocks["delete_follower_order"].assert_awaited_once_with(
        token="tok-123456789", user_id="777", head_order_id="300000002", follower_order_id="300000001"
    )
    assert all(l["official_order_id"] != "300000001" for l in res.json()["lines"])
    from app.dependencies import cart_store
    assert cart_store.nidin_order_id(leader_bound) == "300000002"


@pytest.mark.anyio
async def test_strangers_cannot_cancel_someone_elses_order(leader_bound):
    await _submitted(leader_bound)
    _anonymous()
    mocks, patches = _cancel_patches()
    res = await _post(
        f"/cart-sessions/{leader_bound}/orders/300000001",
        method="DELETE",
        headers={"X-Cancel-Token": "0" * 64},
        patches=patches,
    )

    assert res.status_code == 403
    mocks["delete_follower_order"].assert_not_awaited()


@pytest.mark.anyio
async def test_the_leader_can_cancel_without_a_token(leader_bound):
    await _submitted(leader_bound)
    mocks, patches = _cancel_patches()
    res = await _post(f"/cart-sessions/{leader_bound}/orders/300000001", method="DELETE", patches=patches)

    assert res.status_code == 200
    mocks["delete_follower_order"].assert_awaited_once()


@pytest.mark.anyio
async def test_leaders_own_drinks_are_not_cancellable_here(leader_bound):
    mocks, patches = _cancel_patches()
    res = await _post(f"/cart-sessions/{leader_bound}/orders/head:M0300000002", method="DELETE", patches=patches)

    assert res.status_code == 400
    mocks["delete_follower_order"].assert_not_awaited()


@pytest.mark.anyio
async def test_refresh_mirrors_nidin_and_is_throttled(leader_bound):
    official = normalize_official_group_buy_info(REAL_SHAPE, "ABCDEFGHIJ")
    fetch = AsyncMock(return_value=official)
    _anonymous()
    with patch.object(NidinClient, "fetch_group_buy_official_info", new=fetch):
        async with _api() as client:
            first = await client.post(f"/cart-sessions/{leader_bound}/refresh")
            second = await client.post(f"/cart-sessions/{leader_bound}/refresh")

    assert first.json()["synced"] is True
    assert sorted(l["contributor_name"] for l in first.json()["session"]["lines"]) == sorted(["小明", "小美"])
    assert second.json()["synced"] is False
    fetch.assert_awaited_once()


@pytest.mark.anyio
async def test_refresh_explains_when_the_leader_is_not_connected(session_id):
    res = await _post(f"/cart-sessions/{session_id}/refresh")

    assert res.status_code == 200
    assert res.json()["synced"] is False
    assert "Nidin" in res.json()["message"]


@pytest.mark.anyio
async def test_anonymous_visitors_cannot_write_local_only_lines(session_id):
    _anonymous()
    app.dependency_overrides.pop(account_auth.require_user, None)
    res = await _post(f"/cart-sessions/{session_id}/lines", {"contributor_name": "小明", "lines": [LINE]})

    assert res.status_code == 401


@pytest.mark.anyio
async def test_cancelling_twice_does_not_hit_nidin_again(leader_bound):
    submitted = await _submitted(leader_bound)
    _anonymous()
    headers = {"X-Cancel-Token": submitted["cancel_token"]}
    mocks, patches = _cancel_patches()
    first = await _post(f"/cart-sessions/{leader_bound}/orders/300000001", method="DELETE", headers=headers, patches=patches)
    second = await _post(f"/cart-sessions/{leader_bound}/orders/300000001", method="DELETE", headers=headers, patches=patches)

    assert first.status_code == 200
    assert second.status_code == 409
    mocks["delete_follower_order"].assert_awaited_once()


@pytest.mark.anyio
async def test_a_non_ascii_cancel_token_is_just_rejected(leader_bound):
    await _submitted(leader_bound)
    _anonymous()
    mocks, patches = _cancel_patches()
    res = await _post(
        f"/cart-sessions/{leader_bound}/orders/300000001",
        method="DELETE",
        headers={"X-Cancel-Token": "取消".encode()},
        patches=patches,
    )

    assert res.status_code == 403


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{"status": 1101, "message": "輸入資訊錯誤!"}, {"status": 200, "message": "ok"}])
async def test_official_info_error_bodies_raise_instead_of_looking_like_an_empty_group(body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))) as http:
        client = NidinClient(base_url="https://api.test", client=http)
        with pytest.raises(NidinApiError):
            await client.fetch_group_buy_official_info(group_buy_code="ABCDEFGHIJ", token="t", user_id="1")


@pytest.mark.anyio
async def test_a_nidin_error_during_refresh_keeps_the_orders_on_screen(leader_bound):
    await _submitted(leader_bound)
    with patch.object(NidinClient, "fetch_group_buy_official_info", new=AsyncMock(side_effect=NidinApiError("輸入資訊錯誤!"))):
        res = await _post(f"/cart-sessions/{leader_bound}/refresh")

    assert res.json()["synced"] is False
    assert [l["official_order_id"] for l in res.json()["session"]["lines"]] == ["300000001"]


@pytest.mark.anyio
@pytest.mark.parametrize(("phone", "sent", "status"), [("", "", 200), ("0912-345-678", "0912345678", 200), ("12345", None, 422)])
async def test_the_orderers_phone_is_optional_and_validated(session_id, phone, sent, status):
    mocks, patches = _nidin()
    res = await _post(
        f"/cart-sessions/{session_id}/orders",
        {**_order(), "contributor_phone": phone},
        patches=patches,
    )

    assert res.status_code == status
    if sent is not None:
        assert mocks["order_guest_cart_session"].await_args.kwargs["guest_phone"] == sent
