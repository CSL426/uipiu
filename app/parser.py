import re

from app.models import MenuItem, NormalizedMenu, ParsedItem, ParsedOrder, ParsedSelection


QUANTITY_PATTERN = re.compile(r"^\s*\+?(?P<quantity>\d+)\s*")


def parse_order_lines(text: str, menu: NormalizedMenu) -> list[ParsedOrder]:
    orders: list[ParsedOrder] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        orders.append(parse_order_text(stripped, menu))
    return orders


def parse_order_text(text: str, menu: NormalizedMenu) -> ParsedOrder:
    quantity, remaining = _extract_quantity(text)
    item, remaining = _match_item(remaining, menu.items)
    tokens = [token for token in remaining.split() if token]
    selections, unmatched_tokens = _match_options(tokens, item, menu)

    return ParsedOrder(
        quantity=quantity,
        item=ParsedItem(id=item.id, name=item.name, price=item.price),
        selections=selections,
        unmatched_tokens=unmatched_tokens,
    )


def _extract_quantity(text: str) -> tuple[int, str]:
    match = QUANTITY_PATTERN.match(text)
    if not match:
        return 1, text.strip()

    quantity = int(match.group("quantity"))
    remaining = text[match.end() :].strip()
    return quantity, remaining


def _match_item(text: str, items: list[MenuItem]) -> tuple[MenuItem, str]:
    normalized_text = text.strip()
    matches: list[tuple[int, int, MenuItem, str]] = []

    for item in items:
        item_best_match: tuple[int, int, MenuItem, str] | None = None
        for variant in dict.fromkeys([item.name, *item.aliases]):
            index = normalized_text.find(variant)
            if index == -1:
                continue
            candidate = (len(variant), index, item, variant)
            if item_best_match is None or candidate[0] > item_best_match[0] or (
                candidate[0] == item_best_match[0] and candidate[1] < item_best_match[1]
            ):
                item_best_match = candidate
        if item_best_match is not None:
            matches.append(item_best_match)

    if not matches:
        raise ValueError(f"Unable to match item from: {text}")

    best_length = max(match[0] for match in matches)
    top_matches = [match for match in matches if match[0] == best_length]
    earliest_index = min(match[1] for match in top_matches)
    top_matches = [match for match in top_matches if match[1] == earliest_index]

    if len(top_matches) > 1:
        candidates = ", ".join(match[2].name for match in top_matches[:5])
        raise ValueError(f"Ambiguous item from: {text}. Candidates: {candidates}")

    _, _, item, variant = top_matches[0]
    remaining = normalized_text.replace(variant, " ", 1)
    return item, " ".join(remaining.split())


def _match_options(
    tokens: list[str],
    item: MenuItem,
    menu: NormalizedMenu,
) -> tuple[list[ParsedSelection], list[str]]:
    group_map = {group.id: group for group in menu.groups}
    selected_by_group: dict[str, list[ParsedSelection]] = {group_id: [] for group_id in item.group_ids}
    unmatched_tokens: list[str] = []

    for token in tokens:
        matched = False
        for group_id in item.group_ids:
            group = group_map.get(group_id)
            if group is None:
                continue

            option = _find_option(token, group.options)
            if option is None:
                continue

            selection = ParsedSelection(
                group_id=group.id,
                group_name=group.name,
                option_id=option.id,
                option_name=option.name,
                price=option.price,
                source_token=token,
            )
            if group.max_select == 1:
                selected_by_group[group.id] = [selection]
            else:
                selected_by_group[group.id].append(selection)
            matched = True
            break

        if not matched:
            unmatched_tokens.append(token)

    selections: list[ParsedSelection] = []
    for group_id in item.group_ids:
        selections.extend(selected_by_group[group_id])

    return selections, unmatched_tokens


def _find_option(token: str, options):
    for option in options:
        if token == option.name or token in option.aliases:
            return option
    return None
