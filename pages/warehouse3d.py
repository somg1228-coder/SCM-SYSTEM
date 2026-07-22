from __future__ import annotations

import base64
import json
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

try:
    from backend.database import SessionLocal, init_db
    from backend import services
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    services = None
    WAREHOUSE_IMPORT_ERROR = str(exc)
else:
    WAREHOUSE_IMPORT_ERROR = ""


DEFAULT_LOGIN_DRAWING_PATH = Path.home() / "Downloads" / "[FAC-001~005] 시설 도면_Rev. 1_260305.pdf"
LOGIN_FLOORS = ["1층", "2층", "3층", "4층", "5층"]
TWO_FLOORS = ["1층", "2층"]
ONE_FLOOR = ["1층"]

LOCATIONS = {
    "로긴": {
        "floors": LOGIN_FLOORS,
        "default_floor": "1층",
        "description": "1층부터 5층까지 랙 배치/적재 관리",
        "default_drawing": DEFAULT_LOGIN_DRAWING_PATH,
    },
    "포장부서": {
        "floors": TWO_FLOORS,
        "default_floor": "1층",
        "description": "포장부서 1층/2층 작업 및 포장재 랙 관리",
        "default_drawing": None,
    },
    "창고1": {
        "floors": ONE_FLOOR,
        "default_floor": "1층",
        "description": "창고1 적재 및 피킹 랙 관리",
        "default_drawing": None,
    },
    "창고2": {
        "floors": TWO_FLOORS,
        "default_floor": "1층",
        "description": "창고2 적재 및 예비 랙 관리",
        "default_drawing": None,
    },
    "NC층": {
        "floors": ONE_FLOOR,
        "default_floor": "1층",
        "description": "NC 구역 랙 배치/적재 관리",
        "default_drawing": None,
    },
}

FLOOR_ZONES = {
    "1층": ["회사 출입구", "피킹존", "검수존", "랙 배치"],
    "2층": ["보관 구역", "포장재", "예비 랙", "랙 배치"],
    "3층": ["완제품 보관", "시즌 재고", "저회전 재고", "랙 배치"],
    "4층": ["장기보관", "예비 랙", "확장 구역", "랙 배치"],
    "5층": ["옥상", "설비 구역", "임시 보관", "랙 배치"],
}

FLOOR_MODELS = {
    "1층": {
        "source": "FAC-001",
        "name": "1층 제조/출입구 기준",
        "width": 46,
        "depth": 28,
        "entrances": [{"x": -14.5, "z": 14.05, "w": 5.4, "d": 0.32, "label": "회사 출입구"}],
        "cores": [{"x": 14.6, "z": -8.8, "w": 5.2, "d": 4.8, "h": 2.4, "label": "계단/설비 코어"}],
        "rooms": [
            {"x": -14.8, "z": -7.8, "w": 11.8, "d": 9.2, "label": "제조 작업"},
            {"x": -2.4, "z": -8.2, "w": 10.4, "d": 8.8, "label": "검수/대기"},
            {"x": 7.8, "z": 4.2, "w": 13.4, "d": 8.8, "label": "피킹/적재"},
            {"x": -10.4, "z": 6.8, "w": 14.8, "d": 6.4, "label": "출입 동선"},
        ],
        "columns": [
            [-18, -10], [-10, -10], [-2, -10], [6, -10], [14, -10],
            [-18, 0], [-10, 0], [-2, 0], [6, 0], [14, 0],
            [-18, 10], [-10, 10], [-2, 10], [6, 10], [14, 10],
        ],
    },
    "2층": {
        "source": "FAC-002",
        "name": "2층 포장/부자재 기준",
        "width": 43,
        "depth": 26,
        "entrances": [{"x": 16.2, "z": 13.05, "w": 4.2, "d": 0.32, "label": "계단 출입"}],
        "cores": [{"x": 14.2, "z": -8.4, "w": 5.2, "d": 4.6, "h": 2.2, "label": "계단/설비 코어"}],
        "rooms": [
            {"x": -13.8, "z": -7.4, "w": 12.6, "d": 8.4, "label": "포장 작업"},
            {"x": 0.2, "z": -7.2, "w": 10.8, "d": 8.2, "label": "부자재"},
            {"x": -11.4, "z": 5.8, "w": 11.4, "d": 7.2, "label": "반제품"},
            {"x": 4.8, "z": 5.8, "w": 15.2, "d": 7.2, "label": "랙 배치"},
        ],
        "columns": [
            [-16, -9], [-8, -9], [0, -9], [8, -9], [16, -9],
            [-16, 0], [-8, 0], [0, 0], [8, 0], [16, 0],
            [-16, 9], [-8, 9], [0, 9], [8, 9], [16, 9],
        ],
    },
    "3층": {
        "source": "FAC-003",
        "name": "3층 완제품/재고 기준",
        "width": 41,
        "depth": 25,
        "entrances": [{"x": 15.2, "z": 12.55, "w": 4.0, "d": 0.32, "label": "계단 출입"}],
        "cores": [{"x": 13.6, "z": -7.8, "w": 5.0, "d": 4.4, "h": 2.2, "label": "계단/설비 코어"}],
        "rooms": [
            {"x": -11.6, "z": -6.6, "w": 14.0, "d": 8.0, "label": "완제품"},
            {"x": 3.8, "z": -6.6, "w": 10.6, "d": 8.0, "label": "검사 대기"},
            {"x": -11.2, "z": 5.6, "w": 13.4, "d": 7.0, "label": "시즌 재고"},
            {"x": 4.6, "z": 5.4, "w": 12.4, "d": 7.2, "label": "저회전"},
        ],
        "columns": [
            [-15, -8], [-7.5, -8], [0, -8], [7.5, -8], [15, -8],
            [-15, 0], [-7.5, 0], [0, 0], [7.5, 0], [15, 0],
            [-15, 8], [-7.5, 8], [0, 8], [7.5, 8], [15, 8],
        ],
    },
    "4층": {
        "source": "FAC-004",
        "name": "4층 장기보관/확장 기준",
        "width": 38,
        "depth": 23,
        "entrances": [{"x": 13.8, "z": 11.55, "w": 3.8, "d": 0.32, "label": "계단 출입"}],
        "cores": [{"x": 12.2, "z": -7.0, "w": 4.8, "d": 4.2, "h": 2.0, "label": "계단/설비 코어"}],
        "rooms": [
            {"x": -10.8, "z": -5.8, "w": 12.2, "d": 7.0, "label": "장기보관"},
            {"x": 2.8, "z": -5.8, "w": 9.2, "d": 7.0, "label": "예비 랙"},
            {"x": -9.2, "z": 5.2, "w": 12.8, "d": 6.2, "label": "확장 구역"},
            {"x": 5.4, "z": 5.0, "w": 8.8, "d": 6.4, "label": "보류품"},
        ],
        "columns": [
            [-14, -7], [-7, -7], [0, -7], [7, -7], [14, -7],
            [-14, 0], [-7, 0], [0, 0], [7, 0], [14, 0],
            [-14, 7], [-7, 7], [0, 7], [7, 7], [14, 7],
        ],
    },
    "5층": {
        "source": "FAC-005",
        "name": "5층 옥상/설비 기준",
        "width": 39.4,
        "depth": 25.0,
        "wallHeight": 0.9,
        "entrances": [{"x": -8.8, "z": 12.55, "w": 3.8, "d": 0.32, "label": "옥상 출입"}],
        "cores": [
            {"x": -9.8, "z": 8.1, "w": 5.6, "d": 3.4, "h": 1.9, "label": "계단실"},
            {"x": 11.6, "z": -7.0, "w": 5.4, "d": 4.2, "h": 1.8, "label": "승강기/설비"},
        ],
        "rooms": [
            {"x": -8.6, "z": 1.6, "w": 7.2, "d": 9.8, "label": "옥상 조경"},
            {"x": 5.2, "z": 3.0, "w": 15.8, "d": 11.4, "label": "옥상 작업"},
            {"x": -8.0, "z": -7.4, "w": 8.6, "d": 6.2, "label": "임시 보관"},
            {"x": 7.8, "z": -7.6, "w": 8.4, "d": 5.8, "label": "설비 주변"},
        ],
        "features": [
            {"x": -8.6, "z": 0.6, "w": 5.6, "d": 9.4, "h": 0.22, "kind": "garden", "label": "옥상 조경"},
            {"x": -9.8, "z": 8.1, "w": 5.6, "d": 3.4, "h": 2.35, "kind": "equipment", "label": "옥탑 계단실"},
            {"x": 11.6, "z": -7.0, "w": 5.4, "d": 4.2, "h": 1.9, "kind": "equipment", "label": "승강기/설비"},
            {"x": 7.6, "z": 4.2, "w": 13.8, "d": 9.2, "h": 0.16, "kind": "zone", "label": "옥상 작업 구역"},
            {"x": -17.8, "z": -9.6, "w": 7.9, "d": 3.3, "h": 0.18, "kind": "detail", "label": "도면 상세 구획"},
        ],
        "columns": [
            [-14, -7.2], [-7, -7.2], [0, -7.2], [7, -7.2], [14, -7.2],
            [-14, 0], [-7, 0], [0, 0], [7, 0], [14, 0],
            [-14, 7.2], [-7, 7.2], [0, 7.2], [7, 7.2], [14, 7.2],
        ],
    },
}


def render_warehouse3d_page() -> None:
    inject_warehouse3d_css()
    st.markdown('<div class="warehouse3d-title">3D 창고관리</div>', unsafe_allow_html=True)

    if not warehouse_available():
        st.error(WAREHOUSE_IMPORT_ERROR or "창고관리 DB를 초기화하지 못했습니다.")
        return

    inventory_rows, work_date = fetch_latest_warehouse_inventory()
    building_col, _ = st.columns([1.05, 2.3], gap="small")
    building_options = list(LOCATIONS)
    if st.session_state.get("warehouse3d_building") not in building_options:
        st.session_state["warehouse3d_building"] = building_options[0]
    with building_col:
        building = st.selectbox("위치 선택", building_options, key="warehouse3d_building")
    default_floor = LOCATIONS[building]["default_floor"]
    floor = default_floor
    drawing_mode = "3D 배치"
    drawing = {"name": "", "source": "", "src": "", "kind": "", "available": False}
    racks = build_rack_layout(inventory_rows, floor)
    summary = warehouse_summary(racks, inventory_rows)
    render_summary(summary, work_date)

    scene_tab, stock_tab = st.tabs(["3D 배치", "재고 위치표"])
    with scene_tab:
        components.html(
            warehouse_scene3d_html(
                building=building,
                floor=floor,
                drawing_mode=drawing_mode,
                drawing=drawing,
                racks=racks,
                zones=FLOOR_ZONES.get(floor, []),
                inventory_rows=inventory_rows,
            ),
            height=760,
            scrolling=False,
        )
    with stock_tab:
        components.html(
            warehouse_stock_position_html(building=building, inventory_rows=inventory_rows),
            height=760,
            scrolling=False,
        )


def warehouse_stock_position_html(building: str, inventory_rows: list[dict]) -> str:
    floors = LOCATIONS[building]["floors"]
    floor_payload = json.dumps(
        {level: build_rack_layout(inventory_rows, level) for level in floors},
        ensure_ascii=False,
    )
    floors_payload = json.dumps(floors, ensure_ascii=False)
    base_storage_key = f"warehouseRackLayout:{building}:"

    return f"""
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">

        <style>
            * {{ box-sizing: border-box; letter-spacing: 0; }}
            body {{
                background: transparent;
                color: #f2fffb;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                margin: 0;
                overflow: hidden;
            }}
            .stock-board {{
                background: rgba(6, 48, 43, 0.66);
                border: 1px solid rgba(87, 178, 165, 0.28);
                border-radius: 8px;
                display: flex;
                flex-direction: column;
                height: 744px;
                min-height: 0;
                padding: 0.88rem;
            }}
            .stock-head {{
                align-items: center;
                display: flex;
                gap: 0.7rem;
                justify-content: space-between;
                margin-bottom: 0.7rem;
            }}
            h3 {{
                color: #ffffff;
                font-size: 1rem;
                margin: 0;
            }}
            .stock-head span {{
                color: #b2d5cd;
                font-size: 0.74rem;
                font-weight: 850;
            }}
            .stock-tools {{
                display: grid;
                gap: 0.45rem;
                grid-template-columns: minmax(180px, 1fr) 130px 86px;
                margin-bottom: 0.7rem;
            }}
            input,
            select,
            button {{
                background: #171a22;
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 7px;
                color: #ffffff;
                font-size: 0.78rem;
                font-weight: 850;
                min-height: 34px;
                outline: 0;
                padding: 0 0.6rem;
            }}
            button {{ cursor: pointer; }}
            .stock-table {{
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                flex: 1 1 auto;
                min-height: 0;
                overflow: auto;
            }}
            table {{
                border-collapse: collapse;
                font-size: 0.76rem;
                width: 100%;
            }}
            th,
            td {{
                border-bottom: 1px solid rgba(126, 197, 185, 0.14);
                color: #f2fffb;
                padding: 0.5rem;
                text-align: left;
            }}
            th {{
                background: rgba(255, 255, 255, 0.07);
                color: #cfe8e2;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #b2d5cd;
                padding: 1rem;
                text-align: center;
            }}
            .stock-foot {{
                color: #b2d5cd;
                font-size: 0.72rem;
                font-weight: 850;
                margin-top: 0.62rem;
            }}
        </style>
    </head>
    <body>
        <section class="stock-board">
            <div class="stock-head">
                <h3>재고 위치표</h3>
                <span>{escape(building)} 전체 층 기준</span>
            </div>
            <div class="stock-tools">
                <input id="stockSearch" type="search" placeholder="상품명, 바코드, 층, 랙, 위치 검색">
                <select id="floorFilter"></select>
                <button type="button" id="refreshStock">새로고침</button>
            </div>
            <div class="stock-table">
                <table>
                    <thead>
                        <tr>
                            <th>위치</th>
                            <th>층</th>
                            <th>보관위치</th>
                            <th>형태</th>
                            <th>상품명</th>
                            <th>바코드</th>
                            <th>수량</th>
                        </tr>
                    </thead>
                    <tbody id="stockBody"></tbody>
                </table>
            </div>
            <div class="stock-foot" id="stockFoot"></div>
        </section>
        <script>
            const activeBuilding = {json.dumps(building, ensure_ascii=False)};
            const floors = {floors_payload};
            const defaultRacksByFloor = {floor_payload};
            const baseStorageKey = {json.dumps(base_storage_key, ensure_ascii=False)};
            const stockBody = document.getElementById("stockBody");
            const stockFoot = document.getElementById("stockFoot");
            const stockSearch = document.getElementById("stockSearch");
            const floorFilter = document.getElementById("floorFilter");

            function escapeHtml(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;");
            }}

            function storageKeyFor(floorName) {{
                return `${{baseStorageKey}}${{floorName}}`;
            }}

            function fixtureStorageKeyFor(floorName) {{
                return `${{baseStorageKey}}fixtures:${{floorName}}`;
            }}

            function loadJson(key, fallback) {{
                try {{
                    const value = JSON.parse(localStorage.getItem(key) || "null");
                    return value ?? fallback;
                }} catch (error) {{
                    return fallback;
                }}
            }}

            function loadRacks(floorName) {{
                const saved = loadJson(storageKeyFor(floorName), null);
                return Array.isArray(saved) ? saved : (defaultRacksByFloor[floorName] || []);
            }}

            function loadFixtures(floorName) {{
                const saved = loadJson(fixtureStorageKeyFor(floorName), []);
                return Array.isArray(saved) ? saved : [];
            }}

            function rackIsRoofOnly(rack) {{
                return Boolean(rack?.roofOnly);
            }}

            function partOptionsFor(rack) {{
                const levels = [2, 3].includes(Number(rack?.levels)) ? Number(rack.levels) : 2;
                const roofPart = `${{levels}}단 지붕칸`;
                if (rackIsRoofOnly(rack)) return [roofPart];
                const bottomOpen = Boolean(rack?.bottomOpen);
                if (levels === 2) return bottomOpen ? ["2단", roofPart] : ["1단", "2단", roofPart];
                if (levels === 3) return bottomOpen ? ["2단", "3단", roofPart] : ["1단", "2단", "3단", roofPart];
                return ["1단", roofPart];
            }}

            function shapeLabel(shape) {{
                return shape === "pallet" || shape === "wrapped_pallet" ? "파렛트" : "박스";
            }}

            function stackLabel(stack) {{
                const count = Math.max(1, Math.min(2, Number(stack || 1)));
                return count > 1 ? `${{count}}중` : "1중";
            }}

            function quantityOf(item) {{
                return Number(item?.qty || item?.stock || 0);
            }}

            function addRow(rows, floorName, location, shape, name, barcode, qty, stack = 1) {{
                const product = String(name || "").trim();
                const itemBarcode = String(barcode || "").trim();
                const count = Number(qty || 0);
                if (!product || !count) return;
                const type = shapeLabel(shape);
                const typeText = type === "파렛트" ? `${{type}} ${{stackLabel(stack)}}` : type;
                const key = `${{floorName}}::${{location}}::${{typeText}}::${{itemBarcode || product}}`;
                const existing = rows.get(key);
                if (existing) {{
                    existing.qty += count;
                    return;
                }}
                rows.set(key, {{
                    building: activeBuilding,
                    floor: floorName,
                    location,
                    type: typeText,
                    name: product,
                    barcode: itemBarcode,
                    qty: count,
                }});
            }}

            function collectRows() {{
                const rows = new Map();
                floors.forEach(floorName => {{
                    loadRacks(floorName).forEach(rack => {{
                        const parts = partOptionsFor(rack);
                        (rack.items || []).forEach((item, index) => {{
                            const part = item.part || parts[index % parts.length] || "1단";
                            const location = `${{rack.id || "랙"}} / ${{part}}`;
                            addRow(rows, floorName, location, item.shape || "box", item.name, item.barcode, quantityOf(item), item.stack || 1);
                            if ((item.shape === "pallet" || item.shape === "wrapped_pallet") && Array.isArray(item.items)) {{
                                item.items.forEach(innerItem => {{
                                    addRow(rows, floorName, `${{location}} / 파렛트 내부`, innerItem.shape || "box", innerItem.name, innerItem.barcode, quantityOf(innerItem), innerItem.stack || 1);
                                }});
                            }}
                        }});
                    }});
                    loadFixtures(floorName).forEach(fixture => {{
                        if (!["box", "pallet", "wrapped_pallet"].includes(fixture.type)) return;
                        const location = `바닥 X${{Number(fixture.x || 0).toFixed(0)}} Y${{Number(fixture.y || 0).toFixed(0)}}`;
                        addRow(rows, floorName, location, fixture.type, fixture.label, fixture.barcode, Number(fixture.qty || 1), fixture.stack || 1);
                        if (fixture.type === "pallet" && Array.isArray(fixture.items)) {{
                            fixture.items.forEach(innerItem => {{
                                addRow(rows, floorName, `${{location}} / 파렛트 내부`, innerItem.shape || "box", innerItem.name, innerItem.barcode, quantityOf(innerItem), innerItem.stack || 1);
                            }});
                        }}
                    }});
                }});
                return Array.from(rows.values()).sort((a, b) =>
                    a.floor.localeCompare(b.floor, "ko-KR") ||
                    a.location.localeCompare(b.location, "ko-KR") ||
                    a.name.localeCompare(b.name, "ko-KR")
                );
            }}

            function renderFloorFilter() {{
                floorFilter.innerHTML = '<option value="">전체 층</option>' + floors.map(floorName => `<option value="${{floorName}}">${{floorName}}</option>`).join("");
            }}

            function renderRows() {{
                const query = stockSearch.value.trim().toLowerCase();
                const selectedFloor = floorFilter.value;
                const rows = collectRows().filter(row => {{
                    if (selectedFloor && row.floor !== selectedFloor) return false;
                    if (!query) return true;
                    return [row.building, row.floor, row.location, row.type, row.name, row.barcode].join(" ").toLowerCase().includes(query);
                }});
                if (!rows.length) {{
                    stockBody.innerHTML = '<tr><td colspan="7" class="empty">표시할 재고 위치가 없습니다.</td></tr>';
                    stockFoot.textContent = "0개 위치 / 총 0개";
                    return;
                }}
                const totalQty = rows.reduce((sum, row) => sum + Number(row.qty || 0), 0);
                stockBody.innerHTML = rows.map(row => `
                    <tr>
                        <td>${{escapeHtml(row.building)}}</td>
                        <td>${{escapeHtml(row.floor)}}</td>
                        <td>${{escapeHtml(row.location)}}</td>
                        <td>${{escapeHtml(row.type)}}</td>
                        <td>${{escapeHtml(row.name)}}</td>
                        <td>${{escapeHtml(row.barcode || "-")}}</td>
                        <td>${{Number(row.qty || 0).toLocaleString("ko-KR")}}개</td>
                    </tr>
                `).join("");
                stockFoot.textContent = `${{rows.length.toLocaleString("ko-KR")}}개 위치 / 총 ${{totalQty.toLocaleString("ko-KR")}}개`;
            }}

            renderFloorFilter();
            renderRows();
            stockSearch.addEventListener("input", renderRows);
            floorFilter.addEventListener("change", renderRows);
            document.getElementById("refreshStock").addEventListener("click", renderRows);
        </script>
    </body>
    </html>
    """


def warehouse_available() -> bool:
    if init_db is None or SessionLocal is None or services is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global WAREHOUSE_IMPORT_ERROR
        WAREHOUSE_IMPORT_ERROR = f"창고관리 DB 초기화 실패: {exc}"
        return False
    return True


def with_db(action):
    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        return action(db)
    finally:
        db.close()


def fetch_latest_warehouse_inventory() -> tuple[list[dict], str]:
    def action(db):
        dates = services.list_work_dates(db, "창고")
        if not dates:
            return [], ""
        work_date = dates[0]
        rows = [services.daily_to_dict(row) for row in services.list_daily(db, "창고", work_date)]
        return rows, work_date.isoformat()

    return with_db(action) or ([], "")


def build_rack_layout(inventory_rows: list[dict], floor: str) -> list[dict]:
    rack_names = [f"{zone}-{index:02d}" for zone in ("A", "B", "C", "D") for index in range(1, 7)]
    sorted_items = sorted(
        inventory_rows,
        key=lambda row: (int(row.get("current_stock") or 0), row.get("product_name", "")),
        reverse=True,
    )
    racks = []
    for rack_index, rack_name in enumerate(rack_names):
        assigned = sorted_items[rack_index::len(rack_names)][:4]
        current_stock = sum(int(row.get("current_stock") or 0) for row in assigned)
        safe_stock = sum(int(row.get("safe_stock") or 0) for row in assigned)
        status = "empty"
        if current_stock > 0:
            status = "short" if safe_stock and current_stock <= safe_stock else "normal"
        racks.append(
            {
                "id": rack_name,
                "floor": floor,
                "zone": rack_name.split("-", 1)[0],
                "level_count": 4,
                "current_stock": current_stock,
                "safe_stock": safe_stock,
                "status": status,
                "items": [
                    {
                        "name": row.get("product_name", ""),
                        "barcode": row.get("barcode", ""),
                        "stock": int(row.get("current_stock") or 0),
                        "safe": int(row.get("safe_stock") or 0),
                        "status": row.get("stock_status", ""),
                    }
                    for row in assigned
                ],
            }
        )
    return racks


def warehouse_summary(racks: list[dict], inventory_rows: list[dict]) -> dict:
    occupied = sum(1 for rack in racks if rack["current_stock"] > 0)
    shortage = sum(1 for rack in racks if rack["status"] == "short")
    total_stock = sum(int(row.get("current_stock") or 0) for row in inventory_rows)
    return {
        "rack_count": len(racks),
        "occupied": occupied,
        "empty": len(racks) - occupied,
        "shortage": shortage,
        "sku_count": len(inventory_rows),
        "total_stock": total_stock,
    }


def resolve_drawing(building: str, uploaded_file) -> dict:
    if uploaded_file is not None:
        data = uploaded_file.getvalue()
        file_type = getattr(uploaded_file, "type", "") or ""
        if file_type.startswith("image/"):
            return {
                "name": uploaded_file.name,
                "source": "업로드 도면 이미지",
                "src": image_data_uri(data, file_type),
                "kind": "image",
                "available": True,
            }
        return {
            "name": uploaded_file.name,
            "source": "업로드 도면",
            "src": pdf_data_uri(data),
            "kind": "pdf",
            "available": True,
        }

    default_path = LOCATIONS[building].get("default_drawing")
    if default_path and Path(default_path).exists():
        data = Path(default_path).read_bytes()
        return {
            "name": Path(default_path).name,
            "source": "기본 도면",
            "src": pdf_data_uri(data),
            "kind": "pdf",
            "available": True,
        }

    return {"name": "도면 미연결", "source": "-", "src": "", "kind": "", "available": False}


def pdf_data_uri(file_bytes: bytes) -> str:
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"data:application/pdf;base64,{encoded}"


def image_data_uri(file_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def render_summary(summary: dict, work_date: str) -> None:
    cards = [
        ("기준일", work_date or "-"),
        ("SKU", f'{summary["sku_count"]:,}개'),
        ("총 현재고", f'{summary["total_stock"]:,}개'),
        ("사용 랙", f'{summary["occupied"]:,}/{summary["rack_count"]:,}'),
        ("부족 랙", f'{summary["shortage"]:,}'),
    ]
    html = "".join(
        f"""
        <article class="warehouse3d-kpi">
            <span>{escape(label)}</span>
            <strong>{escape(value)}</strong>
        </article>
        """
        for label, value in cards
    )
    components.html(
        f"""
        <!doctype html>
        <html lang="ko">
        <head>
            <meta charset="utf-8">
            <style>
                * {{ box-sizing: border-box; }}
                body {{
                    background: transparent;
                    font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                    margin: 0;
                    overflow: hidden;
                }}
                .warehouse3d-kpi-grid {{
                    display: grid;
                    gap: 0.48rem;
                    grid-template-columns: repeat(5, minmax(0, 1fr));
                    width: 100%;
                }}
                .warehouse3d-kpi {{
                    background: rgba(7, 58, 52, 0.68);
                    border: 1px solid rgba(87, 178, 165, 0.25);
                    border-radius: 8px;
                    min-height: 70px;
                    padding: 0.68rem;
                }}
                .warehouse3d-kpi span {{
                    color: #b2d5cd;
                    display: block;
                    font-size: 0.72rem;
                    font-weight: 900;
                    margin-bottom: 0.28rem;
                }}
                .warehouse3d-kpi strong {{
                    color: #ffffff;
                    display: block;
                    font-size: 0.94rem;
                    font-weight: 950;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }}
                @media (max-width: 1100px) {{
                    .warehouse3d-kpi-grid {{
                        grid-template-columns: repeat(3, minmax(0, 1fr));
                    }}
                }}
            </style>
        </head>
        <body><div class="warehouse3d-kpi-grid">{html}</div></body>
        </html>
        """,
        height=86,
        scrolling=False,
    )


def drawing_layer_html(drawing: dict) -> str:
    src = drawing.get("src", "")
    kind = drawing.get("kind", "")
    name = drawing.get("name", "도면 미연결")
    if kind == "image" and src:
        return f'<img class="drawing-image" src="{escape(src, quote=True)}" alt="{escape(name, quote=True)}">'
    if kind == "pdf" and src:
        return (
            f'<object class="drawing-pdf" data="{escape(src, quote=True)}#toolbar=0&navpanes=0&view=FitH" '
            'type="application/pdf">'
            '<div class="drawing-reference">'
            f'<span>{escape(name)}<br>PDF 도면을 표시할 수 없습니다.<br>'
            'PNG/JPG로 변환한 도면을 업로드하면 도면 위에 랙을 배치할 수 있습니다.</span>'
            '</div>'
            '</object>'
        )
    return '<div class="drawing-reference"><span>도면 이미지를 업로드하면 이 영역에 배경으로 표시됩니다.</span></div>'


def warehouse_scene_html(
    building: str,
    floor: str,
    drawing_mode: str,
    drawing: dict,
    racks: list[dict],
    zones: list[str],
    inventory_rows: list[dict],
) -> str:
    payload = json.dumps(racks, ensure_ascii=False)
    floor_payload = json.dumps(
        {level: build_rack_layout(inventory_rows, level) for level in LOCATIONS[building]["floors"]},
        ensure_ascii=False,
    )
    zones_payload = json.dumps(
        {level: FLOOR_ZONES.get(level, []) for level in LOCATIONS[building]["floors"]},
        ensure_ascii=False,
    )
    floor_model_payload = json.dumps(FLOOR_MODELS, ensure_ascii=False)
    inventory_payload = json.dumps(
        [
            {
                "name": row.get("product_name", ""),
                "barcode": row.get("barcode", ""),
                "stock": int(row.get("current_stock") or 0),
                "status": row.get("stock_status", ""),
            }
            for row in inventory_rows
        ],
        ensure_ascii=False,
    )
    base_storage_key = f"warehouseRackLayout:{building}:"
    floors = LOCATIONS[building]["floors"]
    drawing_overlay = drawing_layer_html(drawing)
    drawing_badge = f'{drawing.get("source", "-")} · {drawing.get("name", "도면 미연결")}'
    floor_stack = "".join(
        f'<button class="floor-chip {"active" if level == floor else ""}" data-floor="{escape(level, quote=True)}" type="button">{escape(level)}</button>'
        for level in floors
    )
    zone_tags = "".join(f"<span>{escape(zone)}</span>" for zone in zones)

    return f"""
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <style>
            * {{ box-sizing: border-box; letter-spacing: 0; }}
            body {{
                background: transparent;
                color: #f2fffb;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                margin: 0;
                overflow: hidden;
            }}
            .warehouse-scene {{
                display: grid;
                gap: 0.72rem;
                grid-template-columns: 210px minmax(0, 1.35fr) minmax(340px, 0.82fr);
                height: 744px;
            }}
            .panel {{
                background: rgba(6, 48, 43, 0.66);
                border: 1px solid rgba(87, 178, 165, 0.28);
                border-radius: 8px;
                min-height: 0;
                overflow: hidden;
            }}
            .building-panel {{
                padding: 0.8rem;
            }}
            .building-panel h3,
            .rack-panel h3,
            .detail-panel h3 {{
                color: #ffffff;
                font-size: 0.96rem;
                margin: 0 0 0.56rem;
            }}
            .building-name {{
                color: #b2d5cd;
                font-size: 0.76rem;
                font-weight: 850;
                line-height: 1.45;
                margin-bottom: 0.7rem;
            }}
            .building-stack {{
                display: flex;
                flex-direction: column;
                gap: 0.28rem;
            }}
            .floor-chip {{
                background: #171a22;
                border: 1px solid rgba(126, 197, 185, 0.25);
                border-radius: 7px;
                color: #dffaf4;
                cursor: pointer;
                min-height: 34px;
                font-size: 0.78rem;
                font-weight: 900;
                padding: 0.52rem 0.6rem;
                text-align: center;
            }}
            .floor-chip.active {{
                background: rgba(22, 213, 198, 0.18);
                border-color: rgba(22, 213, 198, 0.58);
                color: #ffffff;
                box-shadow: inset 3px 0 0 #16d5c6;
            }}
            .zone-tags {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.32rem;
                margin-top: 0.8rem;
            }}
            .zone-tags span {{
                background: rgba(75, 156, 255, 0.14);
                border: 1px solid rgba(75, 156, 255, 0.28);
                border-radius: 999px;
                color: #d8ebff;
                font-size: 0.68rem;
                font-weight: 850;
                padding: 0.24rem 0.42rem;
            }}
            .rack-panel {{
                display: grid;
                grid-template-rows: auto auto minmax(0, 1fr);
                padding: 0.8rem;
            }}
            .scene-head {{
                align-items: center;
                display: flex;
                justify-content: space-between;
                margin-bottom: 0.58rem;
            }}
            .scene-head span {{
                color: #b2d5cd;
                font-size: 0.74rem;
                font-weight: 850;
            }}
            .scene-tools {{
                display: grid;
                gap: 0.42rem;
                grid-template-columns: 1.1fr repeat(6, minmax(0, 1fr));
                margin-bottom: 0.58rem;
            }}
            button,
            select,
            input {{
                background: #171a22;
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 7px;
                color: #ffffff;
                font-size: 0.76rem;
                font-weight: 850;
                min-height: 34px;
                outline: 0;
                padding: 0 0.55rem;
            }}
            button {{
                cursor: pointer;
            }}
            button:hover {{
                border-color: rgba(117, 236, 219, 0.58);
            }}
            .floor-plan {{
                background:
                    linear-gradient(rgba(143, 247, 232, 0.07) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(143, 247, 232, 0.07) 1px, transparent 1px),
                    rgba(2, 20, 19, 0.72);
                background-size: 36px 36px;
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 8px;
                min-height: 0;
                overflow: hidden;
                position: relative;
                user-select: none;
            }}
            .drawing-pdf,
            .drawing-image {{
                height: 100%;
                inset: 0;
                object-fit: contain;
                opacity: 0.82;
                pointer-events: none;
                position: absolute;
                width: 100%;
                z-index: 0;
            }}
            .drawing-reference {{
                align-items: center;
                color: #dffaf4;
                display: flex;
                font-size: 0.9rem;
                font-weight: 900;
                inset: 0;
                justify-content: center;
                line-height: 1.5;
                padding: 1rem;
                position: absolute;
                text-align: center;
                z-index: 0;
            }}
            .drawing-reference span {{
                background: rgba(3, 27, 24, 0.82);
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 8px;
                padding: 0.82rem 1rem;
            }}
            .plan-label {{
                background: rgba(3, 27, 24, 0.86);
                border: 1px solid rgba(126, 197, 185, 0.24);
                border-radius: 6px;
                color: #b2d5cd;
                font-size: 0.68rem;
                font-weight: 900;
                padding: 0.32rem 0.42rem;
                position: absolute;
                z-index: 2;
            }}
            .company-label {{ left: 1rem; top: 1rem; }}
            .entrance-label {{ bottom: 1rem; left: 1rem; }}
            .rack-grid {{
                inset: 0;
                position: absolute;
                z-index: 3;
            }}
            .rack {{
                background: linear-gradient(145deg, rgba(20, 132, 118, 0.72), rgba(6, 55, 50, 0.92));
                border: 1px solid rgba(143, 247, 232, 0.38);
                border-radius: 5px;
                box-shadow: 0 8px 18px rgba(0, 0, 0, 0.24);
                color: #ffffff;
                cursor: pointer;
                min-height: 34px;
                padding: 0.42rem;
                position: absolute;
                touch-action: none;
                transition: transform 0.12s ease, border-color 0.12s ease;
            }}
            .rack:hover,
            .rack.active {{
                border-color: #16d5c6;
                outline: 2px solid rgba(22, 213, 198, 0.28);
            }}
            .rack.short {{
                background: linear-gradient(145deg, rgba(255, 76, 76, 0.54), rgba(72, 24, 24, 0.9));
                border-color: rgba(255, 140, 140, 0.6);
            }}
            .rack.empty {{
                background: linear-gradient(145deg, rgba(215, 228, 226, 0.12), rgba(5, 36, 33, 0.84));
                border-style: dashed;
            }}
            .rack strong {{
                display: block;
                font-size: 0.72rem;
            }}
            .rack span {{
                color: #dffaf4;
                display: block;
                font-size: 0.62rem;
                font-weight: 900;
                margin-top: 0.16rem;
            }}
            .detail-panel {{
                display: flex;
                flex-direction: column;
                padding: 0.8rem;
            }}
            .rack-detail {{
                background: rgba(2, 20, 19, 0.5);
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                margin-bottom: 0.7rem;
                padding: 0.68rem;
            }}
            .rack-detail strong {{
                color: #ffffff;
                display: block;
                font-size: 1.05rem;
            }}
            .rack-detail span {{
                color: #b2d5cd;
                display: block;
                font-size: 0.76rem;
                font-weight: 800;
                margin-top: 0.26rem;
            }}
            .item-list {{
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                flex: 1 1 auto;
                min-height: 0;
                overflow: auto;
            }}
            .assign-box {{
                display: grid;
                gap: 0.42rem;
                grid-template-columns: minmax(0, 1fr) 74px 72px;
                margin-bottom: 0.68rem;
            }}
            table {{
                border-collapse: collapse;
                font-size: 0.72rem;
                width: 100%;
            }}
            th,
            td {{
                border-bottom: 1px solid rgba(126, 197, 185, 0.14);
                color: #f2fffb;
                padding: 0.42rem;
                text-align: left;
            }}
            th {{
                background: rgba(255, 255, 255, 0.07);
                color: #cfe8e2;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #b2d5cd;
                text-align: center;
            }}
            @media (max-width: 980px) {{
                .warehouse-scene {{
                    grid-template-columns: 1fr;
                    height: auto;
                }}
                .floor-plan {{
                    height: 520px;
                }}
                .detail-panel {{
                    min-height: 360px;
                }}
            }}
        </style>
    </head>
    <body>
        <main class="warehouse-scene">
            <section class="panel building-panel">
                <h3>건물/층 선택</h3>
                <div class="building-name">{escape(building)}<br>{escape(LOCATIONS[building]["description"])}</div>
                <div class="building-stack">{floor_stack}</div>
            </section>
            <section class="panel rack-panel">
                <div class="scene-head">
                    <h3><span id="currentFloorLabel">{escape(floor)}</span> 도면 기반 랙 배치</h3>
                    <span>{escape(drawing_mode)} · {escape(drawing_badge)}</span>
                </div>
                <div class="scene-tools">
                    <select id="rackTypeSelect" aria-label="랙 종류">
                        <option value="light">경량랙 · 흰색 선반</option>
                        <option value="heavy">중량랙 · 파랑/주황</option>
                    </select>
                    <button type="button" id="addRack">랙 추가</button>
                    <button type="button" id="deleteRack">선택 랙 삭제</button>
                    <button type="button" id="lockRack">랙 고정</button>
                    <button type="button" id="resetRack">배치 초기화</button>
                </div>
                <div class="floor-plan">
                    {drawing_overlay}
                    <div class="plan-label company-label">도면 기준 랙 배치 영역</div>
                    <div class="plan-label entrance-label">회사 출입구 기준</div>
                    <div class="rack-grid" id="rackLayer"></div>
                </div>
            </section>
            <aside class="panel detail-panel">
                <h3>랙 적재 품목</h3>
                <div class="rack-detail" id="rackDetail">
                    <strong>랙을 선택하세요</strong>
                    <span>랙을 추가하거나 도면 위 랙을 클릭한 뒤 품목과 수량을 배정하세요.</span>
                </div>
                <div class="assign-box">
                    <select id="itemSelect"></select>
                    <input id="itemQty" type="number" min="1" value="1" aria-label="수량">
                    <button type="button" id="addItem">배정</button>
                </div>
                <div class="item-list">
                    <table>
                        <thead><tr><th>상품명</th><th>적재</th><th></th></tr></thead>
                        <tbody id="itemBody"><tr><td colspan="3" class="empty">선택된 랙이 없습니다.</td></tr></tbody>
                    </table>
                </div>
            </aside>
        </main>
        <script>
            const defaultRacks = {payload};
            const defaultRacksByFloor = {floor_payload};
            const zonesByFloor = {zones_payload};
            const floorModels = {floor_model_payload};
            const inventory = {inventory_payload};
            const baseStorageKey = {json.dumps(base_storage_key, ensure_ascii=False)};
            const activeBuilding = {json.dumps(building, ensure_ascii=False)};
            const locationFocus = {{
                "로긴": ["제조", "출입", "옥상"],
                "포장부서": ["포장", "작업", "부자재", "반제품"],
                "창고1": ["피킹", "완제품", "랙 배치", "장기보관"],
                "창고2": ["예비", "저회전", "임시 보관"],
                "NC층": ["설비", "코어"],
            }};
            const floors = {json.dumps(floors, ensure_ascii=False)};
            let activeFloor = {json.dumps(floor, ensure_ascii=False)};
            const rackLayer = document.getElementById("rackLayer");
            const rackDetail = document.getElementById("rackDetail");
            const itemBody = document.getElementById("itemBody");
            const itemSelect = document.getElementById("itemSelect");
            const itemQty = document.getElementById("itemQty");
            const partSelect = document.getElementById("partSelect");
            const rackTypeSelect = document.getElementById("rackTypeSelect");
            const lockButton = document.getElementById("lockRack");
            const deleteButton = document.getElementById("deleteRack");
            const currentFloorLabel = document.getElementById("currentFloorLabel");
            let racks = loadLayout(activeFloor);
            let selectedRackId = racks[0]?.id || "";

            function escapeHtml(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;");
            }}

            function storageKeyFor(floorName) {{
                return `${{baseStorageKey}}${{floorName}}`;
            }}

            function fixtureStorageKeyFor(floorName) {{
                return `${{baseStorageKey}}fixtures:${{floorName}}`;
            }}

            function floorSizeStorageKeyFor(floorName) {{
                return `${{baseStorageKey}}floorSize:${{floorName}}`;
            }}

            function baseFloorSize(floorName) {{
                const model = floorModels[floorName] || floorModels["1층"] || {{}};
                return {{
                    width: Number(model.width || 44),
                    depth: Number(model.depth || 27),
                }};
            }}

            function loadFloorSize(floorName) {{
                const base = baseFloorSize(floorName);
                try {{
                    const saved = JSON.parse(localStorage.getItem(floorSizeStorageKeyFor(floorName)) || "null");
                    if (saved && Number.isFinite(Number(saved.width)) && Number.isFinite(Number(saved.depth))) {{
                        return {{
                            width: clamp(Number(saved.width), base.width * 0.7, base.width * 2.2),
                            depth: clamp(Number(saved.depth), base.depth * 0.7, base.depth * 2.2),
                        }};
                    }}
                }} catch (error) {{}}
                return base;
            }}

            function saveFloorSize(floorName, size) {{
                localStorage.setItem(floorSizeStorageKeyFor(floorName), JSON.stringify(size));
            }}

            function currentFloorSize() {{
                return loadFloorSize(activeFloor);
            }}

            function normalizeFixture(fixture, index = 0) {{
                const template = fixtureDefaults[fixture?.type] || fixtureDefaults.entrance;
                return {{
                    ...template,
                    ...fixture,
                    id: fixture?.id || `F-${{String(index + 1).padStart(2, "0")}}`,
                    label: fixture?.label || template.label,
                    x: Number.isFinite(Number(fixture?.x)) ? Number(fixture.x) : 50,
                    y: Number.isFinite(Number(fixture?.y)) ? Number(fixture.y) : 50,
                    qty: Math.max(1, Number(fixture?.qty || 1)),
                    stack: clamp(Number(fixture?.stack || 1), 1, 2),
                    items: Array.isArray(fixture?.items) ? fixture.items : [],
                    rotation: Number.isFinite(Number(fixture?.rotation)) ? Number(fixture.rotation) : 0,
                    locked: Boolean(fixture?.locked),
                }};
            }}

            function defaultLayout(floorName) {{
                const source = defaultRacksByFloor[floorName] || defaultRacks;
                return source.map((rack, index) => ({{
                    ...rack,
                    x: 8 + (index % 6) * 13.2,
                    y: 16 + Math.floor(index / 6) * 15.2,
                    w: 10.8,
                    h: 8.4,
                    items: rack.items || [],
                }}));
            }}

            function nextRackIdFromSet(existingIds, start = 1) {{
                let number = Math.max(1, Number(start) || 1);
                let id = "";
                do {{
                    id = `R-${{String(number).padStart(2, "0")}}`;
                    number += 1;
                }} while (existingIds.has(id));
                return id;
            }}

            function nextRackId() {{
                const existingIds = new Set(racks.map(rack => String(rack.id || "").trim()).filter(Boolean));
                const maxNumber = racks.reduce((max, rack) => {{
                    const match = String(rack.id || "").match(/^R-(\\d+)$/);
                    return match ? Math.max(max, Number(match[1]) || 0) : max;
                }}, 0);
                return nextRackIdFromSet(existingIds, Math.max(racks.length + 1, maxNumber + 1));
            }}

            function normalizeRackIds(layout) {{
                const existingIds = new Set();
                return (Array.isArray(layout) ? layout : []).map((rack, index) => {{
                    const currentId = String(rack?.id || "").trim();
                    const id = currentId && !existingIds.has(currentId)
                        ? currentId
                        : nextRackIdFromSet(existingIds, index + 1);
                    rack.id = id;
                    existingIds.add(id);
                    return rack;
                }});
            }}

            function rackBounds(rack) {{
                const w = Math.max(1, Number(rack.w || 10.8));
                const h = Math.max(1, Number(rack.h || 8.4));
                const x = Number(rack.x || 50);
                const y = Number(rack.y || 50);
                return {{
                    left: x - w / 2,
                    right: x + w / 2,
                    top: y - h / 2,
                    bottom: y + h / 2,
                }};
            }}

            function racksOverlap(a, b, gap = 1.4) {{
                const first = rackBounds(a);
                const second = rackBounds(b);
                return !(
                    first.right + gap < second.left ||
                    first.left - gap > second.right ||
                    first.bottom + gap < second.top ||
                    first.top - gap > second.bottom
                );
            }}

            function findOpenRackPosition(width, height) {{
                const candidates = [];
                for (let y = 14; y <= 86; y += 12) {{
                    for (let x = 12; x <= 88; x += 13) {{
                        candidates.push({{ x, y }});
                    }}
                }}
                candidates.push({{ x: 50, y: 50 }});
                const size = {{ w: width, h: height }};
                const found = candidates.find(point => {{
                    const candidate = {{ ...size, x: point.x, y: point.y }};
                    return !racks.some(rack => racksOverlap(candidate, rack));
                }});
                return found || {{
                    x: clamp(12 + (racks.length * 11) % 76, 6, 94),
                    y: clamp(14 + (Math.floor(racks.length / 7) * 12) % 72, 8, 92),
                }};
            }}

            function loadLayout(floorName) {{
                try {{
                    const saved = JSON.parse(localStorage.getItem(storageKeyFor(floorName)) || "null");
                    if (Array.isArray(saved)) return normalizeRackIds(saved);
                }} catch (error) {{}}
                return normalizeRackIds(defaultLayout(floorName));
            }}

            function saveLayout() {{
                racks = normalizeRackIds(racks);
                localStorage.setItem(storageKeyFor(activeFloor), JSON.stringify(racks));
            }}

            function saveLayoutFor(floorName, floorRacks) {{
                localStorage.setItem(storageKeyFor(floorName), JSON.stringify(normalizeRackIds(floorRacks)));
            }}

            function loadFixtures(floorName) {{
                try {{
                    const saved = JSON.parse(localStorage.getItem(fixtureStorageKeyFor(floorName)) || "[]");
                    if (Array.isArray(saved)) return saved.map(normalizeFixture);
                }} catch (error) {{}}
                return [];
            }}

            function saveFixtures() {{
                localStorage.setItem(fixtureStorageKeyFor(activeFloor), JSON.stringify(fixtures));
            }}

            function saveFixturesFor(floorName, floorFixtures) {{
                localStorage.setItem(fixtureStorageKeyFor(floorName), JSON.stringify(floorFixtures));
            }}

            function renderFloorControls() {{
                document.querySelectorAll(".floor-chip").forEach(button => {{
                    button.classList.toggle("active", button.dataset.floor === activeFloor);
                }});
                currentFloorLabel.textContent = activeFloor;
            }}

            function rackStatus(rack) {{
                const total = rack.items.reduce((sum, item) => sum + Number(item.qty || item.stock || 0), 0);
                if (!total) return "empty";
                return rack.status === "short" ? "short" : "normal";
            }}

            function renderRackLayer() {{
                rackLayer.innerHTML = racks.map(rack => `
                    <button class="rack ${{rackStatus(rack)}} ${{rack.id === selectedRackId ? "active" : ""}}"
                        data-rack="${{escapeHtml(rack.id)}}"
                        style="left:${{rack.x}}%; top:${{rack.y}}%; width:${{rack.w}}%; height:${{rack.h}}%;"
                        type="button">
                        <strong>${{escapeHtml(rack.id)}}</strong>
                        <span>${{rack.items.length}}품목</span>
                    </button>
                `).join("");
                bindRackEvents();
            }}

            function selectedRack() {{
                return racks.find(rack => rack.id === selectedRackId);
            }}

            function renderRack(rack) {{
                selectedRackId = rack?.id || "";
                renderRackLayer();
                if (!rack) {{
                    rackDetail.innerHTML = "<strong>랙을 선택하세요</strong><span>선택된 랙이 없습니다.</span>";
                    itemBody.innerHTML = '<tr><td colspan="3" class="empty">선택된 랙이 없습니다.</td></tr>';
                    return;
                }}
                const loadedQty = rack.items.reduce((sum, item) => sum + Number(item.qty || item.stock || 0), 0);
                rackDetail.innerHTML = `
                    <strong>${{escapeHtml(rack.id)}} / ${{escapeHtml(activeFloor)}}</strong>
                    <span>위치 X ${{Number(rack.x).toFixed(1)}}%, Y ${{Number(rack.y).toFixed(1)}}% · 적재 ${{loadedQty.toLocaleString("ko-KR")}}개</span>
                `;
                if (!rack.items.length) {{
                    itemBody.innerHTML = '<tr><td colspan="3" class="empty">이 랙에 연결된 품목이 없습니다.</td></tr>';
                    return;
                }}
                itemBody.innerHTML = rack.items.map(item => `
                    <tr>
                        <td>${{escapeHtml(item.name)}}</td>
                        <td>${{Number(item.qty || item.stock || 0).toLocaleString("ko-KR")}}</td>
                        <td><button type="button" data-remove="${{escapeHtml(item.barcode || item.name)}}">삭제</button></td>
                    </tr>
                `).join("");
                itemBody.querySelectorAll("[data-remove]").forEach(button => {{
                    button.addEventListener("click", () => {{
                        const key = button.dataset.remove;
                        rack.items = rack.items.filter(item => (item.barcode || item.name) !== key);
                        saveLayout();
                        renderRack(rack);
                    }});
                }});
            }}

            function bindRackEvents() {{
                document.querySelectorAll(".rack").forEach(node => {{
                    node.addEventListener("click", () => {{
                        const rack = racks.find(target => target.id === node.dataset.rack);
                        renderRack(rack);
                    }});
                    node.addEventListener("pointerdown", event => startDrag(event, node.dataset.rack));
                }});
            }}

            function startDrag(event, rackId) {{
                event.preventDefault();
                const rack = racks.find(target => target.id === rackId);
                if (!rack) return;
                selectedRackId = rackId;
                const board = rackLayer.getBoundingClientRect();
                const offsetX = event.clientX - (board.left + (rack.x / 100) * board.width);
                const offsetY = event.clientY - (board.top + (rack.y / 100) * board.height);
                const move = moveEvent => {{
                    rack.x = Math.max(0, Math.min(96, ((moveEvent.clientX - board.left - offsetX) / board.width) * 100));
                    rack.y = Math.max(0, Math.min(94, ((moveEvent.clientY - board.top - offsetY) / board.height) * 100));
                    renderRackLayer();
                }};
                const up = () => {{
                    window.removeEventListener("pointermove", move);
                    window.removeEventListener("pointerup", up);
                    saveLayout();
                    renderRack(rack);
                }};
                window.addEventListener("pointermove", move);
                window.addEventListener("pointerup", up);
            }}

            function renderItemSelect() {{
                const emptyOption = '<option value="">직접입력 / 재고 선택 없음</option>';
                itemSelect.innerHTML = inventory.length
                    ? emptyOption + inventory.map((item, index) => `<option value="${{index}}">${{escapeHtml(item.name)}} / 현재고 ${{Number(item.stock || 0).toLocaleString("ko-KR")}}</option>`).join("")
                    : emptyOption;
            }}

            document.getElementById("addRack").addEventListener("click", () => {{
                const id = nextRackId();
                const rack = {{ id, floor: activeFloor, x: 42, y: 42, w: 11, h: 8, status: "empty", items: [] }};
                racks.push(rack);
                saveLayout();
                renderRack(rack);
            }});

            document.getElementById("deleteRack").addEventListener("click", () => {{
                if (!selectedRackId) return;
                racks = racks.filter(rack => rack.id !== selectedRackId);
                selectedRackId = racks[0]?.id || "";
                saveLayout();
                renderRack(selectedRack());
            }});

            document.getElementById("resetRack").addEventListener("click", () => {{
                racks = [];
                selectedRackId = "";
                saveLayout();
                renderRack(null);
            }});

            document.getElementById("addItem").addEventListener("click", () => {{
                const rack = selectedRack();
                const item = inventory[Number(itemSelect.value)];
                if (!rack || !item) return;
                const qty = Math.max(1, Number(itemQty.value || 1));
                const key = item.barcode || item.name;
                const existing = rack.items.find(row => (row.barcode || row.name) === key);
                if (existing) {{
                    existing.qty = Number(existing.qty || existing.stock || 0) + qty;
                }} else {{
                    rack.items.push({{ ...item, qty }});
                }}
                saveLayout();
                renderRack(rack);
            }});

            document.querySelectorAll(".floor-chip").forEach(button => {{
                button.addEventListener("click", () => {{
                    saveLayout();
                    activeFloor = button.dataset.floor;
                    racks = loadLayout(activeFloor);
                    selectedRackId = racks[0]?.id || "";
                    renderFloorControls();
                    renderRack(selectedRack());
                }});
            }});

            renderItemSelect();
            renderFloorControls();
            renderRack(selectedRack());
        </script>
    </body>
    </html>
    """


def warehouse_scene3d_html(
    building: str,
    floor: str,
    drawing_mode: str,
    drawing: dict,
    racks: list[dict],
    zones: list[str],
    inventory_rows: list[dict],
) -> str:
    payload = json.dumps(racks, ensure_ascii=False)
    floor_payload = json.dumps(
        {level: build_rack_layout(inventory_rows, level) for level in LOCATIONS[building]["floors"]},
        ensure_ascii=False,
    )
    zones_payload = json.dumps(
        {level: FLOOR_ZONES.get(level, []) for level in LOCATIONS[building]["floors"]},
        ensure_ascii=False,
    )
    floor_model_payload = json.dumps(FLOOR_MODELS, ensure_ascii=False)
    inventory_payload = json.dumps(
        [
            {
                "name": row.get("product_name", ""),
                "barcode": row.get("barcode", ""),
                "stock": int(row.get("current_stock") or 0),
                "status": row.get("stock_status", ""),
            }
            for row in inventory_rows
        ],
        ensure_ascii=False,
    )
    floor_heights = {"1층": 0, "2층": 3.2, "3층": 6.4, "4층": 9.6}
    base_storage_key = f"warehouseRackLayout:{building}:"
    floors = LOCATIONS[building]["floors"]
    drawing_badge = f'{drawing.get("source", "-")} · {drawing.get("name", "도면 미연결")}'
    floor_stack = "".join(
        f'<button class="floor-chip {"active" if level == floor else ""}" data-floor="{escape(level, quote=True)}" type="button">{escape(level)}</button>'
        for level in floors
    )
    zone_tags = "".join(f"<span>{escape(zone)}</span>" for zone in zones)

    return f"""
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <script type="importmap">
            {{"imports": {{"three": "https://unpkg.com/three@0.160.0/build/three.module.js", "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"}}}}
        </script>
        <style>
            * {{ box-sizing: border-box; letter-spacing: 0; }}
            body {{
                background: transparent;
                color: #f2fffb;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                margin: 0;
                overflow: hidden;
            }}
            .warehouse-scene {{
                display: grid;
                gap: 0.72rem;
                grid-template-columns: 170px minmax(0, 1.15fr) minmax(430px, 0.95fr);
                height: 744px;
            }}
            .panel {{
                background: rgba(6, 48, 43, 0.66);
                border: 1px solid rgba(87, 178, 165, 0.28);
                border-radius: 8px;
                min-height: 0;
                overflow: hidden;
            }}
            .building-panel,
            .model-panel,
            .detail-panel {{
                padding: 0.8rem;
            }}
            .building-panel h3,
            .model-panel h3,
            .detail-panel h3 {{
                color: #ffffff;
                font-size: 0.96rem;
                margin: 0 0 0.56rem;
            }}
            .building-name,
            .scene-head span {{
                color: #b2d5cd;
                font-size: 0.74rem;
                font-weight: 850;
                line-height: 1.45;
            }}
            .building-stack {{
                display: flex;
                flex-direction: column;
                gap: 0.28rem;
                margin-top: 0.7rem;
            }}
            .floor-chip,
            button,
            select,
            input {{
                background: #171a22;
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 7px;
                color: #ffffff;
                font-size: 0.76rem;
                font-weight: 850;
                min-height: 34px;
                outline: 0;
                padding: 0 0.55rem;
            }}
            button {{
                cursor: pointer;
            }}
            button:hover {{
                border-color: rgba(117, 236, 219, 0.58);
            }}
            .floor-chip {{
                color: #dffaf4;
                font-weight: 900;
                padding: 0.52rem 0.6rem;
                text-align: center;
            }}
            .floor-chip.active {{
                background: rgba(22, 213, 198, 0.18);
                border-color: rgba(22, 213, 198, 0.58);
                box-shadow: inset 3px 0 0 #16d5c6;
            }}
            .zone-tags {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.32rem;
                margin-top: 0.8rem;
            }}
            .zone-tags span {{
                background: rgba(75, 156, 255, 0.14);
                border: 1px solid rgba(75, 156, 255, 0.28);
                border-radius: 999px;
                color: #d8ebff;
                font-size: 0.68rem;
                font-weight: 850;
                padding: 0.24rem 0.42rem;
            }}
            .model-panel {{
                display: grid;
                grid-template-rows: auto auto minmax(0, 1fr);
            }}
            .scene-head {{
                align-items: center;
                display: flex;
                justify-content: space-between;
                margin-bottom: 0.58rem;
            }}
            .scene-tools {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.42rem;
                margin-bottom: 0.58rem;
            }}
            .scene-tools select,
            .scene-tools button {{
                min-width: 0;
            }}
            #rackTypeSelect {{
                flex: 1 1 180px;
                min-width: 170px;
            }}
            #rackLevelSelect {{
                flex: 0 0 92px;
            }}
            #rackBottomSelect {{
                flex: 0 0 118px;
            }}
            #rackStackTargetSelect {{
                flex: 1 1 160px;
                min-width: 150px;
            }}
            .scene-tools button {{
                flex: 1 1 92px;
                min-width: 86px;
                white-space: nowrap;
            }}
            .fixture-name-toggle {{
                align-items: center;
                background: rgba(6, 48, 43, 0.82);
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 6px;
                color: #e9fff9;
                display: inline-flex;
                font-size: 0.76rem;
                font-weight: 900;
                gap: 0.34rem;
                justify-content: center;
                min-height: 34px;
                padding: 0 0.62rem;
                white-space: nowrap;
            }}
            .fixture-name-toggle input {{
                accent-color: #16d5c6;
                margin: 0;
            }}
            .floor-size-tools {{
                align-items: center;
                background: rgba(3, 27, 24, 0.56);
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 7px;
                display: grid;
                gap: 0.34rem;
                grid-template-columns: auto 72px auto 72px 58px 58px;
                left: 1rem;
                padding: 0.34rem;
                position: absolute;
                top: 3.95rem;
                z-index: 3;
            }}
            .floor-size-tools span {{
                color: #b2d5cd;
                font-size: 0.68rem;
                font-weight: 900;
                white-space: nowrap;
            }}
            .floor-size-tools input {{
                min-height: 28px;
                padding: 0 0.38rem;
                width: 72px;
            }}
            .floor-size-tools button {{
                min-height: 28px;
                padding: 0 0.42rem;
            }}
            .zoom-tools {{
                align-items: center;
                background: rgba(3, 27, 24, 0.56);
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 7px;
                display: flex;
                gap: 0.36rem;
                padding: 0.34rem;
                position: absolute;
                right: 1rem;
                top: 1rem;
                z-index: 3;
            }}
            .zoom-tools span {{
                color: #b2d5cd;
                font-size: 0.68rem;
                font-weight: 900;
                margin-right: 0.1rem;
            }}
            .zoom-tools button {{
                min-height: 26px;
                padding: 0 0.42rem;
            }}
            .zoom-tools button.active {{
                background: rgba(22, 213, 198, 0.2);
                border-color: rgba(22, 213, 198, 0.62);
                color: #ffffff;
            }}
            .nav-tools {{
                background: rgba(3, 27, 24, 0.56);
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 7px;
                display: grid;
                gap: 0.28rem;
                grid-template-columns: repeat(3, 30px);
                padding: 0.34rem;
                position: absolute;
                right: 1rem;
                top: 3.9rem;
                z-index: 3;
            }}
            .nav-tools button {{
                min-height: 28px;
                padding: 0;
            }}
            .nav-tools .nav-reset {{
                font-size: 0.66rem;
                grid-column: 1 / -1;
            }}
            .model-viewport {{
                background:
                    radial-gradient(circle at 50% 18%, rgba(86, 218, 203, 0.18), transparent 28%),
                    linear-gradient(180deg, rgba(9, 38, 42, 0.96), rgba(2, 20, 19, 0.96));
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 8px;
                min-height: 0;
                overflow: hidden;
                position: relative;
            }}
            #warehouseCanvas {{
                display: block;
                height: 100%;
                width: 100%;
            }}
            .model-label {{
                background: rgba(3, 27, 24, 0.86);
                border: 1px solid rgba(126, 197, 185, 0.24);
                border-radius: 6px;
                color: #dffaf4;
                font-size: 0.68rem;
                font-weight: 900;
                left: 1rem;
                padding: 0.32rem 0.42rem;
                position: absolute;
                top: 1rem;
                z-index: 3;
            }}
            .model-help {{
                background: rgba(3, 27, 24, 0.86);
                border: 1px solid rgba(126, 197, 185, 0.24);
                border-radius: 6px;
                bottom: 1rem;
                color: #b2d5cd;
                font-size: 0.68rem;
                font-weight: 900;
                left: 1rem;
                padding: 0.32rem 0.42rem;
                position: absolute;
                z-index: 3;
            }}
            .model-error {{
                align-items: center;
                color: #ffffff;
                display: none;
                font-size: 0.86rem;
                font-weight: 900;
                inset: 0;
                justify-content: center;
                line-height: 1.55;
                padding: 1rem;
                position: absolute;
                text-align: center;
                z-index: 4;
            }}
            .detail-panel {{
                display: flex;
                flex-direction: column;
            }}
            .rack-detail {{
                background: rgba(2, 20, 19, 0.5);
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                margin-bottom: 0.7rem;
                padding: 0.68rem;
            }}
            .rack-detail strong {{
                color: #ffffff;
                display: block;
                font-size: 1.05rem;
            }}
            .rack-detail span {{
                color: #b2d5cd;
                display: block;
                font-size: 0.76rem;
                font-weight: 800;
                margin-top: 0.26rem;
            }}
            .assign-box {{
                display: grid;
                gap: 0.42rem;
                grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.85fr) 56px 74px 82px 62px 64px;
                margin-bottom: 0.68rem;
            }}
            #itemSelect {{
                display: none !important;
            }}
            .detail-tools {{
                flex: 0 0 auto;
            }}
            .stock-guide {{
                background: rgba(22, 213, 198, 0.08);
                border: 1px solid rgba(22, 213, 198, 0.22);
                border-radius: 7px;
                color: #cfe8e2;
                font-size: 0.7rem;
                font-weight: 850;
                line-height: 1.45;
                margin-bottom: 0.68rem;
                padding: 0.52rem 0.62rem;
            }}
            .nudge-grid {{
                display: grid;
                gap: 0.36rem;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                margin-bottom: 0.68rem;
            }}
            .fixture-box {{
                display: grid;
                gap: 0.42rem;
                grid-template-columns: minmax(0, 1fr) repeat(4, 76px);
                margin-bottom: 0.68rem;
            }}
            .move-to-rack-box {{
                display: grid;
                gap: 0.42rem;
                grid-template-columns: minmax(0, 1fr) 82px 116px;
                margin-bottom: 0.68rem;
            }}
            .move-floor-box {{
                display: none;
                gap: 0.42rem;
                grid-template-columns: minmax(0, 1fr) 96px;
                margin-bottom: 0.68rem;
            }}
            .row-actions {{
                display: grid;
                gap: 0.28rem;
                grid-template-columns: minmax(0, 1fr);
            }}
            .row-actions select {{
                grid-column: 1 / -1;
                min-height: 28px;
                min-width: 0;
                padding: 0 0.34rem;
                width: 100%;
            }}
            .row-actions button {{
                min-height: 28px;
                min-width: 0;
                padding: 0 0.34rem;
                white-space: nowrap;
            }}
            .tool-label {{
                color: #dffaf4;
                font-size: 0.72rem;
                font-weight: 900;
                margin: 0.1rem 0 0.36rem;
            }}
            .item-list {{
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                flex: 1 1 auto;
                min-height: 180px;
                overflow: auto;
            }}
            .item-list table {{
                min-width: 660px;
                table-layout: fixed;
            }}
            .item-list th,
            .item-list td {{
                vertical-align: top;
            }}
            .item-list th:nth-child(1),
            .item-list td:nth-child(1) {{
                width: 12%;
            }}
            .item-list th:nth-child(2),
            .item-list td:nth-child(2) {{
                width: 15%;
            }}
            .item-list th:nth-child(3),
            .item-list td:nth-child(3) {{
                line-height: 1.35;
                overflow-wrap: anywhere;
                white-space: normal;
                width: 26%;
            }}
            .item-list th:nth-child(4),
            .item-list td:nth-child(4) {{
                line-height: 1.35;
                overflow-wrap: anywhere;
                white-space: normal;
                width: 20%;
            }}
            .item-list th:nth-child(5),
            .item-list td:nth-child(5) {{
                width: 12%;
            }}
            .item-list th:nth-child(6),
            .item-list td:nth-child(6) {{
                width: 15%;
            }}
            table {{
                border-collapse: collapse;
                font-size: 0.72rem;
                width: 100%;
            }}
            th,
            td {{
                border-bottom: 1px solid rgba(126, 197, 185, 0.14);
                color: #f2fffb;
                padding: 0.42rem;
                text-align: left;
            }}
            th {{
                background: rgba(255, 255, 255, 0.07);
                color: #cfe8e2;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #b2d5cd;
                text-align: center;
            }}
            @media (max-width: 980px) {{
                .warehouse-scene {{
                    grid-template-columns: 1fr;
                    height: auto;
                }}
                .model-viewport {{
                    height: 540px;
                }}
                .detail-panel {{
                    min-height: 360px;
                }}
            }}
        </style>
    </head>
    <body>
        <main class="warehouse-scene">
            <section class="panel building-panel">
                <h3>건물/층 선택</h3>
                <div class="building-name">{escape(building)}<br>{escape(LOCATIONS[building]["description"])}</div>
                <div class="building-stack">{floor_stack}</div>
            </section>
            <section class="panel model-panel">
                <div class="scene-head">
                    <h3><span id="currentFloorLabel">{escape(floor)}</span> 3D 창고 모델</h3>
                    <span>{escape(building)} · 재고/랙 배치 관리</span>
                </div>
                <div class="scene-tools">
                    <select id="rackTypeSelect" aria-label="랙 종류">
                        <option value="light">경량랙 · 흰색 선반</option>
                        <option value="heavy">중량랙 · 파랑/주황</option>
                    </select>
                    <select id="rackLevelSelect" aria-label="랙 단수">
                        <option value="2">2단</option>
                        <option value="3">3단</option>
                    </select>
                    <select id="rackBottomSelect" aria-label="랙 하단">
                        <option value="normal">하단 사용</option>
                        <option value="open">1단 없음</option>
                        <option value="roof">지붕칸만</option>
                    </select>
                    <select id="rackStackTargetSelect" aria-label="올릴 랙"></select>
                    <button type="button" id="stackRack">위에 올리기</button>
                    <button type="button" id="unstackRack">내리기</button>
                    <button type="button" id="addRack">랙 추가</button>
                    <button type="button" id="deleteRack">선택 랙 삭제</button>
                    <button type="button" id="rotateRack">방향전환</button>
                    <button type="button" id="lockRack">랙 고정</button>
                    <button type="button" id="resetRack">배치 초기화</button>
                    <button type="button" id="fitRack">기본배치</button>
                    <button type="button" id="printScene">모델 출력</button>
                </div>
                <div class="model-viewport" id="modelViewport">
                    <canvas id="warehouseCanvas"></canvas>
                    <div class="model-label">창고 외곽 / 층 / 랙 3D 모델</div>
                    <div class="floor-size-tools">
                        <span>가로</span>
                        <input id="floorWidthInput" type="number" min="10" step="1" aria-label="층 바닥 가로">
                        <span>세로</span>
                        <input id="floorDepthInput" type="number" min="10" step="1" aria-label="층 바닥 세로">
                        <button type="button" id="applyFloorSize">적용</button>
                        <button type="button" id="resetFloorSize">기본</button>
                    </div>
                    <div class="zoom-tools" id="zoomTools">
                        <span>확대</span>
                        <button type="button" data-zoom="90">90%</button>
                        <button type="button" data-zoom="95">95%</button>
                        <button type="button" data-zoom="100">100%</button>
                        <button type="button" data-zoom="115">115%</button>
                    </div>
                    <div class="nav-tools" id="navTools">
                        <span></span>
                        <button type="button" data-pan="up">↑</button>
                        <span></span>
                        <button type="button" data-pan="left">←</button>
                        <button type="button" data-pan="down">↓</button>
                        <button type="button" data-pan="right">→</button>
                        <button class="nav-reset" type="button" data-pan="reset">중앙</button>
                    </div>
                    <div class="model-help">화면 드래그 회전 · Shift+드래그 배치 이동 · 모서리 핸들 크기 조절 · 방향키 이동</div>
                    <div class="model-error" id="modelError">3D 라이브러리를 불러오지 못했습니다.<br>인터넷 연결 또는 CDN 차단 여부를 확인해주세요.</div>
                </div>
            </section>
            <aside class="panel detail-panel">
                <h3>랙 적재 품목</h3>
                <div class="rack-detail" id="rackDetail">
                    <strong>랙을 선택하세요</strong>
                    <span>3D 모델에서 랙을 클릭하면 해당 랙의 적재 품목만 표시됩니다.</span>
                </div>
                <div class="detail-tools">
                    <div class="assign-box">
                        <select id="itemSelect"></select>
                        <input id="manualItemName" type="text" placeholder="상품명 직접 입력" aria-label="상품명 직접 입력">
                        <input id="manualItemBarcode" type="text" placeholder="바코드" aria-label="바코드">
                        <input id="itemQty" type="number" min="1" value="1" aria-label="수량">
                        <select id="partSelect" aria-label="랙 칸">
                            <option value="1단">1단</option>
                            <option value="2단">2단</option>
                            <option value="3단">3단</option>
                            <option value="4단">4단</option>
                        </select>
                        <select id="loadShapeSelect" aria-label="적재 형태">
                            <option value="box">박스</option>
                            <option value="pallet">파렛트</option>
                        </select>
                        <select id="stackSelect" aria-label="적치">
                            <option value="1">1중</option>
                            <option value="2">2중</option>
                        </select>
                        <button type="button" id="addLoad">추가</button>
                    </div>
                    <div class="stock-guide">랙을 선택하면 해당 랙/단에 적재되고, 바닥 박스/파렛트는 시설물 배치에서 추가 후 랙에 넣기로 옮길 수 있습니다.</div>
                    <div class="tool-label">시설물 배치</div>
                    <div class="fixture-box">
                        <select id="fixtureTypeSelect" aria-label="오브젝트 종류">
                            <option value="box">박스</option>
                            <option value="pallet">파렛트</option>
                            <option value="wrapped_pallet">랩핑 파렛트</option>
                            <option value="entrance">출입구</option>
                            <option value="door">문</option>
                            <option value="shutter">셔터</option>
                            <option value="dock">상차도크</option>
                            <option value="exit">비상구</option>
                            <option value="elevator">엘리베이터</option>
                            <option value="desk">책상</option>
                            <option value="wall">벽/칸막이</option>
                            <option value="aisle">통로</option>
                            <option value="zone">작업구역</option>
                        </select>
                        <button type="button" id="addFixture">추가</button>
                        <button type="button" id="rotateFixture">회전</button>
                        <button type="button" id="lockFixture">고정</button>
                        <button type="button" id="deleteFixture">삭제</button>
                        <label class="fixture-name-toggle">
                            <input id="toggleFixtureLabels" type="checkbox" checked>
                            이름 표시
                        </label>
                    </div>
                    <div class="move-to-rack-box">
                        <select id="targetRackSelect" aria-label="이동할 랙"></select>
                        <select id="targetRackPartSelect" aria-label="이동할 랙 단"></select>
                        <button type="button" id="moveFixtureToRack">랙에 넣기</button>
                    </div>
                    <div class="move-floor-box">
                        <select id="targetFloorSelect" aria-label="이동할 층"></select>
                        <button type="button" id="moveSelectionFloor">층 이동</button>
                    </div>
                    <div class="nudge-grid">
                        <button type="button" data-nudge="left">←</button>
                        <button type="button" data-nudge="up">↑</button>
                        <button type="button" data-nudge="down">↓</button>
                        <button type="button" data-nudge="right">→</button>
                    </div>
                </div>
                <div class="item-list">
                    <table>
                        <thead><tr><th>칸</th><th>형태</th><th>상품명</th><th>바코드</th><th>적재</th><th></th></tr></thead>
                        <tbody id="itemBody"><tr><td colspan="6" class="empty">선택된 랙이 없습니다.</td></tr></tbody>
                    </table>
                </div>
            </aside>
        </main>
        <script type="module">
            import * as THREE from "three";
            import {{ OrbitControls }} from "three/addons/controls/OrbitControls.js";

            const defaultRacks = {payload};
            const defaultRacksByFloor = {floor_payload};
            const zonesByFloor = {zones_payload};
            const floorModels = {floor_model_payload};
            const inventory = {inventory_payload};
            const baseStorageKey = {json.dumps(base_storage_key, ensure_ascii=False)};
            const activeBuilding = {json.dumps(building, ensure_ascii=False)};
            const locationFocus = {{
                "로긴": ["제조", "출입", "옥상"],
                "포장부서": ["포장", "작업", "부자재", "반제품"],
                "창고1": ["피킹", "완제품", "랙 배치", "장기보관"],
                "창고2": ["예비", "저회전", "임시 보관"],
                "NC층": ["설비", "코어"],
            }};
            const floors = {json.dumps(floors, ensure_ascii=False)};
            let activeFloor = {json.dumps(floor, ensure_ascii=False)};
            let racks = loadLayout(activeFloor);
            let fixtures = [];
            let selectedRackId = racks[0]?.id || "";
            let selectedFixtureId = "";
            let selectedRackItemKey = "";

            const canvas = document.getElementById("warehouseCanvas");
            const viewport = document.getElementById("modelViewport");
            const rackDetail = document.getElementById("rackDetail");
            const itemBody = document.getElementById("itemBody");
            const itemSelect = document.getElementById("itemSelect");
            const itemQty = document.getElementById("itemQty");
            const manualItemName = document.getElementById("manualItemName");
            const manualItemBarcode = document.getElementById("manualItemBarcode");
            const partSelect = document.getElementById("partSelect");
            const loadShapeSelect = document.getElementById("loadShapeSelect");
            const stackSelect = document.getElementById("stackSelect");
            const fixtureTypeSelect = document.getElementById("fixtureTypeSelect");
            const rotateFixtureButton = document.getElementById("rotateFixture");
            const lockFixtureButton = document.getElementById("lockFixture");
            const deleteFixtureButton = document.getElementById("deleteFixture");
            const targetRackSelect = document.getElementById("targetRackSelect");
            const targetRackPartSelect = document.getElementById("targetRackPartSelect");
            const moveFixtureToRackButton = document.getElementById("moveFixtureToRack");
            const targetFloorSelect = document.getElementById("targetFloorSelect");
            const moveSelectionFloorButton = document.getElementById("moveSelectionFloor");
            const rackTypeSelect = document.getElementById("rackTypeSelect");
            const rackLevelSelect = document.getElementById("rackLevelSelect");
            const rackBottomSelect = document.getElementById("rackBottomSelect");
            const rackStackTargetSelect = document.getElementById("rackStackTargetSelect");
            const stackRackButton = document.getElementById("stackRack");
            const unstackRackButton = document.getElementById("unstackRack");
            const lockButton = document.getElementById("lockRack");
            const rotateButton = document.getElementById("rotateRack");
            const deleteButton = document.getElementById("deleteRack");
            const currentFloorLabel = document.getElementById("currentFloorLabel");
            const labelToggleButton = document.getElementById("toggleFixtureLabels");
            const floorWidthInput = document.getElementById("floorWidthInput");
            const floorDepthInput = document.getElementById("floorDepthInput");
            const applyFloorSizeButton = document.getElementById("applyFloorSize");
            const resetFloorSizeButton = document.getElementById("resetFloorSize");
            const printSceneButton = document.getElementById("printScene");
            const placementScale = 1.45;

            const scene = new THREE.Scene();
            const screenSceneBackground = new THREE.Color(0x071d1b);
            const screenSceneFog = new THREE.Fog(0x071d1b, 28, 76);
            scene.background = screenSceneBackground;
            scene.fog = screenSceneFog;

            const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: true, preserveDrawingBuffer: true }});
            const screenPixelRatio = Math.min(window.devicePixelRatio || 1, 2);
            renderer.setPixelRatio(screenPixelRatio);

            const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
            camera.position.set(26, 15, 30);

            const controls = new OrbitControls(camera, renderer.domElement);
            controls.enableDamping = true;
            controls.target.set(0, 1.2, 0);
            controls.maxPolarAngle = Math.PI * 0.48;
            controls.enableZoom = false;
            controls.enablePan = true;
            controls.screenSpacePanning = false;
            controls.minDistance = 18;
            controls.maxDistance = 70;

            const zoomLevels = [90, 95, 100, 115];
            const zoomMin = 70;
            const zoomMax = 150;
            const zoomStep = 5;
            let zoomLevel = 100;

            function setZoom(level) {{
                const requestedLevel = Number(level);
                if (!Number.isFinite(requestedLevel)) return;
                zoomLevel = clamp(Math.round(requestedLevel / zoomStep) * zoomStep, zoomMin, zoomMax);
                camera.zoom = zoomLevel / 100;
                camera.updateProjectionMatrix();
                document.querySelectorAll("[data-zoom]").forEach(button => {{
                    button.classList.toggle("active", Number(button.dataset.zoom) === zoomLevel);
                }});
            }}

            function panView(direction) {{
                if (direction === "reset") {{
                    camera.position.set(26, 15, 30);
                    controls.target.set(0, 1.2, 0);
                    controls.update();
                    return;
                }}
                const step = 2.6 / Math.max(0.7, camera.zoom);
                const forward = new THREE.Vector3();
                camera.getWorldDirection(forward);
                forward.y = 0;
                if (forward.lengthSq() < 0.0001) forward.set(0, 0, -1);
                forward.normalize();
                const right = new THREE.Vector3().crossVectors(forward, camera.up).normalize();
                const move = new THREE.Vector3();
                if (direction === "left") move.addScaledVector(right, -step);
                if (direction === "right") move.addScaledVector(right, step);
                if (direction === "up") move.addScaledVector(forward, step);
                if (direction === "down") move.addScaledVector(forward, -step);
                camera.position.add(move);
                controls.target.add(move);
                controls.update();
            }}

            const ambient = new THREE.HemisphereLight(0xdffff9, 0x071d1b, 1.7);
            scene.add(ambient);
            const keyLight = new THREE.DirectionalLight(0xffffff, 2.2);
            keyLight.position.set(18, 28, 22);
            scene.add(keyLight);
            const fillLight = new THREE.DirectionalLight(0x58d9d0, 0.8);
            fillLight.position.set(-20, 12, -18);
            scene.add(fillLight);

            const buildingGroup = new THREE.Group();
            const fixtureGroup = new THREE.Group();
            const rackGroup = new THREE.Group();
            scene.add(buildingGroup, fixtureGroup, rackGroup);

            const raycaster = new THREE.Raycaster();
            const pointer = new THREE.Vector2();
            const dragPlane = new THREE.Plane();
            const dragPoint = new THREE.Vector3();
            const rackBodies = [];
            const rackItemBodies = [];
            const rackResizeHandles = [];
            const fixtureBodies = [];
            const fixtureResizeHandles = [];
            const floorResizeHandles = [];
            const rackObjectById = new Map();
            const fixtureObjectById = new Map();
            let draggingRack = null;
            let draggingFixture = null;
            let resizingRack = null;
            let resizingFixture = null;
            let resizingFloor = null;
            let resizeState = null;
            let rackDropAnimation = null;
            let lastRackAddAt = 0;
            let dragOffset = new THREE.Vector3();
            const fixtureDefaults = {{
                entrance: {{ label: "출입구", w: 4.2, d: 0.45, h: 0.34, color: 0x16d5c6 }},
                door: {{ label: "문", w: 2.2, d: 0.32, h: 1.55, color: 0x75ecdb }},
                shutter: {{ label: "셔터", w: 4.8, d: 0.38, h: 1.8, color: 0xa7c3c0 }},
                dock: {{ label: "상차도크", w: 5.4, d: 1.6, h: 0.42, color: 0x4b9cff }},
                exit: {{ label: "비상구", w: 2.8, d: 0.38, h: 1.45, color: 0x58d163 }},
                elevator: {{ label: "엘리베이터", w: 2.6, d: 2.4, h: 2.3, color: 0x7f9f9b }},
                desk: {{ label: "책상", w: 2.4, d: 1.25, h: 0.82, color: 0xb8874f }},
                wall: {{ label: "벽/칸막이", w: 6.8, d: 0.18, h: 1.35, color: 0x9fb7b2 }},
                aisle: {{ label: "통로", w: 8.0, d: 2.0, h: 0.08, color: 0x4b9cff }},
                zone: {{ label: "작업구역", w: 6.2, d: 4.0, h: 0.08, color: 0xffb22e }},
                box: {{ label: "박스", w: 1.2, d: 1.0, h: 0.72, color: 0xffb22e }},
                pallet: {{ label: "파렛트", w: 1.55, d: 1.55, h: 1.35, color: 0xd8a35d }},
                wrapped_pallet: {{ label: "랩핑 파렛트", w: 1.55, d: 1.55, h: 1.45, color: 0x8ec7ff }},
            }};
            const outsideFixtureTypes = new Set(["entrance", "door", "shutter", "dock", "exit"]);
            const fixtureLabelStorageKey = `${{baseStorageKey}}fixtureLabels`;
            let showFixtureLabels = localStorage.getItem(fixtureLabelStorageKey) !== "hidden";

            function fixtureAllowsOutside(type) {{
                return outsideFixtureTypes.has(type);
            }}

            const materials = {{
                slab: new THREE.MeshStandardMaterial({{ color: 0x194d48, roughness: 0.88, metalness: 0.05, transparent: true, opacity: 0.18 }}),
                activeSlab: new THREE.MeshStandardMaterial({{ color: 0x16d5c6, roughness: 0.72, metalness: 0.08, transparent: true, opacity: 0.34 }}),
                wall: new THREE.MeshStandardMaterial({{ color: 0x8fded3, roughness: 0.9, transparent: true, opacity: 0.16 }}),
                rack: new THREE.MeshStandardMaterial({{ color: 0x159886, roughness: 0.78, metalness: 0.08 }}),
                rackEmpty: new THREE.MeshStandardMaterial({{ color: 0x5d7774, roughness: 0.9, transparent: true, opacity: 0.55 }}),
                rackShort: new THREE.MeshStandardMaterial({{ color: 0xff4c4c, roughness: 0.75, metalness: 0.08 }}),
                rackPost: new THREE.MeshStandardMaterial({{ color: 0xf4f7f3, roughness: 0.58, metalness: 0.28 }}),
                rackShelf: new THREE.MeshStandardMaterial({{ color: 0xe7ece7, roughness: 0.62, metalness: 0.18 }}),
                rackBrace: new THREE.MeshStandardMaterial({{ color: 0xbecac5, roughness: 0.7, metalness: 0.25 }}),
                heavyPost: new THREE.MeshStandardMaterial({{ color: 0x0f78c8, roughness: 0.42, metalness: 0.32 }}),
                heavyBeam: new THREE.MeshStandardMaterial({{ color: 0xff7a1a, roughness: 0.46, metalness: 0.24 }}),
                heavyDeck: new THREE.MeshStandardMaterial({{ color: 0xf3dfc6, roughness: 0.7, metalness: 0.04 }}),
                heavyBrace: new THREE.MeshStandardMaterial({{ color: 0x0a4d86, roughness: 0.5, metalness: 0.28 }}),
                itemBox: new THREE.MeshStandardMaterial({{ color: 0x58d163, roughness: 0.72, metalness: 0.04 }}),
                itemBoxShort: new THREE.MeshStandardMaterial({{ color: 0xff4c4c, roughness: 0.72, metalness: 0.04 }}),
                itemBoxSelected: new THREE.MeshStandardMaterial({{ color: 0x16d5c6, emissive: 0x063a36, roughness: 0.68, metalness: 0.05 }}),
                hitbox: new THREE.MeshBasicMaterial({{ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false }}),
                room: new THREE.MeshStandardMaterial({{ color: 0x2b6860, roughness: 0.85, transparent: true, opacity: 0.26 }}),
                column: new THREE.MeshStandardMaterial({{ color: 0xdffaf4, roughness: 0.72, metalness: 0.18, transparent: true, opacity: 0.74 }}),
                entrance: new THREE.MeshStandardMaterial({{ color: 0x16d5c6, emissive: 0x063a36, roughness: 0.5 }}),
                locked: new THREE.MeshStandardMaterial({{ color: 0xffd12c, emissive: 0x3a2a00, roughness: 0.45 }}),
                resizeHandle: new THREE.MeshStandardMaterial({{ color: 0x16d5c6, emissive: 0x063a36, roughness: 0.36, metalness: 0.12 }}),
                roofGarden: new THREE.MeshStandardMaterial({{ color: 0x4fbf72, roughness: 0.86, metalness: 0.02, transparent: true, opacity: 0.76 }}),
                roofEquip: new THREE.MeshStandardMaterial({{ color: 0xa7c3c0, roughness: 0.68, metalness: 0.32, transparent: true, opacity: 0.88 }}),
                roofDetail: new THREE.MeshStandardMaterial({{ color: 0xd8c88c, roughness: 0.82, metalness: 0.05, transparent: true, opacity: 0.5 }}),
                selected: new THREE.LineBasicMaterial({{ color: 0x16d5c6 }}),
                edge: new THREE.LineBasicMaterial({{ color: 0x98fff4, transparent: true, opacity: 0.42 }}),
                floorEdge: new THREE.LineBasicMaterial({{ color: 0x6fd6ca, transparent: true, opacity: 0.32 }}),
            }};

            function escapeHtml(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;");
            }}

            function storageKeyFor(floorName) {{
                return `${{baseStorageKey}}${{floorName}}`;
            }}

            function fixtureStorageKeyFor(floorName) {{
                return `${{baseStorageKey}}fixtures:${{floorName}}`;
            }}

            function floorSizeStorageKeyFor(floorName) {{
                return `${{baseStorageKey}}floorSize:${{floorName}}`;
            }}

            function baseFloorSize(floorName) {{
                const model = floorModels[floorName] || floorModels["1층"] || {{}};
                return {{
                    width: Number(model.width || 44) * placementScale,
                    depth: Number(model.depth || 27) * placementScale,
                }};
            }}

            function loadFloorSize(floorName) {{
                const base = baseFloorSize(floorName);
                try {{
                    const saved = JSON.parse(localStorage.getItem(floorSizeStorageKeyFor(floorName)) || "null");
                    if (saved && Number.isFinite(Number(saved.width)) && Number.isFinite(Number(saved.depth))) {{
                        return {{
                            width: clamp(Number(saved.width), base.width * 0.45, base.width * 2.6),
                            depth: clamp(Number(saved.depth), base.depth * 0.45, base.depth * 2.6),
                            x: Number.isFinite(Number(saved.x)) ? Number(saved.x) : 0,
                            z: Number.isFinite(Number(saved.z)) ? Number(saved.z) : 0,
                        }};
                    }}
                }} catch (error) {{}}
                return {{ ...base, x: 0, z: 0 }};
            }}

            function saveFloorSize(floorName, size) {{
                localStorage.setItem(floorSizeStorageKeyFor(floorName), JSON.stringify(size));
            }}

            function currentFloorSize() {{
                return loadFloorSize(activeFloor);
            }}

            function layoutFloorSize() {{
                return baseFloorSize(activeFloor);
            }}

            function syncFixtureLabelButton() {{
                if (labelToggleButton.type === "checkbox") {{
                    labelToggleButton.checked = showFixtureLabels;
                }} else {{
                    labelToggleButton.textContent = showFixtureLabels ? "이름표 숨김" : "이름표 표시";
                }}
            }}

            function syncFloorSizeInputs() {{
                const size = currentFloorSize();
                floorWidthInput.value = Number(size.width).toFixed(0);
                floorDepthInput.value = Number(size.depth).toFixed(0);
            }}

            function keepViewFixed(callback) {{
                const cameraPosition = camera.position.clone();
                const targetPosition = controls.target.clone();
                const fixedZoom = camera.zoom;
                callback();
                camera.position.copy(cameraPosition);
                controls.target.copy(targetPosition);
                camera.zoom = fixedZoom;
                camera.updateProjectionMatrix();
                controls.update();
            }}

            function refreshFloorOnly() {{
                keepViewFixed(() => {{
                    buildWarehouseModel();
                    syncFloorSizeInputs();
                }});
            }}

            function applyFloorSizeFromInputs() {{
                const base = baseFloorSize(activeFloor);
                const current = currentFloorSize();
                const width = clamp(Number(floorWidthInput.value || base.width), base.width * 0.45, base.width * 2.6);
                const depth = clamp(Number(floorDepthInput.value || base.depth), base.depth * 0.45, base.depth * 2.6);
                saveFloorSize(activeFloor, {{ width, depth, x: Number(current.x || 0), z: Number(current.z || 0) }});
                refreshFloorOnly();
            }}

            function resetFloorSizeToBase() {{
                localStorage.removeItem(floorSizeStorageKeyFor(activeFloor));
                refreshFloorOnly();
            }}

            function normalizeFixture(fixture, index = 0) {{
                const type = fixture?.type || "entrance";
                const template = fixtureDefaults[type] || fixtureDefaults.entrance;
                const min = fixtureAllowsOutside(type) ? -24 : 1;
                const max = fixtureAllowsOutside(type) ? 124 : 99;
                const rawX = Number.isFinite(Number(fixture?.x)) ? Number(fixture.x) : 50;
                const rawY = Number.isFinite(Number(fixture?.y)) ? Number(fixture.y) : 50;
                return {{
                    ...template,
                    ...fixture,
                    type,
                    id: fixture?.id || `F-${{String(index + 1).padStart(2, "0")}}`,
                    label: fixture?.label || template.label,
                    x: clamp(rawX, min, max),
                    y: clamp(rawY, min, max),
                    qty: Math.max(1, Number(fixture?.qty || 1)),
                    stack: clamp(Number(fixture?.stack || 1), 1, 2),
                    items: Array.isArray(fixture?.items) ? fixture.items : [],
                    rotation: Number.isFinite(Number(fixture?.rotation)) ? Number(fixture.rotation) : 0,
                }};
            }}

            function defaultLayout(floorName) {{
                const source = defaultRacksByFloor[floorName] || defaultRacks;
                return source.map((rack, index) => ({{
                    ...rack,
                    x: Number.isFinite(Number(rack.x)) ? Number(rack.x) : 8 + (index % 6) * 13.2,
                    y: Number.isFinite(Number(rack.y)) ? Number(rack.y) : 16 + Math.floor(index / 6) * 15.2,
                    w: Number.isFinite(Number(rack.w)) ? Number(rack.w) : 10.8,
                    h: Number.isFinite(Number(rack.h)) ? Number(rack.h) : 8.4,
                    rotation: Number.isFinite(Number(rack.rotation)) ? Number(rack.rotation) : 0,
                    type: rack.type || "light",
                    levels: [2, 3].includes(Number(rack.levels)) ? Number(rack.levels) : 2,
                    bottomOpen: Boolean(rack.bottomOpen),
                    roofOnly: Boolean(rack.roofOnly),
                    parentRackId: String(rack.parentRackId || ""),
                    locked: Boolean(rack.locked),
                    items: rack.items || [],
                }}));
            }}

            function nextRackIdFromSet(existingIds, start = 1) {{
                let number = Math.max(1, Number(start) || 1);
                let id = "";
                do {{
                    id = `R-${{String(number).padStart(2, "0")}}`;
                    number += 1;
                }} while (existingIds.has(id));
                return id;
            }}

            function nextRackId() {{
                const existingIds = new Set(racks.map(rack => String(rack.id || "").trim()).filter(Boolean));
                const maxNumber = racks.reduce((max, rack) => {{
                    const match = String(rack.id || "").match(/^R-(\\d+)$/);
                    return match ? Math.max(max, Number(match[1]) || 0) : max;
                }}, 0);
                return nextRackIdFromSet(existingIds, Math.max(racks.length + 1, maxNumber + 1));
            }}

            function normalizeRackIds(layout) {{
                const existingIds = new Set();
                return (Array.isArray(layout) ? layout : []).map((rack, index) => {{
                    const currentId = String(rack?.id || "").trim();
                    const id = currentId && !existingIds.has(currentId)
                        ? currentId
                        : nextRackIdFromSet(existingIds, index + 1);
                    rack.id = id;
                    existingIds.add(id);
                    return rack;
                }});
            }}

            function rackBounds(rack) {{
                const w = Math.max(1, Number(rack.w || 10.8));
                const h = Math.max(1, Number(rack.h || 8.4));
                const x = Number(rack.x || 50);
                const y = Number(rack.y || 50);
                return {{
                    left: x - w / 2,
                    right: x + w / 2,
                    top: y - h / 2,
                    bottom: y + h / 2,
                }};
            }}

            function racksOverlap(a, b, gap = 1.4) {{
                const first = rackBounds(a);
                const second = rackBounds(b);
                return !(
                    first.right + gap < second.left ||
                    first.left - gap > second.right ||
                    first.bottom + gap < second.top ||
                    first.top - gap > second.bottom
                );
            }}

            function findOpenRackPosition(width, height) {{
                const candidates = [];
                for (let y = 14; y <= 86; y += 12) {{
                    for (let x = 12; x <= 88; x += 13) {{
                        candidates.push({{ x, y }});
                    }}
                }}
                candidates.push({{ x: 50, y: 50 }});
                const size = {{ w: width, h: height }};
                const found = candidates.find(point => {{
                    const candidate = {{ ...size, x: point.x, y: point.y }};
                    return !racks.some(rack => racksOverlap(candidate, rack));
                }});
                return found || {{
                    x: clamp(12 + (racks.length * 11) % 76, 6, 94),
                    y: clamp(14 + (Math.floor(racks.length / 7) * 12) % 72, 8, 92),
                }};
            }}

            function loadLayout(floorName) {{
                try {{
                    const saved = JSON.parse(localStorage.getItem(storageKeyFor(floorName)) || "null");
                    if (Array.isArray(saved)) return normalizeRackIds(saved);
                }} catch (error) {{}}
                return normalizeRackIds(defaultLayout(floorName));
            }}

            function saveLayout() {{
                racks = normalizeRackIds(racks);
                localStorage.setItem(storageKeyFor(activeFloor), JSON.stringify(racks));
            }}

            function saveLayoutFor(floorName, floorRacks) {{
                localStorage.setItem(storageKeyFor(floorName), JSON.stringify(normalizeRackIds(floorRacks)));
            }}

            function loadFixtures(floorName) {{
                try {{
                    const saved = JSON.parse(localStorage.getItem(fixtureStorageKeyFor(floorName)) || "[]");
                    if (Array.isArray(saved)) return saved.map(normalizeFixture);
                }} catch (error) {{}}
                return [];
            }}

            function saveFixtures() {{
                localStorage.setItem(fixtureStorageKeyFor(activeFloor), JSON.stringify(fixtures));
            }}

            function saveFixturesFor(floorName, floorFixtures) {{
                localStorage.setItem(fixtureStorageKeyFor(floorName), JSON.stringify(floorFixtures));
            }}

            function clamp(value, min, max) {{
                return Math.max(min, Math.min(max, value));
            }}

            function rackLoadedQty(rack) {{
                return (rack.items || []).reduce((sum, item) => sum + Number(item.qty || item.stock || 0), 0);
            }}

            function rackStatus(rack) {{
                if (!rackLoadedQty(rack)) return "empty";
                return rack.status === "short" ? "short" : "normal";
            }}

            function rackIsRoofOnly(rack) {{
                return Boolean(rack?.roofOnly);
            }}

            function rackLevelCount(rack) {{
                return [2, 3].includes(Number(rack?.levels)) ? Number(rack.levels) : 2;
            }}

            function rackVisualHeight(rack) {{
                return rackLevelCount(rack) === 2 ? 3.8 : 4.25;
            }}

            function rackRenderPosition(rack) {{
                const parent = racks.find(row => row.id === rack?.parentRackId);
                if (!parent) return {{ x: Number(rack?.x || 50), y: Number(rack?.y || 50) }};
                return rackRenderPosition(parent);
            }}

            function rackStackBaseY(rack, visited = new Set()) {{
                if (!rack?.parentRackId || visited.has(rack.id)) return 0;
                visited.add(rack.id);
                const parent = racks.find(row => row.id === rack.parentRackId);
                if (!parent) return 0;
                return rackStackBaseY(parent, visited) + rackVisualHeight(parent) + 0.22;
            }}

            function rackDisplayType(rack) {{
                const typeText = (rack?.type || "light") === "heavy" ? "중량랙" : "경량랙";
                if (rackIsRoofOnly(rack)) return `${{typeText}} · ${{rackLevelCount(rack)}}단 지붕만`;
                return `${{typeText}} · ${{rack?.bottomOpen ? "1단 없음 " : "하단 사용 "}}${{rackLevelCount(rack)}}단`;
            }}

            function rackDisplayName(rack) {{
                const id = String(rack?.id || "랙").trim();
                const typeText = (rack?.type || "light") === "heavy" ? "중량랙" : "경량랙";
                if (rackIsRoofOnly(rack)) return `${{id}} ${{typeText}} ${{rackLevelCount(rack)}}단 지붕`;
                const bottomText = rack?.bottomOpen ? " 1단없음" : "";
                return `${{id}} ${{typeText}} ${{rackLevelCount(rack)}}단${{bottomText}}`;
            }}

            function rackLabelText(rack) {{
                return `${{rackDisplayName(rack)}}${{rack?.locked ? " / 고정" : ""}}`;
            }}

            function rackToWorld(rack) {{
                const size = layoutFloorSize();
                const position = rackRenderPosition(rack);
                return {{
                    x: (Number(position.x || 0) - 50) * size.width / 100,
                    z: (Number(position.y || 0) - 50) * size.depth / 100,
                    w: Math.max(1.8, Number(rack.w || 10.8) * size.width / 100),
                    d: Math.max(1.4, Number(rack.h || 8.4) * size.depth / 100),
                }};
            }}

            function worldToRack(x, z) {{
                return worldToPercent(x, z, false);
            }}

            function worldToPercent(x, z, allowOutside = false) {{
                const size = layoutFloorSize();
                const min = allowOutside ? -24 : 1;
                const max = allowOutside ? 124 : 99;
                return {{
                    x: clamp(x / Math.max(1, size.width) * 100 + 50, min, max),
                    y: clamp(z / Math.max(1, size.depth) * 100 + 50, min, max),
                }};
            }}

            function snapValue(value, anchors, threshold = 2.4) {{
                const nearest = anchors.reduce((best, anchor) =>
                    Math.abs(value - anchor) < Math.abs(value - best) ? anchor : best
                , anchors[0]);
                return Math.abs(value - nearest) <= threshold ? nearest : value;
            }}

            function snapPercentPosition(position, allowOutside = false) {{
                const anchors = allowOutside ? [-6, 1, 50, 99, 106] : [1, 50, 99];
                return {{
                    x: snapValue(position.x, anchors),
                    y: snapValue(position.y, anchors),
                }};
            }}

            function fixtureToWorld(fixture) {{
                const size = layoutFloorSize();
                return {{
                    x: (Number(fixture.x || 0) - 50) * size.width / 100,
                    z: (Number(fixture.y || 0) - 50) * size.depth / 100,
                    w: Number(fixture.w || fixtureDefaults.entrance.w),
                    d: Number(fixture.d || fixtureDefaults.entrance.d),
                    h: Number(fixture.h || fixtureDefaults.entrance.h),
                }};
            }}

            function clearGroup(group) {{
                while (group.children.length) {{
                    const child = group.children.pop();
                    child.traverse?.(node => {{
                        node.geometry?.dispose?.();
                    }});
                }}
            }}

            function makeBox(width, height, depth, material, position) {{
                const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
                mesh.position.copy(position);
                const edges = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry), materials.edge);
                mesh.add(edges);
                return mesh;
            }}

            function makeLabel(text, position, scale = 1) {{
                const rawText = String(text ?? "").trim();
                if (!rawText) return new THREE.Group();
                const wrapLabel = (value, maxChars = 18) => {{
                    if (value.length <= maxChars) return [value];
                    const words = value.split(/\\s+/).filter(Boolean);
                    const lines = [];
                    let current = "";
                    words.forEach(word => {{
                        if (!current) {{
                            current = word;
                        }} else if (`${{current}} ${{word}}`.length <= maxChars) {{
                            current = `${{current}} ${{word}}`;
                        }} else {{
                            lines.push(current);
                            current = word;
                        }}
                    }});
                    if (current) lines.push(current);
                    if (lines.length <= 2) return lines;
                    return [lines[0], `${{lines.slice(1).join(" ").slice(0, maxChars - 1)}}…`];
                }};
                const lines = wrapLabel(rawText);
                const longestLine = lines.reduce((longest, line) => line.length > longest.length ? line : longest, "");
                const labelCanvas = document.createElement("canvas");
                labelCanvas.width = Math.min(920, Math.max(440, longestLine.length * 34 + 112));
                labelCanvas.height = lines.length > 1 ? 176 : 132;
                const ctx = labelCanvas.getContext("2d");
                ctx.clearRect(0, 0, labelCanvas.width, labelCanvas.height);
                ctx.fillStyle = "rgba(3, 27, 24, 0.9)";
                ctx.strokeStyle = "rgba(126, 236, 219, 0.72)";
                ctx.lineWidth = 5;
                if (ctx.roundRect) {{
                    ctx.roundRect(14, 18, labelCanvas.width - 28, labelCanvas.height - 36, 16);
                }} else {{
                    ctx.rect(14, 18, labelCanvas.width - 28, labelCanvas.height - 36);
                }}
                ctx.fill();
                ctx.stroke();
                ctx.fillStyle = "#f2fffb";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                let fontSize = lines.length > 1 ? 30 : 36;
                ctx.font = `900 ${{fontSize}}px Pretendard, Arial, sans-serif`;
                while (fontSize > 20 && lines.some(line => ctx.measureText(line).width > labelCanvas.width - 74)) {{
                    fontSize -= 2;
                    ctx.font = `900 ${{fontSize}}px Pretendard, Arial, sans-serif`;
                }}
                const lineHeight = fontSize * 1.22;
                const startY = labelCanvas.height / 2 - ((lines.length - 1) * lineHeight) / 2;
                lines.forEach((line, index) => {{
                    ctx.fillText(line, labelCanvas.width / 2, startY + index * lineHeight);
                }});
                const texture = new THREE.CanvasTexture(labelCanvas);
                const sprite = new THREE.Sprite(new THREE.SpriteMaterial({{ map: texture, transparent: true }}));
                sprite.position.copy(position);
                sprite.scale.set(Math.max(5.4, labelCanvas.width / 72) * scale, Math.max(1.8, labelCanvas.height / 72) * scale, 1);
                return sprite;
            }}

            const shelfParts = ["1단", "2단", "3단", "4단"];

            function rackVisualItemKey(rack, shelfIndex, itemIndex, item) {{
                return [
                    rack?.id || "",
                    shelfIndex,
                    itemIndex,
                    item?.barcode || "",
                    item?.name || "",
                ].join(":");
            }}

            function shelfPartIndex(part, fallback = 0) {{
                const index = shelfParts.indexOf(part);
                return index >= 0 ? index : fallback % shelfParts.length;
            }}

            function rackPart(width, height, depth, material, x, y, z) {{
                const mesh = new THREE.Mesh(new THREE.BoxGeometry(width, height, depth), material);
                mesh.position.set(x, y, z);
                return mesh;
            }}

            function sideDiagonal(depth, height, material, x, sign = 1) {{
                const length = Math.sqrt(depth * depth + height * height);
                const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.08, length), material);
                mesh.position.set(x, height / 2, 0);
                mesh.rotation.x = sign * Math.atan2(height, depth);
                return mesh;
            }}

            function itemMaterialFor(index, status) {{
                if (status === "short") return materials.itemBoxShort;
                const palette = [0x58d163, 0x4b9cff, 0xff941f, 0xc77dff, 0x16d5c6, 0xffd12c];
                return new THREE.MeshStandardMaterial({{ color: palette[index % palette.length], roughness: 0.72, metalness: 0.04 }});
            }}

            function shortLabel(value, max = 12) {{
                const text = String(value || "");
                return text.length > max ? `${{text.slice(0, max - 1)}}…` : text;
            }}

            function shapeLabel(shape) {{
                return shape === "pallet" || shape === "wrapped_pallet" ? "파렛트" : "박스";
            }}

            function stackLabel(stack) {{
                const count = clamp(Number(stack || 1), 1, 2);
                return count > 1 ? `${{count}}중` : "1중";
            }}

            function loadQtyText(item) {{
                const qty = Number(item?.qty || item?.stock || 0).toLocaleString("ko-KR");
                const isPallet = item?.shape === "pallet" || item?.shape === "wrapped_pallet" || item?.type === "pallet" || item?.type === "wrapped_pallet";
                const innerQty = Array.isArray(item?.items)
                    ? item.items.reduce((sum, innerItem) => sum + Number(innerItem.qty || innerItem.stock || 0), 0)
                    : 0;
                const innerText = innerQty ? ` · 내부 ${{innerQty.toLocaleString("ko-KR")}}개` : "";
                return isPallet ? `${{qty}}개 · ${{stackLabel(item?.stack)}}${{innerText}}` : `${{qty}}개${{innerText}}`;
            }}

            function palletContentQty(fixture) {{
                return (fixture?.items || []).reduce((sum, item) => sum + Number(item.qty || item.stock || 0), 0);
            }}

            function makeFixture(fixture) {{
                const world = fixtureToWorld(fixture);
                const height = Math.max(0.06, Number(world.h || 0.1));
                let visualHeight = height;
                const group = new THREE.Group();
                const color = Number(fixture.color || fixtureDefaults[fixture.type]?.color || 0x16d5c6);
                group.position.set(world.x, 0.2, world.z);
                group.rotation.y = THREE.MathUtils.degToRad(Number(fixture.rotation || 0));
                group.userData.fixtureId = fixture.id;

                if (fixture.type === "pallet" || fixture.type === "wrapped_pallet") {{
                    const stackCount = clamp(Number(fixture.stack || 1), 1, 2);
                    const boxMaterial = new THREE.MeshStandardMaterial({{ color: 0xd99a42, roughness: 0.7, metalness: 0.02 }});
                    const boxW = world.w * 0.42;
                    const boxD = world.d * 0.42;
                    const boxH = 0.28;
                    const palletBoxLevels = 3;
                    const layerStep = 0.1 + boxH * palletBoxLevels + 0.16;
                    Array.from({{ length: stackCount }}).forEach((_, stackIndex) => {{
                        const baseY = stackIndex * layerStep;
                        group.add(rackPart(world.w, 0.1, world.d, materials.heavyDeck, 0, baseY + 0.05, 0));
                        [[-0.24, -0.24], [0.24, -0.24], [-0.24, 0.24], [0.24, 0.24]].forEach(([xRatio, zRatio]) => {{
                            Array.from({{ length: palletBoxLevels }}).forEach((_, levelIndex) => {{
                                group.add(rackPart(boxW, boxH, boxD, boxMaterial, xRatio * world.w, baseY + 0.1 + boxH * (levelIndex + 0.5), zRatio * world.d));
                            }});
                        }});
                    }});
                    visualHeight = (stackCount - 1) * layerStep + 0.1 + boxH * palletBoxLevels;
                }} else if (fixture.type === "box") {{
                    const material = new THREE.MeshStandardMaterial({{ color, roughness: 0.72, metalness: 0.04 }});
                    group.add(makeBox(world.w, height, world.d, material, new THREE.Vector3(0, height / 2, 0)));
                }} else if (fixture.type === "elevator") {{
                    const shellMaterial = new THREE.MeshStandardMaterial({{ color, roughness: 0.58, metalness: 0.28, transparent: true, opacity: 0.92 }});
                    const doorMaterial = new THREE.MeshStandardMaterial({{ color: 0x263d3a, roughness: 0.48, metalness: 0.36 }});
                    const lineMaterial = new THREE.MeshStandardMaterial({{ color: 0x16d5c6, emissive: 0x063a36, roughness: 0.38, metalness: 0.12 }});
                    group.add(makeBox(world.w, height, world.d, shellMaterial, new THREE.Vector3(0, height / 2, 0)));
                    group.add(rackPart(world.w * 0.42, height * 0.68, 0.055, doorMaterial, -world.w * 0.22, height * 0.42, -world.d / 2 - 0.035));
                    group.add(rackPart(world.w * 0.42, height * 0.68, 0.055, doorMaterial, world.w * 0.22, height * 0.42, -world.d / 2 - 0.035));
                    group.add(rackPart(0.055, height * 0.68, 0.07, lineMaterial, 0, height * 0.42, -world.d / 2 - 0.07));
                    group.add(rackPart(world.w * 0.58, 0.12, 0.08, lineMaterial, 0, height + 0.08, -world.d / 2 - 0.08));
                }} else if (fixture.type === "desk") {{
                    const topMaterial = new THREE.MeshStandardMaterial({{ color, roughness: 0.62, metalness: 0.04 }});
                    const legMaterial = new THREE.MeshStandardMaterial({{ color: 0x263d3a, roughness: 0.48, metalness: 0.18 }});
                    const panelMaterial = new THREE.MeshStandardMaterial({{ color: 0x8f6239, roughness: 0.68, metalness: 0.02 }});
                    const topThickness = Math.min(0.16, Math.max(0.1, height * 0.18));
                    const topY = Math.max(0.42, height);
                    const legH = Math.max(0.32, topY - topThickness);
                    const legW = Math.min(0.12, Math.max(0.07, Math.min(world.w, world.d) * 0.08));
                    group.add(rackPart(world.w, topThickness, world.d, topMaterial, 0, topY, 0));
                    [[-1, -1], [-1, 1], [1, -1], [1, 1]].forEach(([xSign, zSign]) => {{
                        group.add(rackPart(
                            legW,
                            legH,
                            legW,
                            legMaterial,
                            xSign * (world.w / 2 - legW * 1.4),
                            legH / 2,
                            zSign * (world.d / 2 - legW * 1.4)
                        ));
                    }});
                    group.add(rackPart(world.w * 0.72, Math.min(0.32, legH * 0.48), 0.06, panelMaterial, 0, legH * 0.52, -world.d / 2 + 0.09));
                    visualHeight = topY + topThickness / 2;
                }} else {{
                    const material = new THREE.MeshStandardMaterial({{
                        color,
                        roughness: 0.72,
                        metalness: fixture.type === "wall" ? 0.12 : 0.04,
                        transparent: true,
                        opacity: fixture.type === "zone" || fixture.type === "aisle" ? 0.62 : 0.9,
                    }});
                    group.add(makeBox(world.w, height, world.d, material, new THREE.Vector3(0, height / 2, 0)));
                }}

                const innerQty = (fixture.type === "pallet" || fixture.type === "wrapped_pallet") ? palletContentQty(fixture) : 0;
                const labelText = (fixture.type === "pallet" || fixture.type === "wrapped_pallet")
                    ? `${{fixture.label || "파렛트"}}${{Number(fixture.stack || 1) > 1 ? ` · ${{stackLabel(fixture.stack)}}` : ""}}${{innerQty ? ` · 내부 ${{innerQty}}개` : ""}}`
                    : (fixture.label || "시설물");
                const shouldShowFixtureLabel = showFixtureLabels || fixture.id === selectedFixtureId;
                if (shouldShowFixtureLabel) {{
                    group.add(makeLabel(labelText, new THREE.Vector3(0, visualHeight + 0.62, 0), 0.9));
                }}

                const hitHeight = Math.max(0.5, visualHeight);
                const hitbox = rackPart(world.w, hitHeight, world.d, materials.hitbox, 0, hitHeight / 2, 0);
                hitbox.userData.fixtureId = fixture.id;
                group.add(hitbox);
                group.userData.hitbox = hitbox;

                if (fixture.locked) {{
                    group.add(rackPart(Math.min(0.82, world.w * 0.42), 0.1, 0.22, materials.locked, 0, visualHeight + 0.18, 0));
                    if (shouldShowFixtureLabel) {{
                        group.add(makeLabel("고정", new THREE.Vector3(0, visualHeight + 0.58, 0), 0.46));
                    }}
                }}

                if (fixture.id === selectedFixtureId) {{
                    const selection = new THREE.LineSegments(
                        new THREE.EdgesGeometry(new THREE.BoxGeometry(world.w + 0.24, visualHeight + 0.16, world.d + 0.24)),
                        materials.selected
                    );
                    selection.position.set(0, visualHeight / 2, 0);
                    group.add(selection);

                    if (!fixture.locked) {{
                        group.userData.resizeHandles = [];
                        const handleSize = Math.min(0.32, Math.max(0.18, Math.min(world.w, world.d) * 0.16));
                        const handleY = Math.max(0.32, visualHeight + 0.18);
                        [
                            {{ key: "w", x: -1, z: 0, cursor: "ew-resize" }},
                            {{ key: "e", x: 1, z: 0, cursor: "ew-resize" }},
                            {{ key: "n", x: 0, z: -1, cursor: "ns-resize" }},
                            {{ key: "s", x: 0, z: 1, cursor: "ns-resize" }},
                            {{ key: "nw", x: -1, z: -1, cursor: "nwse-resize" }},
                            {{ key: "ne", x: 1, z: -1, cursor: "nesw-resize" }},
                            {{ key: "sw", x: -1, z: 1, cursor: "nesw-resize" }},
                            {{ key: "se", x: 1, z: 1, cursor: "nwse-resize" }},
                        ].forEach(handle => {{
                            const handleWidth = handle.z === 0 ? handleSize * 1.45 : handleSize;
                            const handleDepth = handle.x === 0 ? handleSize * 1.45 : handleSize;
                            const mesh = rackPart(handleWidth, handleSize, handleDepth, materials.resizeHandle, handle.x * world.w / 2, handleY, handle.z * world.d / 2);
                            mesh.userData.fixtureId = fixture.id;
                            mesh.userData.resizeHandle = handle;
                            group.add(mesh);
                            group.userData.resizeHandles.push(mesh);
                        }});
                    }}
                }}

                return group;
            }}

            function makeShelfRack(rack, world, floorY) {{
                const group = new THREE.Group();
                const itemHitboxes = [];
                const rackType = rack.type || "light";
                const isHeavy = rackType === "heavy";
                const roofOnly = rackIsRoofOnly(rack);
                const rackLevels = rackLevelCount(rack);
                const bottomOpen = Boolean(rack.bottomOpen);
                const rackHeight = rackVisualHeight(rack);
                const post = isHeavy ? 0.16 : 0.12;
                const shelfThickness = isHeavy ? 0.12 : 0.08;
                const bottomShelfY = 0.62;
                const midShelfY = rackLevels === 2 ? rackHeight * 0.52 : rackHeight * 0.38;
                const upperShelfY = rackHeight * 0.65;
                const capShelfY = rackHeight - shelfThickness / 2;
                const shelfYs = roofOnly
                    ? [capShelfY]
                    : rackLevels === 2
                    ? (bottomOpen ? [midShelfY, capShelfY] : [bottomShelfY, midShelfY, capShelfY])
                    : (bottomOpen ? [midShelfY, upperShelfY, capShelfY] : [bottomShelfY, midShelfY, upperShelfY, capShelfY]);
                const roofPart = `${{rackLevels}}단 지붕칸`;
                const shelfLabels = roofOnly
                    ? [roofPart]
                    : rackLevels === 2
                    ? (bottomOpen ? ["2단", roofPart] : ["1단", "2단", roofPart])
                    : (bottomOpen ? ["2단", "3단", roofPart] : ["1단", "2단", "3단", roofPart]);
                const postMaterial = isHeavy ? materials.heavyPost : materials.rackPost;
                const shelfMaterial = isHeavy ? materials.heavyDeck : materials.rackShelf;
                const braceMaterial = isHeavy ? materials.heavyBrace : materials.rackBrace;
                const beamMaterial = isHeavy ? materials.heavyBeam : materials.rackBrace;
                const halfW = world.w / 2;
                const halfD = world.d / 2;
                const status = rackStatus(rack);

                group.position.set(world.x, floorY, world.z);
                group.rotation.y = THREE.MathUtils.degToRad(Number(rack.rotation || 0));
                group.userData.rackId = rack.id;

                [[-1, -1], [-1, 1], [1, -1], [1, 1]].forEach(([xSign, zSign]) => {{
                    group.add(rackPart(post, rackHeight, post, postMaterial, xSign * (halfW - post / 2), rackHeight / 2, zSign * (halfD - post / 2)));
                }});

                shelfYs.forEach((y, index) => {{
                    const shelf = rackPart(world.w, shelfThickness, world.d, shelfMaterial, 0, y, 0);
                    group.add(shelf);
                    group.add(rackPart(world.w, isHeavy ? 0.12 : 0.07, isHeavy ? 0.16 : 0.08, beamMaterial, 0, y + 0.16, -halfD + 0.08));
                    group.add(rackPart(world.w, isHeavy ? 0.12 : 0.07, isHeavy ? 0.16 : 0.08, beamMaterial, 0, y + 0.16, halfD - 0.08));
                    if (index > 0) {{
                        group.add(rackPart(isHeavy ? 0.12 : 0.08, 0.07, world.d, braceMaterial, -halfW + 0.08, y + 0.16, 0));
                        group.add(rackPart(isHeavy ? 0.12 : 0.08, 0.07, world.d, braceMaterial, halfW - 0.08, y + 0.16, 0));
                    }}
                }});

                if (rackLevels === 2 || rackLevels === 3) {{
                    const beamH = isHeavy ? 0.12 : 0.08;
                    const beamD = isHeavy ? 0.18 : 0.1;
                    shelfYs.forEach(y => {{
                        group.add(rackPart(beamD, beamH, world.d, beamMaterial, -halfW + beamD / 2, y, 0));
                        group.add(rackPart(beamD, beamH, world.d, beamMaterial, halfW - beamD / 2, y, 0));
                    }});
                }}

                if (isHeavy) {{
                    group.add(sideDiagonal(world.d, rackHeight * 0.9, braceMaterial, -halfW + 0.08, 1));
                    group.add(sideDiagonal(world.d, rackHeight * 0.9, braceMaterial, -halfW + 0.08, -1));
                    group.add(sideDiagonal(world.d, rackHeight * 0.9, braceMaterial, halfW - 0.08, 1));
                    group.add(sideDiagonal(world.d, rackHeight * 0.9, braceMaterial, halfW - 0.08, -1));
                }}
                const shouldShowRackLabel = showFixtureLabels || (rack.id === selectedRackId && !selectedRackItemKey);
                if (shouldShowRackLabel) {{
                    group.add(makeLabel(rackLabelText(rack), new THREE.Vector3(0, rackHeight + 0.48, halfD - 0.18), 0.62));
                }}

                const itemsByPart = new Map(shelfLabels.map(part => [part, []]));
                (rack.items || []).forEach((item, index) => {{
                    const part = shelfLabels.includes(item.part) ? item.part : shelfLabels[index % shelfLabels.length];
                    itemsByPart.get(part).push(item);
                }});

                shelfLabels.forEach((part, shelfIndex) => {{
                    const items = itemsByPart.get(part) || [];
                    const y = shelfYs[shelfIndex] + 0.22;
                    const maxBoxes = Math.min(5, Math.max(1, items.length));
                    items.slice(0, maxBoxes).forEach((item, itemIndex) => {{
                        const isPallet = item.shape === "pallet" || item.shape === "wrapped_pallet";
                        const boxW = isPallet
                            ? Math.min(1.18, Math.max(0.82, world.w / Math.max(1.8, maxBoxes + 0.35)))
                            : Math.min(0.62, Math.max(0.28, world.w / (maxBoxes + 1.8)));
                        const boxD = isPallet
                            ? Math.min(1.18, Math.max(0.82, world.d * 0.72))
                            : Math.min(0.62, Math.max(0.26, world.d * 0.34));
                        const boxH = isPallet
                            ? Math.min(1.45, isHeavy ? 1.22 : 1.05)
                            : 0.28 + Math.min(0.34, Math.log10(Number(item.qty || 1) + 1) * 0.16);
                        const x = -world.w / 2 + boxW * 0.9 + itemIndex * (world.w - boxW * 1.8) / Math.max(1, maxBoxes - 1);
                        const z = itemIndex % 2 === 0 ? -world.d * 0.16 : world.d * 0.16;
                        const itemKey = rackVisualItemKey(rack, shelfIndex, itemIndex, item);
                        const shouldShowItemLabel = showFixtureLabels || itemKey === selectedRackItemKey;
                        if (isPallet) {{
                            const stackCount = clamp(Number(item.stack || 1), 1, 2);
                            const palletBoxMaterial = new THREE.MeshStandardMaterial({{ color: 0xd99a42, roughness: 0.7, metalness: 0.02 }});
                            const layerBoxW = boxW * 0.42;
                            const layerBoxD = boxD * 0.42;
                            const layerBoxH = Math.min(0.26, boxH * 0.26);
                            const palletBoxLevels = 3;
                            const layerStep = Math.max(0.88, 0.08 + layerBoxH * palletBoxLevels + 0.14);
                            Array.from({{ length: stackCount }}).forEach((_, stackIndex) => {{
                                const baseY = y + stackIndex * layerStep;
                                group.add(rackPart(boxW, 0.08, boxD, materials.heavyDeck, x, baseY + 0.04, z));
                                [[-0.24, -0.24], [0.24, -0.24], [-0.24, 0.24], [0.24, 0.24]].forEach(([xRatio, zRatio]) => {{
                                    Array.from({{ length: palletBoxLevels }}).forEach((_, levelIndex) => {{
                                        group.add(rackPart(layerBoxW, layerBoxH, layerBoxD, palletBoxMaterial, x + xRatio * boxW, baseY + 0.08 + layerBoxH * (levelIndex + 0.5), z + zRatio * boxD));
                                    }});
                                }});
                            }});
                            const palletHitHeight = (stackCount - 1) * layerStep + 0.08 + layerBoxH * palletBoxLevels;
                            const itemHitbox = rackPart(boxW, Math.max(0.36, palletHitHeight), boxD, materials.hitbox, x, y + Math.max(0.36, palletHitHeight) / 2, z);
                            itemHitbox.userData.rackId = rack.id;
                            itemHitbox.userData.rackItemKey = itemKey;
                            itemHitbox.userData.itemName = item.name || "";
                            itemHitbox.userData.root = group;
                            group.add(itemHitbox);
                            itemHitboxes.push(itemHitbox);
                            if (shouldShowItemLabel) {{
                                group.add(makeLabel(shortLabel(item.name, 10), new THREE.Vector3(x, y + (stackCount - 1) * layerStep + 0.08 + layerBoxH * palletBoxLevels + 0.44, z), 0.42));
                            }}
                        }} else {{
                            const boxMaterial = itemMaterialFor(itemIndex + shelfIndex, status);
                            const boxMesh = rackPart(boxW, boxH, boxD, boxMaterial, x, y + boxH / 2, z);
                            boxMesh.userData.rackId = rack.id;
                            boxMesh.userData.rackItemKey = itemKey;
                            boxMesh.userData.itemName = item.name || "";
                            boxMesh.userData.root = group;
                            group.add(boxMesh);
                            itemHitboxes.push(boxMesh);
                            if (shouldShowItemLabel) {{
                                group.add(makeLabel(shortLabel(item.name, 10), new THREE.Vector3(x, y + boxH + 0.34, z), 0.34));
                            }}
                        }}
                    }});
                }});

                const hitbox = rackPart(world.w, rackHeight, world.d, materials.hitbox, 0, rackHeight / 2, 0);
                hitbox.userData.rackId = rack.id;
                hitbox.userData.root = group;
                group.add(hitbox);
                group.userData.hitbox = hitbox;
                group.userData.itemHitboxes = itemHitboxes;

                if (rack.locked) {{
                    group.add(rackPart(Math.min(0.9, world.w * 0.28), 0.12, 0.26, materials.locked, 0, rackHeight + 0.18, -halfD + 0.24));
                }}

                if (rack.id === selectedRackId) {{
                    const selection = new THREE.LineSegments(
                        new THREE.EdgesGeometry(new THREE.BoxGeometry(world.w + 0.28, rackHeight + 0.18, world.d + 0.28)),
                        materials.selected
                    );
                    selection.position.set(0, rackHeight / 2, 0);
                    group.add(selection);

                    if (!rack.locked) {{
                        group.userData.resizeHandles = [];
                        const handleSize = Math.min(0.34, Math.max(0.22, Math.min(world.w, world.d) * 0.08));
                        const handleY = Math.max(0.48, shelfYs[0] + 0.14);
                        [
                            {{ key: "w", x: -1, z: 0, cursor: "ew-resize" }},
                            {{ key: "e", x: 1, z: 0, cursor: "ew-resize" }},
                            {{ key: "n", x: 0, z: -1, cursor: "ns-resize" }},
                            {{ key: "s", x: 0, z: 1, cursor: "ns-resize" }},
                            {{ key: "nw", x: -1, z: -1, cursor: "nwse-resize" }},
                            {{ key: "ne", x: 1, z: -1, cursor: "nesw-resize" }},
                            {{ key: "sw", x: -1, z: 1, cursor: "nesw-resize" }},
                            {{ key: "se", x: 1, z: 1, cursor: "nwse-resize" }},
                        ].forEach(handle => {{
                            const mesh = rackPart(handleSize, handleSize, handleSize, materials.resizeHandle, handle.x * halfW, handleY, handle.z * halfD);
                            mesh.userData.rackId = rack.id;
                            mesh.userData.resizeHandle = handle;
                            group.add(mesh);
                            group.userData.resizeHandles.push(mesh);
                        }});
                    }}
                }}

                return group;
            }}

            function buildWarehouseModel() {{
                clearGroup(buildingGroup);
                floorResizeHandles.length = 0;
                const size = currentFloorSize();
                const gridSize = layoutFloorSize();
                const length = size.width;
                const depth = size.depth;
                const centerX = Number(size.x || 0);
                const centerZ = Number(size.z || 0);
                const floorThickness = 0.16;

                const grid = new THREE.GridHelper(Math.max(gridSize.width, gridSize.depth) + 10, 34, 0x2fe3d0, 0x164944);
                grid.position.y = -0.08;
                buildingGroup.add(grid);

                const slab = makeBox(length, floorThickness, depth, materials.activeSlab, new THREE.Vector3(centerX, 0, centerZ));
                buildingGroup.add(slab);
                const outline = new THREE.LineSegments(new THREE.EdgesGeometry(slab.geometry), materials.floorEdge);
                outline.position.copy(slab.position);
                buildingGroup.add(outline);

                [
                    {{ key: "w", x: -1, z: 0, cursor: "ew-resize" }},
                    {{ key: "e", x: 1, z: 0, cursor: "ew-resize" }},
                    {{ key: "n", x: 0, z: -1, cursor: "ns-resize" }},
                    {{ key: "s", x: 0, z: 1, cursor: "ns-resize" }},
                    {{ key: "nw", x: -1, z: -1, cursor: "nwse-resize" }},
                    {{ key: "ne", x: 1, z: -1, cursor: "nesw-resize" }},
                    {{ key: "sw", x: -1, z: 1, cursor: "nesw-resize" }},
                    {{ key: "se", x: 1, z: 1, cursor: "nwse-resize" }},
                ].forEach(handle => {{
                    const handleWidth = handle.z === 0 ? 0.92 : 0.72;
                    const handleDepth = handle.x === 0 ? 0.92 : 0.72;
                    const mesh = rackPart(handleWidth, 0.22, handleDepth, materials.resizeHandle, centerX + handle.x * length / 2, 0.24, centerZ + handle.z * depth / 2);
                    mesh.userData.floorHandle = handle;
                    buildingGroup.add(mesh);
                    floorResizeHandles.push(mesh);
                }});
            }}

            function buildFixtures() {{
                clearGroup(fixtureGroup);
                fixtureBodies.length = 0;
                fixtureResizeHandles.length = 0;
                fixtureObjectById.clear();
                fixtures.forEach(fixture => {{
                    const model = makeFixture(fixture);
                    fixtureGroup.add(model);
                    fixtureBodies.push(model.userData.hitbox);
                    (model.userData.resizeHandles || []).forEach(handle => fixtureResizeHandles.push(handle));
                    fixtureObjectById.set(fixture.id, model);
                }});
            }}

            function buildRacks() {{
                racks = normalizeRackIds(racks);
                if (selectedRackId && !racks.some(rack => rack.id === selectedRackId)) {{
                    selectedRackId = racks[0]?.id || "";
                    selectedRackItemKey = "";
                }}
                clearGroup(rackGroup);
                rackBodies.length = 0;
                rackItemBodies.length = 0;
                rackResizeHandles.length = 0;
                rackObjectById.clear();
                const floorY = 0.18;
                racks.forEach(rack => {{
                    const world = rackToWorld(rack);
                    const model = makeShelfRack(rack, world, floorY + rackStackBaseY(rack));
                    rackGroup.add(model);
                    rackBodies.push(model.userData.hitbox);
                    (model.userData.itemHitboxes || []).forEach(hitbox => rackItemBodies.push(hitbox));
                    (model.userData.resizeHandles || []).forEach(handle => rackResizeHandles.push(handle));
                    rackObjectById.set(rack.id, model);
                }});
            }}

            function rebuildScene() {{
                buildWarehouseModel();
                buildFixtures();
                buildRacks();
                renderTargetRackSelect();
                renderTargetFloorSelect();
                renderFloorControls();
                renderRack(selectedRack());
            }}

            function renderFloorControls() {{
                document.querySelectorAll(".floor-chip").forEach(button => {{
                    button.classList.toggle("active", button.dataset.floor === activeFloor);
                }});
                currentFloorLabel.textContent = activeFloor;
                syncFloorSizeInputs();
            }}

            function selectedRack() {{
                return racks.find(rack => rack.id === selectedRackId);
            }}

            function selectedFixture() {{
                return fixtures.find(fixture => fixture.id === selectedFixtureId);
            }}

            function isLoadFixture(fixture) {{
                return ["box", "pallet", "wrapped_pallet"].includes(fixture?.type);
            }}

            function targetRack() {{
                const selectedTarget = racks.find(rack => rack.id === targetRackSelect.value);
                return selectedTarget || racks[0];
            }}

            function renderTargetRackSelect(preferredRackId = targetRackSelect.value) {{
                const preferredRack = racks.find(rack => rack.id === preferredRackId);
                targetRackSelect.innerHTML = racks.length
                    ? racks.map(rack => {{
                        const lockText = rack.locked ? " · 위치고정" : "";
                        return `<option value="${{escapeHtml(rack.id)}}">${{escapeHtml(rackDisplayName(rack))}}${{lockText}}</option>`;
                    }}).join("")
                    : '<option value="">이동할 랙 없음</option>';
                if (preferredRack) {{
                    targetRackSelect.value = preferredRackId;
                }} else {{
                    targetRackSelect.value = racks[0]?.id || "";
                }}
                renderTargetRackPartSelect();
            }}

            function rackHasAncestor(rack, ancestorId, visited = new Set()) {{
                if (!rack?.parentRackId || visited.has(rack.id)) return false;
                if (rack.parentRackId === ancestorId) return true;
                visited.add(rack.id);
                const parent = racks.find(row => row.id === rack.parentRackId);
                return rackHasAncestor(parent, ancestorId, visited);
            }}

            function stackTargetOptions(rack) {{
                if (!rack) return [];
                return racks.filter(target =>
                    target.id !== rack.id
                    && !rackHasAncestor(target, rack.id)
                );
            }}

            function renderStackTargetSelect(preferredRackId = rackStackTargetSelect.value) {{
                const rack = selectedRack();
                const options = stackTargetOptions(rack);
                rackStackTargetSelect.innerHTML = options.length
                    ? options.map(target => `<option value="${{escapeHtml(target.id)}}">${{escapeHtml(rackDisplayName(target))}}</option>`).join("")
                    : '<option value="">올릴 랙 없음</option>';
                rackStackTargetSelect.value = options.some(target => target.id === preferredRackId)
                    ? preferredRackId
                    : (options[0]?.id || "");
                const canStack = Boolean(rack) && Boolean(rackStackTargetSelect.value) && !rack.locked;
                stackRackButton.disabled = !canStack;
                unstackRackButton.disabled = !rack || !rack.parentRackId || rack.locked;
            }}

            function renderTargetRackPartSelect(preferredPart = targetRackPartSelect.value) {{
                const rack = targetRack();
                const options = partOptionsFor(rack);
                targetRackPartSelect.innerHTML = options.map(part => `<option value="${{part}}">${{part}}</option>`).join("");
                targetRackPartSelect.value = options.includes(preferredPart) ? preferredPart : options[0];
            }}

            function renderTargetFloorSelect(preferredFloor = targetFloorSelect.value || activeFloor) {{
                targetFloorSelect.innerHTML = floors.map(floorName => `<option value="${{floorName}}">${{floorName}}</option>`).join("");
                targetFloorSelect.value = floors.includes(preferredFloor) ? preferredFloor : activeFloor;
            }}

            function floorOptionsHtml(selectedFloor = activeFloor) {{
                return floors.map(floorName =>
                    `<option value="${{floorName}}" ${{floorName === selectedFloor ? "selected" : ""}}>${{floorName}}</option>`
                ).join("");
            }}

            function updateFixtureButtons() {{
                const hasFixture = Boolean(selectedFixture());
                const fixture = selectedFixture();
                const fixtureLocked = Boolean(fixture?.locked);
                rotateFixtureButton.disabled = !hasFixture || fixtureLocked;
                lockFixtureButton.disabled = !hasFixture;
                lockFixtureButton.textContent = fixtureLocked ? "고정 해제" : "고정";
                deleteFixtureButton.disabled = !hasFixture || fixtureLocked;
                const rack = targetRack();
                const canMoveToRack = isLoadFixture(fixture) && Boolean(rack) && !fixtureLocked;
                targetRackSelect.disabled = !isLoadFixture(fixture) || !racks.length || fixtureLocked;
                targetRackPartSelect.disabled = !isLoadFixture(fixture) || !racks.length || fixtureLocked;
                moveFixtureToRackButton.disabled = !canMoveToRack;
                targetFloorSelect.disabled = true;
                moveSelectionFloorButton.disabled = true;
            }}

            function partOptionsFor(rack) {{
                const levels = [2, 3].includes(Number(rack?.levels)) ? Number(rack.levels) : 2;
                const roofPart = `${{levels}}단 지붕칸`;
                if (rackIsRoofOnly(rack)) return [roofPart];
                const bottomOpen = Boolean(rack?.bottomOpen);
                if (levels === 2) return bottomOpen ? ["2단", roofPart] : ["1단", "2단", roofPart];
                if (levels === 3) return bottomOpen ? ["2단", "3단", roofPart] : ["1단", "2단", "3단", roofPart];
                return ["1단", roofPart];
            }}

            function renderPartSelect(rack) {{
                const options = partOptionsFor(rack);
                const previous = partSelect.value;
                partSelect.innerHTML = options.map(part => `<option value="${{part}}">${{part}}</option>`).join("");
                partSelect.value = options.includes(previous) ? previous : options[0];
            }}

            function selectRack(rackId) {{
                selectedRackId = rackId || "";
                selectedFixtureId = "";
                selectedRackItemKey = "";
                buildRacks();
                buildFixtures();
                renderRack(selectedRack());
            }}

            function selectFixture(fixtureId) {{
                selectedFixtureId = fixtureId || "";
                selectedRackId = "";
                selectedRackItemKey = "";
                buildRacks();
                buildFixtures();
                renderFixture(selectedFixture());
            }}

            function renderFixture(fixture) {{
                if (!fixture) return;
                renderTargetRackSelect(targetRackSelect.value);
                if (isLoadFixture(fixture)) renderTargetRackPartSelect();
                renderTargetFloorSelect(targetFloorSelect.value || activeFloor);
                lockButton.disabled = true;
                rotateButton.disabled = true;
                rackLevelSelect.disabled = false;
                rackBottomSelect.disabled = false;
                deleteButton.disabled = true;
                stackRackButton.disabled = true;
                unstackRackButton.disabled = true;
                fixture.items = fixture.items || [];
                const fixtureLoadText = ["box", "pallet", "wrapped_pallet"].includes(fixture.type)
                    ? ` · 적재 ${{loadQtyText(fixture)}}`
                    : "";
                const fixtureLockText = fixture.locked ? " · 고정됨" : " · 이동 가능";
                rackDetail.innerHTML = `
                    <strong>${{escapeHtml(fixture.label || "시설물")}}</strong>
                    <span>${{escapeHtml(fixture.type || "fixture")}} · 3D 위치 X ${{Number(fixture.x).toFixed(1)}}%, Y ${{Number(fixture.y).toFixed(1)}}% · 회전 ${{Number(fixture.rotation || 0)}}도${{fixtureLoadText}}${{fixtureLockText}}</span>
                `;
                if (fixture.type === "pallet" || fixture.type === "wrapped_pallet") {{
                    const deleteFixtureRow = `
                        <tr>
                            <td colspan="5">선택한 파렛트 전체</td>
                            <td><button type="button" data-fixture-delete="1">삭제</button></td>
                        </tr>
                    `;
                    if (!fixture.items.length) {{
                        itemBody.innerHTML = deleteFixtureRow + '<tr><td colspan="6" class="empty">이 파렛트에 들어간 품목이 없습니다. 파렛트를 선택한 상태에서 상품명/바코드/수량을 입력하고 추가하세요.</td></tr>';
                    }} else {{
                        itemBody.innerHTML = deleteFixtureRow + fixture.items.map((item, index) => `
                            <tr>
                                <td>파렛트</td>
                                <td>${{shapeLabel(item.shape || "box")}}</td>
                                <td>${{escapeHtml(item.name)}}</td>
                                <td>${{escapeHtml(item.barcode || "-")}}</td>
                                <td>${{loadQtyText(item)}}</td>
                                <td><button type="button" data-pallet-remove="${{index}}">삭제</button></td>
                            </tr>
                        `).join("");
                        itemBody.querySelectorAll("[data-pallet-remove]").forEach(button => {{
                            button.addEventListener("click", () => {{
                                fixture.items.splice(Number(button.dataset.palletRemove), 1);
                                saveFixtures();
                                buildFixtures();
                                renderFixture(fixture);
                            }});
                        }});
                    }}
                    itemBody.querySelector("[data-fixture-delete]")?.addEventListener("click", () => {{
                        deleteSelectedFixture();
                    }});
                }} else {{
                    itemBody.innerHTML = isLoadFixture(fixture)
                        ? `<tr><td colspan="5">선택한 바닥 품목 · 바코드 ${{escapeHtml(fixture.barcode || "-")}}</td><td><button type="button" data-fixture-delete="1">삭제</button></td></tr><tr><td colspan="6" class="empty">이 품목은 이동할 랙과 단을 선택한 뒤 랙에 넣기로 적재할 수 있습니다.</td></tr>`
                        : '<tr><td colspan="6" class="empty">시설물은 선택 후 Shift+드래그로 위치를 옮기고, 시설물 배치 도구에서 회전/삭제할 수 있습니다.</td></tr>';
                    itemBody.querySelector("[data-fixture-delete]")?.addEventListener("click", () => {{
                        deleteSelectedFixture();
                    }});
                }}
                updateFixtureButtons();
            }}

            function rackItemKey(item, index) {{
                return `${{item.part || shelfParts[shelfPartIndex(item.part, index)]}}::${{item.shape || "box"}}::${{item.stack || 1}}::${{item.barcode || item.name}}`;
            }}

            function renderRack(rack) {{
                if (!rack) {{
                    rackDetail.innerHTML = "<strong>랙을 선택하세요</strong><span>선택된 랙이 없습니다.</span>";
                    itemBody.innerHTML = '<tr><td colspan="6" class="empty">선택된 랙이 없습니다.</td></tr>';
                    renderPartSelect(null);
                    lockButton.disabled = true;
                    lockButton.textContent = "랙 고정";
                    rotateButton.disabled = true;
                    rackLevelSelect.disabled = false;
                    rackBottomSelect.disabled = false;
                    stackRackButton.disabled = true;
                    unstackRackButton.disabled = true;
                    deleteButton.disabled = true;
                    updateFixtureButtons();
                    return;
                }}
                const loadedQty = rackLoadedQty(rack);
                const lockText = rack.locked ? "위치 고정 · 적재 가능" : "이동 가능";
                const typeText = (rack.type || "light") === "heavy" ? "중량랙" : "경량랙";
                const stackText = rack.parentRackId ? ` · ${{rack.parentRackId}} 위 적층` : "";
                const renderPosition = rackRenderPosition(rack);
                const directionText = Number(rack.rotation || 0) % 180 === 90 ? "세로 방향" : "가로 방향";
                rack.levels = rackLevelCount(rack);
                rack.roofOnly = rackIsRoofOnly(rack);
                rack.bottomOpen = Boolean(rack.bottomOpen);
                const allowedParts = partOptionsFor(rack);
                let partChanged = false;
                rack.items = (rack.items || []).map((item, index) => {{
                    if (allowedParts.includes(item.part)) return item;
                    partChanged = true;
                    return {{ ...item, part: allowedParts[index % allowedParts.length] }};
                }});
                if (partChanged) saveLayout();
                rackTypeSelect.value = rack.type || "light";
                rackLevelSelect.value = String(rack.levels);
                rackBottomSelect.value = rack.roofOnly ? "roof" : rack.bottomOpen ? "open" : "normal";
                rackBottomSelect.disabled = false;
                rackLevelSelect.disabled = false;
                renderPartSelect(rack);
                renderStackTargetSelect(rack.parentRackId || "");
                lockButton.disabled = false;
                lockButton.textContent = rack.locked ? "고정 해제" : "랙 고정";
                rotateButton.disabled = Boolean(rack.locked);
                deleteButton.disabled = Boolean(rack.locked);
                updateFixtureButtons();
                rackDetail.innerHTML = `
                    <strong>${{escapeHtml(rackDisplayName(rack))}} / ${{escapeHtml(activeFloor)}}</strong>
                    <span>${{rackDisplayType(rack)}} · ${{directionText}} · 3D 위치 X ${{Number(renderPosition.x).toFixed(1)}}%, Y ${{Number(renderPosition.y).toFixed(1)}}%${{stackText}} · 적재 ${{loadedQty.toLocaleString("ko-KR")}}개 · ${{lockText}}</span>
                `;
                if (!rack.items.length) {{
                    itemBody.innerHTML = '<tr><td colspan="6" class="empty">이 랙에 연결된 품목이 없습니다.</td></tr>';
                    return;
                }}
                itemBody.innerHTML = rack.items.map((item, index) => `
                    <tr>
                        <td>${{escapeHtml(item.part || shelfParts[shelfPartIndex(item.part, index)])}}</td>
                        <td>${{shapeLabel(item.shape || "box")}}</td>
                        <td>${{escapeHtml(item.name)}}</td>
                        <td>${{escapeHtml(item.barcode || "-")}}</td>
                        <td>${{loadQtyText(item)}}</td>
                        <td>
                            <div class="row-actions">
                                <button type="button" data-remove="${{escapeHtml(rackItemKey(item, index))}}">삭제</button>
                            </div>
                        </td>
                    </tr>
                `).join("");
                itemBody.querySelectorAll("[data-remove]").forEach(button => {{
                    button.addEventListener("click", () => {{
                        const key = button.dataset.remove;
                        rack.items = rack.items.filter((item, index) => rackItemKey(item, index) !== key);
                        saveLayout();
                        buildRacks();
                        renderRack(rack);
                    }});
                }});
            }}

            function renderItemSelect() {{
                const emptyOption = '<option value="">직접입력 / 재고 선택 없음</option>';
                itemSelect.innerHTML = inventory.length
                    ? emptyOption + inventory.map((item, index) => `<option value="${{index}}">${{escapeHtml(item.name)}} / 현재고 ${{Number(item.stock || 0).toLocaleString("ko-KR")}}</option>`).join("")
                    : emptyOption;
            }}

            function resizeRenderer() {{
                const bounds = viewport.getBoundingClientRect();
                renderer.setSize(bounds.width, bounds.height, false);
                camera.aspect = bounds.width / Math.max(1, bounds.height);
                camera.updateProjectionMatrix();
            }}

            function pointerToNdc(event) {{
                const bounds = canvas.getBoundingClientRect();
                pointer.x = ((event.clientX - bounds.left) / bounds.width) * 2 - 1;
                pointer.y = -((event.clientY - bounds.top) / bounds.height) * 2 + 1;
            }}

            function pickRack(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(rackBodies, false);
                return hits[0]?.object || null;
            }}

            function pickRackItem(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(rackItemBodies, false);
                return hits[0]?.object || null;
            }}

            function pickRackResizeHandle(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(rackResizeHandles, false);
                return hits[0]?.object || null;
            }}

            function pickFixtureResizeHandle(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(fixtureResizeHandles, false);
                return hits[0]?.object || null;
            }}

            function pickFloorResizeHandle(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(floorResizeHandles, false);
                return hits[0]?.object || null;
            }}

            function resizeSelectedRackFromDrag() {{
                if (!resizingRack || !resizeState) return;
                const size = layoutFloorSize();
                const handle = resizeState.handle;
                const angle = THREE.MathUtils.degToRad(Number(resizingRack.rotation || 0));
                const dxWorld = dragPoint.x - resizeState.startPoint.x;
                const dzWorld = dragPoint.z - resizeState.startPoint.z;
                const localDxWorld = Math.cos(angle) * dxWorld - Math.sin(angle) * dzWorld;
                const localDzWorld = Math.sin(angle) * dxWorld + Math.cos(angle) * dzWorld;
                const localDxPct = localDxWorld / Math.max(1, size.width) * 100;
                const localDzPct = localDzWorld / Math.max(1, size.depth) * 100;
                const minW = resizingRack.type === "heavy" ? 7.2 : 5.8;
                const minH = resizingRack.type === "heavy" ? 5.6 : 4.6;
                const maxW = 34;
                const maxH = 28;
                const nextW = handle.x ? clamp(resizeState.start.w + handle.x * localDxPct, minW, maxW) : resizeState.start.w;
                const nextH = handle.z ? clamp(resizeState.start.h + handle.z * localDzPct, minH, maxH) : resizeState.start.h;
                const shiftLocalXPct = handle.x ? handle.x * (nextW - resizeState.start.w) / 2 : 0;
                const shiftLocalZPct = handle.z ? handle.z * (nextH - resizeState.start.h) / 2 : 0;
                const shiftLocalXWorld = shiftLocalXPct * size.width / 100;
                const shiftLocalZWorld = shiftLocalZPct * size.depth / 100;
                const shiftWorldX = Math.cos(angle) * shiftLocalXWorld + Math.sin(angle) * shiftLocalZWorld;
                const shiftWorldZ = -Math.sin(angle) * shiftLocalXWorld + Math.cos(angle) * shiftLocalZWorld;
                resizingRack.w = nextW;
                resizingRack.h = nextH;
                resizingRack.x = clamp(resizeState.start.x + shiftWorldX / Math.max(1, size.width) * 100, 1, 99);
                resizingRack.y = clamp(resizeState.start.y + shiftWorldZ / Math.max(1, size.depth) * 100, 1, 99);
                buildRacks();
                renderRack(resizingRack);
            }}

            function resizeSelectedFixtureFromDrag() {{
                if (!resizingFixture || !resizeState) return;
                const size = layoutFloorSize();
                const handle = resizeState.handle;
                const angle = THREE.MathUtils.degToRad(Number(resizingFixture.rotation || 0));
                const dxWorld = dragPoint.x - resizeState.startPoint.x;
                const dzWorld = dragPoint.z - resizeState.startPoint.z;
                const localDxWorld = Math.cos(angle) * dxWorld - Math.sin(angle) * dzWorld;
                const localDzWorld = Math.sin(angle) * dxWorld + Math.cos(angle) * dzWorld;
                const minW = resizingFixture.type === "wall" ? 0.8 : 0.6;
                const minD = resizingFixture.type === "wall" ? 0.12 : 0.4;
                const maxW = size.width * 1.4;
                const maxD = size.depth * 1.4;
                const nextW = handle.x ? clamp(resizeState.start.w + handle.x * localDxWorld, minW, maxW) : resizeState.start.w;
                const nextD = handle.z ? clamp(resizeState.start.d + handle.z * localDzWorld, minD, maxD) : resizeState.start.d;
                const shiftLocalXWorld = handle.x ? handle.x * (nextW - resizeState.start.w) / 2 : 0;
                const shiftLocalZWorld = handle.z ? handle.z * (nextD - resizeState.start.d) / 2 : 0;
                const shiftWorldX = Math.cos(angle) * shiftLocalXWorld + Math.sin(angle) * shiftLocalZWorld;
                const shiftWorldZ = -Math.sin(angle) * shiftLocalXWorld + Math.cos(angle) * shiftLocalZWorld;
                const allowOutside = fixtureAllowsOutside(resizingFixture.type);
                const nextPosition = worldToPercent(resizeState.startWorld.x + shiftWorldX, resizeState.startWorld.z + shiftWorldZ, allowOutside);
                resizingFixture.w = nextW;
                resizingFixture.d = nextD;
                resizingFixture.x = nextPosition.x;
                resizingFixture.y = nextPosition.y;
                buildFixtures();
                renderFixture(resizingFixture);
            }}

            function resizeFloorFromDrag() {{
                if (!resizingFloor || !resizeState) return;
                const handle = resizeState.handle;
                const base = baseFloorSize(activeFloor);
                const dxWorld = dragPoint.x - resizeState.startPoint.x;
                const dzWorld = dragPoint.z - resizeState.startPoint.z;
                const width = handle.x ? clamp(resizeState.start.width + handle.x * dxWorld, base.width * 0.45, base.width * 2.6) : resizeState.start.width;
                const depth = handle.z ? clamp(resizeState.start.depth + handle.z * dzWorld, base.depth * 0.45, base.depth * 2.6) : resizeState.start.depth;
                const x = resizeState.start.x + handle.x * (width - resizeState.start.width) / 2;
                const z = resizeState.start.z + handle.z * (depth - resizeState.start.depth) / 2;
                saveFloorSize(activeFloor, {{ width, depth, x, z }});
                refreshFloorOnly();
            }}

            function pickFixture(event) {{
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                const hits = raycaster.intersectObjects(fixtureBodies, false);
                return hits[0]?.object || null;
            }}

            function claimCanvasDrag(event) {{
                event.preventDefault();
                event.stopPropagation();
                if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
                controls.enabled = false;
                try {{ canvas.setPointerCapture(event.pointerId); }} catch (error) {{}}
            }}

            function deleteSelectedRack() {{
                if (!selectedRackId) return;
                const rack = selectedRack();
                if (rack?.locked) return;
                racks.forEach(row => {{
                    if (row.parentRackId === selectedRackId) row.parentRackId = "";
                }});
                racks = racks.filter(rack => rack.id !== selectedRackId);
                selectedRackId = racks[0]?.id || "";
                selectedRackItemKey = "";
                saveLayout();
                rebuildScene();
            }}

            function deleteSelectedFixture() {{
                if (!selectedFixtureId) return;
                const fixture = selectedFixture();
                if (fixture?.locked) return;
                fixtures = fixtures.filter(fixture => fixture.id !== selectedFixtureId);
                selectedFixtureId = "";
                selectedRackItemKey = "";
                saveFixtures();
                buildFixtures();
                renderRack(selectedRack());
            }}

            function syncStackInput() {{
                const isPallet = loadShapeSelect.value === "pallet";
                stackSelect.disabled = !isPallet;
                if (!isPallet) stackSelect.value = "1";
            }}

            function selectedInventoryItem() {{
                const rawValue = itemSelect.value;
                if (rawValue === "") return null;
                const index = Number(rawValue);
                if (!Number.isInteger(index) || index < 0 || index >= inventory.length) return null;
                return inventory[index];
            }}

            function loadInputData() {{
                const inventoryItem = selectedInventoryItem();
                const manualName = manualItemName.value.trim();
                const manualBarcode = manualItemBarcode.value.trim();
                const barcode = manualBarcode || inventoryItem?.barcode || "";
                const name = manualName || inventoryItem?.name || barcode || "";
                if (!name) return null;
                const shape = loadShapeSelect.value || "box";
                const isManual = Boolean(manualName || manualBarcode);
                return {{
                    name,
                    barcode,
                    stock: Number(inventoryItem?.stock || 0),
                    status: isManual ? "manual" : (inventoryItem?.status || ""),
                    qty: Math.max(1, Number(itemQty.value || 1)),
                    part: partSelect.value || "1단",
                    shape,
                    stack: shape === "pallet" ? clamp(Number(stackSelect.value || 1), 1, 2) : 1,
                }};
            }}

            function addLoadToRack(rack, load) {{
                if (!rack) return false;
                rack.items = rack.items || [];
                const allowedParts = partOptionsFor(rack);
                const nextLoad = {{
                    ...load,
                    barcode: String(load.barcode || "").trim(),
                    part: allowedParts.includes(load.part) ? load.part : allowedParts[0],
                }};
                const key = `${{nextLoad.part}}::${{nextLoad.shape}}::${{nextLoad.stack || 1}}::${{nextLoad.barcode || nextLoad.name}}`;
                const existing = rack.items.find(row => `${{row.part || "1단"}}::${{row.shape || "box"}}::${{row.stack || 1}}::${{row.barcode || row.name}}` === key);
                if (existing) {{
                    existing.qty = Number(existing.qty || existing.stock || 0) + Number(nextLoad.qty || 0);
                    existing.part = nextLoad.part;
                    existing.shape = nextLoad.shape;
                    existing.stack = nextLoad.stack || 1;
                    existing.barcode = nextLoad.barcode;
                    existing.name = nextLoad.name || existing.name;
                    if (Array.isArray(nextLoad.items) && nextLoad.items.length) {{
                        existing.items = [...(existing.items || []), ...nextLoad.items];
                    }}
                }} else {{
                    rack.items.push(nextLoad);
                }}
                return true;
            }}

            function moveRackItemToFloor(sourceRack, itemKey, targetFloor) {{
                if (!sourceRack || !itemKey || !targetFloor || targetFloor === activeFloor) return;
                sourceRack.items = sourceRack.items || [];
                const itemIndex = sourceRack.items.findIndex((item, index) => rackItemKey(item, index) === itemKey);
                if (itemIndex < 0) return;
                const [movingItem] = sourceRack.items.splice(itemIndex, 1);
                const targetRacks = loadLayout(targetFloor);
                const targetRack = targetRacks.find(rack => rack.id === sourceRack.id) || targetRacks[0];
                if (!targetRack) {{
                    sourceRack.items.splice(itemIndex, 0, movingItem);
                    return;
                }}
                const targetParts = partOptionsFor(targetRack);
                const movedLoad = {{
                    ...movingItem,
                    floor: targetFloor,
                    part: targetParts.includes(movingItem.part) ? movingItem.part : targetParts[0],
                }};
                if (!addLoadToRack(targetRack, movedLoad)) {{
                    sourceRack.items.splice(itemIndex, 0, movingItem);
                    return;
                }}
                saveLayout();
                saveLayoutFor(targetFloor, targetRacks);
                buildRacks();
                renderRack(sourceRack);
            }}

            function addLoadToPallet(fixture, load) {{
                fixture.items = fixture.items || [];
                const palletLoad = {{
                    ...load,
                    part: "파렛트 내부",
                    shape: "box",
                    stack: 1,
                }};
                const key = `${{palletLoad.shape}}::${{palletLoad.barcode || palletLoad.name}}`;
                const existing = fixture.items.find(row => `${{row.shape || "box"}}::${{row.barcode || row.name}}` === key);
                if (existing) {{
                    existing.qty = Number(existing.qty || existing.stock || 0) + palletLoad.qty;
                }} else {{
                    fixture.items.push(palletLoad);
                }}
                saveFixtures();
                buildFixtures();
                renderFixture(fixture);
            }}

            function fixtureLoadForRack(fixture, rack) {{
                const allowedParts = partOptionsFor(rack);
                const part = allowedParts.includes(targetRackPartSelect.value) ? targetRackPartSelect.value : allowedParts[0];
                const isPallet = fixture.type === "pallet" || fixture.type === "wrapped_pallet";
                return {{
                    name: fixture.label || (isPallet ? "파렛트" : "박스"),
                    barcode: fixture.barcode || `오브젝트:${{fixture.id}}`,
                    stock: Number(fixture.qty || 1),
                    qty: Number(fixture.qty || 1),
                    status: "floor-object",
                    part,
                    shape: isPallet ? "pallet" : "box",
                    stack: isPallet ? clamp(Number(fixture.stack || 1), 1, 2) : 1,
                    items: Array.isArray(fixture.items) ? fixture.items : [],
                }};
            }}

            function rackDropPosition(rack, load) {{
                const world = rackToWorld(rack);
                const baseY = 0.18 + rackStackBaseY(rack);
                const isPallet = load?.shape === "pallet" || load?.shape === "wrapped_pallet";
                const dropY = baseY + (isPallet ? 1.26 : 0.78);
                return new THREE.Vector3(world.x, dropY, world.z);
            }}

            function animateFixtureIntoRack(fixture, rack, load, onDone) {{
                const model = fixtureObjectById.get(fixture.id);
                if (!model) {{
                    onDone();
                    return;
                }}
                const startPosition = model.position.clone();
                const startScale = model.scale.clone();
                const endPosition = rackDropPosition(rack, load);
                const startedAt = performance.now();
                const duration = 420;
                rackDropAnimation = {{ fixtureId: fixture.id }};
                moveFixtureToRackButton.disabled = true;

                function step(now) {{
                    if (!rackDropAnimation || rackDropAnimation.fixtureId !== fixture.id) return;
                    const t = clamp((now - startedAt) / duration, 0, 1);
                    const eased = t < 0.5
                        ? 4 * t * t * t
                        : 1 - Math.pow(-2 * t + 2, 3) / 2;
                    model.position.lerpVectors(startPosition, endPosition, eased);
                    const shrink = 1 - 0.42 * eased;
                    model.scale.set(startScale.x * shrink, startScale.y * shrink, startScale.z * shrink);
                    if (t < 1) {{
                        requestAnimationFrame(step);
                        return;
                    }}
                    rackDropAnimation = null;
                    model.scale.copy(startScale);
                    onDone();
                }}

                requestAnimationFrame(step);
            }}

            function moveSelectedFixtureToRack() {{
                const fixture = selectedFixture();
                const rack = targetRack();
                if (!isLoadFixture(fixture) || !rack || fixture.locked) return;
                const load = fixtureLoadForRack(fixture, rack);
                if (!addLoadToRack(rack, load)) return;
                saveLayout();
                animateFixtureIntoRack(fixture, rack, load, () => {{
                    fixtures = fixtures.filter(row => row.id !== fixture.id);
                    selectedFixtureId = "";
                    selectedRackId = rack.id;
                    selectedRackItemKey = "";
                    saveFixtures();
                    buildFixtures();
                    buildRacks();
                    renderRack(rack);
                }});
            }}

            function moveSelectedFixtureToFloor() {{
                const fixture = selectedFixture();
                const targetFloor = targetFloorSelect.value;
                if (!fixture || fixture.locked || !targetFloor || targetFloor === activeFloor) return;
                fixtures = fixtures.filter(row => row.id !== fixture.id);
                const targetFixtures = loadFixtures(targetFloor);
                targetFixtures.push({{ ...fixture, floor: targetFloor }});
                selectedFixtureId = "";
                selectedRackItemKey = "";
                saveFixtures();
                saveFixturesFor(targetFloor, targetFixtures);
                buildFixtures();
                renderRack(selectedRack());
            }}

            function addLoadFromInputs() {{
                const load = loadInputData();
                if (!load) return;
                const rack = selectedRack();
                if (rack) {{
                    if (!addLoadToRack(rack, load)) return;
                    selectedRackItemKey = "";
                    saveLayout();
                    buildRacks();
                    renderRack(rack);
                }} else {{
                    const fixture = selectedFixture();
                    if (fixture?.type === "pallet" || fixture?.type === "wrapped_pallet") {{
                        addLoadToPallet(fixture, load);
                        manualItemName.value = "";
                        manualItemBarcode.value = "";
                        itemQty.value = "1";
                        return;
                    }}
                    const template = fixtureDefaults[load.shape] || fixtureDefaults.box;
                    const newFixture = normalizeFixture({{
                        ...template,
                        id: `F-${{Date.now().toString(36).slice(-6)}}`,
                        type: load.shape,
                        label: load.name,
                        barcode: load.barcode,
                        qty: load.qty,
                        stack: load.stack,
                        floor: activeFloor,
                        x: 50,
                        y: 50,
                        rotation: 0,
                    }});
                    fixtures.push(newFixture);
                    selectedRackId = "";
                    selectedFixtureId = newFixture.id;
                    selectedRackItemKey = "";
                    saveFixtures();
                    buildRacks();
                    buildFixtures();
                    renderFixture(newFixture);
                }}
                manualItemName.value = "";
                manualItemBarcode.value = "";
                itemQty.value = "1";
            }}

            canvas.addEventListener("pointerdown", event => {{
                const resizeHandle = pickRackResizeHandle(event);
                if (resizeHandle) {{
                    const rack = racks.find(row => row.id === resizeHandle.userData.rackId);
                    if (!rack) return;
                    if (rack.parentRackId) return;
                    if (rack.locked) return;
                    selectedRackId = rack.id;
                    selectedFixtureId = "";
                    selectedRackItemKey = "";
                    claimCanvasDrag(event);
                    resizingRack = rack;
                    const planeY = 0.18;
                    dragPlane.set(new THREE.Vector3(0, 1, 0), -planeY);
                    raycaster.ray.intersectPlane(dragPlane, dragPoint);
                    resizeState = {{
                        handle: resizeHandle.userData.resizeHandle,
                        startPoint: dragPoint.clone(),
                        start: {{
                            x: Number(rack.x || 50),
                            y: Number(rack.y || 50),
                            w: Number(rack.w || 10.8),
                            h: Number(rack.h || 8.4),
                        }},
                    }};
                    return;
                }}

                const fixtureResizeHandle = pickFixtureResizeHandle(event);
                if (fixtureResizeHandle) {{
                    const fixture = fixtures.find(row => row.id === fixtureResizeHandle.userData.fixtureId);
                    if (!fixture) return;
                    selectedFixtureId = fixture.id;
                    selectedRackId = "";
                    selectedRackItemKey = "";
                    if (fixture.locked) return;
                    claimCanvasDrag(event);
                    resizingFixture = fixture;
                    const planeY = 0.18;
                    dragPlane.set(new THREE.Vector3(0, 1, 0), -planeY);
                    raycaster.ray.intersectPlane(dragPlane, dragPoint);
                    const world = fixtureToWorld(fixture);
                    resizeState = {{
                        handle: fixtureResizeHandle.userData.resizeHandle,
                        startPoint: dragPoint.clone(),
                        startWorld: {{ x: world.x, z: world.z }},
                        start: {{
                            w: Number(fixture.w || world.w),
                            d: Number(fixture.d || world.d),
                        }},
                    }};
                    return;
                }}

                const floorResizeHandle = pickFloorResizeHandle(event);
                if (floorResizeHandle) {{
                    claimCanvasDrag(event);
                    resizingFloor = true;
                    const planeY = 0.18;
                    dragPlane.set(new THREE.Vector3(0, 1, 0), -planeY);
                    raycaster.ray.intersectPlane(dragPlane, dragPoint);
                    const size = currentFloorSize();
                    resizeState = {{
                        handle: floorResizeHandle.userData.floorHandle,
                        startPoint: dragPoint.clone(),
                        start: {{
                            width: Number(size.width),
                            depth: Number(size.depth),
                            x: Number(size.x || 0),
                            z: Number(size.z || 0),
                        }},
                    }};
                    return;
                }}

                const itemMesh = pickRackItem(event);
                if (itemMesh) {{
                    const rack = racks.find(row => row.id === itemMesh.userData.rackId);
                    if (!rack) return;
                    selectedRackId = rack.id;
                    selectedFixtureId = "";
                    selectedRackItemKey = itemMesh.userData.rackItemKey || "";
                    buildRacks();
                    buildFixtures();
                    renderRack(rack);
                    return;
                }}

                const mesh = pickRack(event);
                if (mesh) {{
                    const rack = racks.find(row => row.id === mesh.userData.rackId);
                    if (!rack) return;
                    selectRack(rack.id);
                    if (rack.parentRackId) return;
                    if (rack.locked) return;
                    if (!event.shiftKey) return;
                    claimCanvasDrag(event);
                    draggingRack = rack;
                    const planeY = 0.18;
                    dragPlane.set(new THREE.Vector3(0, 1, 0), -planeY);
                    raycaster.ray.intersectPlane(dragPlane, dragPoint);
                    const world = rackToWorld(rack);
                    dragOffset.set(world.x - dragPoint.x, 0, world.z - dragPoint.z);
                    return;
                }}

                const fixtureMesh = pickFixture(event);
                if (!fixtureMesh) {{
                    selectedRackId = "";
                    selectedFixtureId = "";
                    selectedRackItemKey = "";
                    buildRacks();
                    buildFixtures();
                    renderRack(null);
                    return;
                }}
                const fixture = fixtures.find(row => row.id === fixtureMesh.userData.fixtureId);
                if (!fixture) return;
                selectFixture(fixture.id);
                if (fixture.locked) return;
                if (!event.shiftKey) return;
                claimCanvasDrag(event);
                draggingFixture = fixture;
                const planeY = 0.18;
                dragPlane.set(new THREE.Vector3(0, 1, 0), -planeY);
                raycaster.ray.intersectPlane(dragPlane, dragPoint);
                const world = fixtureToWorld(fixture);
                dragOffset.set(world.x - dragPoint.x, 0, world.z - dragPoint.z);
            }}, {{ capture: true }});

            function hasActiveCanvasDrag() {{
                return Boolean(draggingRack || draggingFixture || resizingRack || resizingFixture || resizingFloor);
            }}

            function handleCanvasPointerMove(event) {{
                if (!draggingRack && !draggingFixture && !resizingRack && !resizingFixture && !resizingFloor) {{
                    const handle = pickRackResizeHandle(event);
                    if (handle) {{
                        canvas.style.cursor = handle.userData.resizeHandle?.cursor || "nwse-resize";
                        return;
                    }}
                    const fixtureHandle = pickFixtureResizeHandle(event);
                    if (fixtureHandle) {{
                        canvas.style.cursor = fixtureHandle.userData.resizeHandle?.cursor || "nwse-resize";
                        return;
                    }}
                    const floorHandle = pickFloorResizeHandle(event);
                    if (floorHandle) {{
                        canvas.style.cursor = floorHandle.userData.floorHandle?.cursor || "nwse-resize";
                        return;
                    }}
                    canvas.style.cursor = pickRackItem(event) || pickRack(event) || pickFixture(event) ? (event.shiftKey ? "grab" : "pointer") : "default";
                    return;
                }}
                event.preventDefault();
                event.stopPropagation();
                if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
                pointerToNdc(event);
                raycaster.setFromCamera(pointer, camera);
                if (!raycaster.ray.intersectPlane(dragPlane, dragPoint)) return;
                if (resizingRack) {{
                    canvas.style.cursor = resizeState?.handle?.cursor || "nwse-resize";
                    resizeSelectedRackFromDrag();
                    return;
                }}
                if (resizingFixture) {{
                    canvas.style.cursor = resizeState?.handle?.cursor || "nwse-resize";
                    resizeSelectedFixtureFromDrag();
                    return;
                }}
                if (resizingFloor) {{
                    canvas.style.cursor = resizeState?.handle?.cursor || "nwse-resize";
                    resizeFloorFromDrag();
                    return;
                }}
                if (draggingRack) {{
                    canvas.style.cursor = "grabbing";
                    const next = snapPercentPosition(worldToRack(dragPoint.x + dragOffset.x, dragPoint.z + dragOffset.z), false);
                    draggingRack.x = next.x;
                    draggingRack.y = next.y;
                    const mesh = rackObjectById.get(draggingRack.id);
                    if (mesh) {{
                        const world = rackToWorld(draggingRack);
                        mesh.position.x = world.x;
                        mesh.position.z = world.z;
                    }}
                    renderRack(draggingRack);
                    return;
                }}
                if (draggingFixture) {{
                    const allowOutside = fixtureAllowsOutside(draggingFixture.type);
                    const next = snapPercentPosition(
                        worldToPercent(dragPoint.x + dragOffset.x, dragPoint.z + dragOffset.z, allowOutside),
                        allowOutside
                    );
                    draggingFixture.x = next.x;
                    draggingFixture.y = next.y;
                    const mesh = fixtureObjectById.get(draggingFixture.id);
                    if (mesh) {{
                        const world = fixtureToWorld(draggingFixture);
                        mesh.position.x = world.x;
                        mesh.position.z = world.z;
                    }}
                    renderFixture(draggingFixture);
                }}
            }}

            function finishCanvasDrag(event) {{
                const wasDraggingObject = hasActiveCanvasDrag();
                if (wasDraggingObject && event) {{
                    event.preventDefault();
                    event.stopPropagation();
                    if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
                }}
                if (resizingFloor) {{
                    syncFloorSizeInputs();
                    resizingFloor = null;
                    resizeState = null;
                    controls.enabled = true;
                    canvas.style.cursor = "default";
                    if (event) try {{ canvas.releasePointerCapture(event.pointerId); }} catch (error) {{}}
                    return;
                }}
                if (resizingFixture) {{
                    saveFixtures();
                    buildFixtures();
                    renderFixture(resizingFixture);
                    resizingFixture = null;
                    resizeState = null;
                    controls.enabled = true;
                    canvas.style.cursor = "default";
                    if (event) try {{ canvas.releasePointerCapture(event.pointerId); }} catch (error) {{}}
                    return;
                }}
                if (resizingRack) {{
                    saveLayout();
                    buildRacks();
                    renderRack(resizingRack);
                    resizingRack = null;
                    resizeState = null;
                    controls.enabled = true;
                    canvas.style.cursor = "default";
                    if (event) try {{ canvas.releasePointerCapture(event.pointerId); }} catch (error) {{}}
                    return;
                }}
                if (draggingRack) {{
                    saveLayout();
                    buildRacks();
                    draggingRack = null;
                    controls.enabled = true;
                    canvas.style.cursor = "default";
                    if (event) try {{ canvas.releasePointerCapture(event.pointerId); }} catch (error) {{}}
                    return;
                }}
                if (draggingFixture) {{
                    saveFixtures();
                    buildFixtures();
                    draggingFixture = null;
                    controls.enabled = true;
                    canvas.style.cursor = "default";
                    if (event) try {{ canvas.releasePointerCapture(event.pointerId); }} catch (error) {{}}
                }}
            }}

            canvas.addEventListener("pointermove", handleCanvasPointerMove);
            canvas.addEventListener("pointerup", finishCanvasDrag);
            canvas.addEventListener("pointercancel", finishCanvasDrag);

            document.getElementById("addRack").addEventListener("click", () => {{
                const now = Date.now();
                if (now - lastRackAddAt < 350) return;
                lastRackAddAt = now;
                racks = normalizeRackIds(racks);
                const id = nextRackId();
                const type = rackTypeSelect.value || "light";
                const levels = [2, 3].includes(Number(rackLevelSelect.value)) ? Number(rackLevelSelect.value) : 2;
                const roofOnly = rackBottomSelect.value === "roof";
                const bottomOpen = !roofOnly && rackBottomSelect.value === "open";
                const w = type === "heavy" ? 13.2 : 10.8;
                const h = type === "heavy" ? 9.2 : 8.4;
                const position = findOpenRackPosition(w, h);
                const rack = {{
                    id,
                    floor: activeFloor,
                    x: position.x,
                    y: position.y,
                    w,
                    h,
                    type,
                    levels,
                    bottomOpen,
                    roofOnly,
                    parentRackId: "",
                    status: "empty",
                    rotation: 0,
                    locked: false,
                    items: [],
                }};
                racks.push(rack);
                selectedRackId = rack.id;
                selectedFixtureId = "";
                selectedRackItemKey = "";
                saveLayout();
                rebuildScene();
            }});

            rackTypeSelect.addEventListener("change", () => {{
                const rack = selectedRack();
                if (!rack) return;
                rack.type = rackTypeSelect.value || "light";
                const allowedParts = partOptionsFor(rack);
                rack.items = (rack.items || []).map((item, index) => ({{
                    ...item,
                    part: allowedParts.includes(item.part) ? item.part : allowedParts[index % allowedParts.length],
                }}));
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            rackLevelSelect.addEventListener("change", () => {{
                const rack = selectedRack();
                if (!rack || rack.locked) return;
                rack.levels = [2, 3].includes(Number(rackLevelSelect.value)) ? Number(rackLevelSelect.value) : 2;
                rack.bottomOpen = Boolean(rack.bottomOpen);
                const allowedParts = partOptionsFor(rack);
                rack.items = (rack.items || []).map((item, index) => ({{
                    ...item,
                    part: allowedParts.includes(item.part) ? item.part : allowedParts[index % allowedParts.length],
                }}));
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            rackBottomSelect.addEventListener("change", () => {{
                const rack = selectedRack();
                if (!rack || rack.locked) return;
                rack.roofOnly = rackBottomSelect.value === "roof";
                rack.bottomOpen = !rack.roofOnly && rackBottomSelect.value === "open";
                const allowedParts = partOptionsFor(rack);
                rack.items = (rack.items || []).map((item, index) => ({{
                    ...item,
                    part: allowedParts.includes(item.part) ? item.part : allowedParts[index % allowedParts.length],
                }}));
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            stackRackButton.addEventListener("click", () => {{
                const rack = selectedRack();
                const target = racks.find(row => row.id === rackStackTargetSelect.value);
                if (!rack || !target || rack.locked || rack.id === target.id || rackHasAncestor(target, rack.id)) return;
                rack.parentRackId = target.id;
                rack.x = Number(target.x || rack.x || 50);
                rack.y = Number(target.y || rack.y || 50);
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            unstackRackButton.addEventListener("click", () => {{
                const rack = selectedRack();
                if (!rack || rack.locked) return;
                const position = rackRenderPosition(rack);
                rack.parentRackId = "";
                rack.x = position.x;
                rack.y = position.y;
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            rackStackTargetSelect.addEventListener("change", () => {{
                renderStackTargetSelect(rackStackTargetSelect.value);
            }});

            document.getElementById("deleteRack").addEventListener("click", () => {{
                deleteSelectedRack();
            }});

            document.getElementById("addFixture").addEventListener("click", () => {{
                const type = fixtureTypeSelect.value || "entrance";
                const template = fixtureDefaults[type] || fixtureDefaults.entrance;
                const id = `F-${{Date.now().toString(36).slice(-6)}}`;
                const fixture = normalizeFixture({{
                    ...template,
                    id,
                    type,
                    label: template.label,
                    floor: activeFloor,
                    x: 50,
                    y: fixtureAllowsOutside(type) ? -6 : 50,
                    rotation: 0,
                    locked: false,
                }});
                fixtures.push(fixture);
                selectedRackId = "";
                selectedFixtureId = fixture.id;
                selectedRackItemKey = "";
                saveFixtures();
                buildRacks();
                buildFixtures();
                renderFixture(fixture);
            }});

            rotateFixtureButton.addEventListener("click", () => {{
                const fixture = selectedFixture();
                if (!fixture || fixture.locked) return;
                fixture.rotation = (Number(fixture.rotation || 0) + 90) % 360;
                saveFixtures();
                buildFixtures();
                renderFixture(fixture);
            }});

            lockFixtureButton.addEventListener("click", () => {{
                const fixture = selectedFixture();
                if (!fixture) return;
                fixture.locked = !fixture.locked;
                saveFixtures();
                buildFixtures();
                renderFixture(fixture);
            }});

            deleteFixtureButton.addEventListener("click", () => {{
                deleteSelectedFixture();
            }});

            rotateButton.addEventListener("click", () => {{
                const rack = selectedRack();
                if (!rack || rack.locked) return;
                rack.rotation = (Number(rack.rotation || 0) + 90) % 180;
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            lockButton.addEventListener("click", () => {{
                const rack = selectedRack();
                if (!rack) return;
                rack.locked = !rack.locked;
                saveLayout();
                buildRacks();
                renderRack(rack);
            }});

            document.getElementById("resetRack").addEventListener("click", () => {{
                racks = [];
                selectedRackId = "";
                selectedRackItemKey = "";
                saveLayout();
                rebuildScene();
            }});

            document.getElementById("fitRack").addEventListener("click", () => {{
                racks = defaultLayout(activeFloor);
                selectedRackId = racks[0]?.id || "";
                selectedFixtureId = "";
                selectedRackItemKey = "";
                saveLayout();
                rebuildScene();
            }});

            labelToggleButton.addEventListener("change", () => {{
                showFixtureLabels = labelToggleButton.type === "checkbox" ? labelToggleButton.checked : !showFixtureLabels;
                localStorage.setItem(fixtureLabelStorageKey, showFixtureLabels ? "visible" : "hidden");
                syncFixtureLabelButton();
                buildRacks();
                buildFixtures();
            }});

            applyFloorSizeButton.addEventListener("click", () => {{
                applyFloorSizeFromInputs();
            }});

            resetFloorSizeButton.addEventListener("click", () => {{
                resetFloorSizeToBase();
            }});

            [floorWidthInput, floorDepthInput].forEach(input => {{
                input.addEventListener("keydown", event => {{
                    if (event.key !== "Enter") return;
                    event.preventDefault();
                    applyFloorSizeFromInputs();
                }});
            }});

            document.getElementById("addLoad").addEventListener("click", () => {{
                addLoadFromInputs();
            }});

            moveFixtureToRackButton.addEventListener("click", () => {{
                moveSelectedFixtureToRack();
            }});

            targetRackSelect.addEventListener("change", () => {{
                const fixture = selectedFixture();
                if (isLoadFixture(fixture)) renderTargetRackPartSelect();
                updateFixtureButtons();
            }});

            targetRackPartSelect.addEventListener("change", () => {{
                updateFixtureButtons();
            }});

            targetFloorSelect.addEventListener("change", () => {{
                updateFixtureButtons();
            }});

            moveSelectionFloorButton.addEventListener("click", () => {{
                moveSelectedFixtureToFloor();
            }});

            loadShapeSelect.addEventListener("change", () => {{
                syncStackInput();
            }});

            function nudgeSelectedRack(direction, step = 2.2) {{
                const rack = selectedRack();
                if (!rack || rack.locked) return false;
                if (direction === "left") rack.x = clamp(Number(rack.x || 50) - step, 1, 96);
                if (direction === "right") rack.x = clamp(Number(rack.x || 50) + step, 1, 96);
                if (direction === "up") rack.y = clamp(Number(rack.y || 50) - step, 2, 94);
                if (direction === "down") rack.y = clamp(Number(rack.y || 50) + step, 2, 94);
                saveLayout();
                buildRacks();
                renderRack(rack);
                return true;
            }}

            function nudgeSelectedFixture(direction, step = 2.2) {{
                const fixture = selectedFixture();
                if (!fixture || fixture.locked) return false;
                const allowOutside = fixtureAllowsOutside(fixture.type);
                const current = {{ x: Number(fixture.x || 50), y: Number(fixture.y || 50) }};
                if (direction === "left") current.x -= step;
                if (direction === "right") current.x += step;
                if (direction === "up") current.y -= step;
                if (direction === "down") current.y += step;
                const min = allowOutside ? -24 : 1;
                const max = allowOutside ? 124 : 99;
                fixture.x = clamp(current.x, min, max);
                fixture.y = clamp(current.y, min, max);
                saveFixtures();
                buildFixtures();
                renderFixture(fixture);
                return true;
            }}

            document.querySelectorAll("[data-nudge]").forEach(button => {{
                button.addEventListener("click", () => {{
                    if (selectedRackId && nudgeSelectedRack(button.dataset.nudge)) return;
                    if (selectedFixtureId) nudgeSelectedFixture(button.dataset.nudge);
                }});
            }});

            document.querySelectorAll("[data-zoom]").forEach(button => {{
                button.addEventListener("click", () => {{
                    setZoom(Number(button.dataset.zoom));
                }});
            }});

            document.querySelectorAll("[data-pan]").forEach(button => {{
                button.addEventListener("click", () => {{
                    panView(button.dataset.pan);
                }});
            }});

            window.addEventListener("keydown", event => {{
                const targetTag = event.target?.tagName?.toLowerCase();
                if (["input", "select", "textarea"].includes(targetTag)) return;
                const arrowPan = {{
                    ArrowUp: "up",
                    ArrowDown: "down",
                    ArrowLeft: "left",
                    ArrowRight: "right",
                }};
                if (arrowPan[event.key]) {{
                    event.preventDefault();
                    if (selectedRackId && selectedRack()?.locked) return;
                    if (selectedRackId && nudgeSelectedRack(arrowPan[event.key], event.shiftKey ? 2.2 : 0.8)) return;
                    if (selectedFixtureId && nudgeSelectedFixture(arrowPan[event.key], event.shiftKey ? 2.2 : 0.8)) return;
                    panView(arrowPan[event.key]);
                    return;
                }}
                if (event.key !== "Delete") return;
                if (!selectedRackId && !selectedFixtureId) return;
                event.preventDefault();
                if (selectedRackId) deleteSelectedRack();
                if (selectedFixtureId) deleteSelectedFixture();
            }});

            canvas.addEventListener("wheel", event => {{
                event.preventDefault();
                if (draggingRack || draggingFixture || resizingRack || resizingFixture || resizingFloor) return;
                const direction = event.deltaY < 0 ? 1 : -1;
                setZoom(zoomLevel + direction * zoomStep);
            }}, {{ passive: false }});

            function captureWarehousePrintImage() {{
                const width = 3508;
                const height = 2480;
                const previousPixelRatio = renderer.getPixelRatio();
                const previousBackground = scene.background;
                const previousFog = scene.fog;

                controls.update();
                renderer.setPixelRatio(1);
                renderer.setSize(width, height, false);
                camera.aspect = width / height;
                camera.updateProjectionMatrix();
                scene.background = new THREE.Color(0xffffff);
                scene.fog = null;
                renderer.render(scene, camera);
                const imageUrl = canvas.toDataURL("image/png");

                scene.background = previousBackground;
                scene.fog = previousFog;
                renderer.setPixelRatio(previousPixelRatio);
                resizeRenderer();
                renderer.render(scene, camera);
                return imageUrl;
            }}

            function printWarehouseModel() {{
                const printWindow = window.open("", "_blank", "width=1180,height=840");
                if (!printWindow) {{
                    window.print();
                    return;
                }}
                const imageUrl = captureWarehousePrintImage();
                const printedAt = new Date().toLocaleString("ko-KR");
                printWindow.document.write(`
                    <!doctype html>
                    <html lang="ko">
                    <head>
                        <meta charset="utf-8">
                        <title>${{activeBuilding}} ${{activeFloor}} 3D 창고 모델</title>
                        <style>
                            @page {{ size: A4 landscape; margin: 8mm; }}
                            * {{ box-sizing: border-box; }}
                            html,
                            body {{
                                background: #ffffff;
                                color: #10201d;
                                font-family: "Malgun Gothic", Arial, sans-serif;
                                height: 194mm;
                                margin: 0;
                                overflow: hidden;
                                width: 281mm;
                            }}
                            .print-shell {{
                                display: grid;
                                gap: 3mm;
                                grid-template-rows: auto minmax(0, 1fr) auto;
                                height: 194mm;
                                overflow: hidden;
                            }}
                            header {{
                                align-items: flex-end;
                                border-bottom: 1px solid #9db5ae;
                                display: flex;
                                justify-content: space-between;
                                padding-bottom: 2mm;
                            }}
                            h1 {{
                                font-size: 16px;
                                margin: 0;
                            }}
                            p {{
                                color: #50645f;
                                font-size: 9px;
                                margin: 1mm 0 0;
                            }}
                            img {{
                                display: block;
                                height: 100%;
                                max-height: 165mm;
                                object-fit: contain;
                                width: 100%;
                            }}
                            .meta {{
                                color: #50645f;
                                display: flex;
                                font-size: 9px;
                                justify-content: space-between;
                            }}
                        </style>
                    </head>
                    <body>
                        <main class="print-shell">
                            <header>
                                <div>
                                    <h1>${{activeBuilding}} ${{activeFloor}} 3D 창고 모델</h1>
                                    <p>랙/시설물 배치 출력</p>
                                </div>
                                <p>${{printedAt}}</p>
                            </header>
                            <img src="${{imageUrl}}" alt="3D 창고 모델">
                            <div class="meta">
                                <span>층: ${{activeFloor}}</span>
                                <span>랙 ${{racks.length}}개 · 시설물 ${{fixtures.length}}개</span>
                            </div>
                        </main>
                        <script>
                            window.addEventListener("load", () => {{
                                setTimeout(() => {{
                                    window.focus();
                                    window.print();
                                }}, 120);
                            }});
                        <\\/script>
                    </body>
                    </html>
                `);
                printWindow.document.close();
            }}

            printSceneButton?.addEventListener("click", printWarehouseModel);

            document.querySelectorAll(".floor-chip").forEach(button => {{
                button.addEventListener("click", () => {{
                    saveLayout();
                    saveFixtures();
                    activeFloor = button.dataset.floor;
                    racks = loadLayout(activeFloor);
                    fixtures = loadFixtures(activeFloor);
                    selectedRackId = racks[0]?.id || "";
                    selectedFixtureId = "";
                    selectedRackItemKey = "";
                    rebuildScene();
                }});
            }});

            function animate() {{
                controls.update();
                renderer.render(scene, camera);
                requestAnimationFrame(animate);
            }}

            fixtures = loadFixtures(activeFloor);
            renderItemSelect();
            syncStackInput();
            syncFixtureLabelButton();
            resizeRenderer();
            setZoom(100);
            rebuildScene();
            canvas.dataset.ready = "true";
            animate();
            window.addEventListener("resize", resizeRenderer);
        </script>
        <script>
            setTimeout(() => {{
                const canvas = document.getElementById("warehouseCanvas");
                const error = document.getElementById("modelError");
                if (canvas && error && canvas.dataset.ready !== "true") {{
                    error.style.display = "flex";
                }}
            }}, 2600);
        </script>
        <script nomodule>
            document.getElementById("modelError").style.display = "flex";
        </script>
    </body>
    </html>
    """


def inject_warehouse3d_css() -> None:
    st.markdown(
        """
        <style>
        .warehouse3d-title {
            color: #ffffff;
            font-size: 1.34rem;
            font-weight: 950;
            margin: 0.1rem 0 0.75rem;
        }
        .warehouse3d-kpi-grid {
            display: grid;
            gap: 0.48rem;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            margin: 0.55rem 0 0.72rem;
        }
        .warehouse3d-kpi {
            background: rgba(7, 58, 52, 0.68);
            border: 1px solid rgba(87, 178, 165, 0.25);
            border-radius: 8px;
            min-height: 70px;
            padding: 0.68rem;
        }
        .warehouse3d-kpi span {
            color: #b2d5cd;
            display: block;
            font-size: 0.72rem;
            font-weight: 900;
            margin-bottom: 0.28rem;
        }
        .warehouse3d-kpi strong {
            color: #ffffff;
            display: block;
            font-size: 0.94rem;
            font-weight: 950;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        @media (max-width: 1100px) {
            .warehouse3d-kpi-grid {
                grid-template-columns: repeat(3, minmax(0, 1fr));
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
