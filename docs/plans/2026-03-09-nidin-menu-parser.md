# Nidin Menu Parser Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a small FastAPI service that extracts a `store_id` from a Nidin menu URL, fetches and normalizes menu data, and parses plain-text drink orders into structured JSON without login or order submission.

**Architecture:** Use a thin HTTP API around pure Python domain functions. Keep Nidin-specific concerns in a client/normalizer module and keep order parsing deterministic so it can be reused later from LINE webhook handlers or other automation.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, httpx, pytest

---

### Task 1: Project skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `app/__init__.py`
- Create: `app/main.py`

**Step 1: Write the failing test**

Create an API smoke test that imports `app.main:app`.

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -q`
Expected: FAIL because `app.main` does not exist.

**Step 3: Write minimal implementation**

Create the package and a `FastAPI` app with `/health`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py -q`
Expected: PASS

### Task 2: Store resolution and menu normalization

**Files:**
- Create: `app/nidin.py`
- Test: `tests/test_nidin.py`

**Step 1: Write the failing test**

Add tests for:
- extracting `9736` from `https://order.nidin.shop/menu/9736`
- normalizing raw API data shaped like `{items, groups}`
- supporting the storefront bundle shape with `category_list_json`, `product_list_json`, `adjust_list_json`, `option_list_json`, `combine_list_json`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_nidin.py -q`
Expected: FAIL because helpers are missing.

**Step 3: Write minimal implementation**

Implement:
- `extract_store_id`
- `normalize_menu`
- `fetch_menu`

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_nidin.py -q`
Expected: PASS

### Task 3: Plain-text order parsing

**Files:**
- Create: `app/parser.py`
- Test: `tests/test_parser.py`

**Step 1: Write the failing test**

Add tests for:
- parsing quantity from `+2`
- matching item names by exact and partial text
- mapping option phrases like `半糖`, `去冰`, `珍珠`
- surfacing unmatched tokens for later human review

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_parser.py -q`
Expected: FAIL because parser logic is missing.

**Step 3: Write minimal implementation**

Implement a deterministic parser with alias maps for common sweetness/ice phrases and item matching against normalized menu data.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_parser.py -q`
Expected: PASS

### Task 4: API wiring

**Files:**
- Modify: `app/main.py`
- Test: `tests/test_api.py`

**Step 1: Write the failing test**

Add tests for:
- `POST /resolve-store`
- `POST /parse-order`

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_api.py -q`
Expected: FAIL because routes are missing.

**Step 3: Write minimal implementation**

Expose JSON endpoints that call the pure helpers.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_api.py -q`
Expected: PASS
