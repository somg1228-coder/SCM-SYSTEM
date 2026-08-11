from __future__ import annotations

import base64
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import tempfile
import threading
from html import escape
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import func, select

from components.lazy_tabs import lazy_tab_selector
from backend.perf import perf_span

try:
    from backend.config import config_text_value
    from backend.database import (
        DATABASE_URL,
        SessionLocal,
        init_db,
        is_postgresql_url,
        record_save_failure,
        record_save_success,
        writable_runtime_data_dir,
    )
    from backend.models import InventoryDaily, WarehouseInventoryPosition, WarehouseLayout, WarehouseRack
    from backend import services, supabase_store
except (ModuleNotFoundError, RuntimeError) as exc:
    DATABASE_URL = ""
    SessionLocal = None
    config_text_value = None
    init_db = None
    is_postgresql_url = None
    record_save_failure = None
    record_save_success = None
    writable_runtime_data_dir = None
    InventoryDaily = None
    WarehouseInventoryPosition = None
    WarehouseLayout = None
    WarehouseRack = None
    services = None
    supabase_store = None
    WAREHOUSE_IMPORT_ERROR = str(exc)
else:
    WAREHOUSE_IMPORT_ERROR = ""


DEFAULT_LOGIN_DRAWING_PATH = Path.home() / "Downloads" / "[FAC-001~005] 시설 도면_Rev. 1_260305.pdf"
LOGIN_FLOORS = ["1층", "2층", "3층", "4층"]
TWO_FLOORS = ["1층", "2층"]
ONE_FLOOR = ["1층"]

LOCATIONS = {
    "로긴": {
        "floors": LOGIN_FLOORS,
        "default_floor": "1층",
        "description": "1층부터 4층까지 랙 배치/적재 관리",
        "default_drawing": DEFAULT_LOGIN_DRAWING_PATH,
    },
    "포장부서": {
        "floors": TWO_FLOORS,
        "default_floor": "1층",
        "description": "포장부서 1층/2층 작업 및 포장재 랙 관리",
        "default_drawing": None,
    },
    "밑창고1": {
        "floors": ONE_FLOOR,
        "default_floor": "1층",
        "description": "밑창고1 적재 및 피킹 랙 관리",
        "default_drawing": None,
    },
    "옆창고2": {
        "floors": ONE_FLOOR,
        "default_floor": "1층",
        "description": "옆창고2 적재 및 예비 랙 관리",
        "default_drawing": None,
    },
}


LEGACY_LOCATION_MAP = {"밑창고1": "창고1", "옆창고2": "창고2"}
CANONICAL_LOCATION_BY_LEGACY = {legacy: current for current, legacy in LEGACY_LOCATION_MAP.items()}
WAREHOUSE_LAYOUT_STORE_NAME = "warehouse3d_layouts.json"
THREE_VENDOR_DIR = Path(__file__).resolve().parents[1] / "assets" / "vendor" / "three-0.160.0"
WAREHOUSE3D_COMPONENT_DIR = Path(__file__).resolve().parents[1] / "components" / "warehouse3d_component"
WAREHOUSE_LAYOUT_API_PORTS = range(8765, 8775)
_WAREHOUSE_LAYOUT_API_SERVER = None
_WAREHOUSE_LAYOUT_API_PORT = None
_WAREHOUSE_LAYOUT_API_LOCK = threading.RLock()
warehouse3d_scene_component = components.declare_component(
    "warehouse3d_scene",
    path=str(WAREHOUSE3D_COMPONENT_DIR),
)


@st.cache_data(show_spinner=False)
def warehouse3d_vendor_sources() -> dict:
    try:
        three_source = (THREE_VENDOR_DIR / "three.module.js").read_text(encoding="utf-8")
        controls_source = (THREE_VENDOR_DIR / "OrbitControls.js").read_text(encoding="utf-8")
    except OSError:
        return {"three": "", "controls": ""}
    return {"three": three_source, "controls": controls_source}


def warehouse_layout_store_path() -> Path:
    project_data_dir = Path(__file__).resolve().parents[1] / "data"
    try:
        project_data_dir.mkdir(parents=True, exist_ok=True)
        return project_data_dir / WAREHOUSE_LAYOUT_STORE_NAME
    except OSError:
        pass

    if writable_runtime_data_dir is not None:
        try:
            data_dir = writable_runtime_data_dir()
        except Exception:
            data_dir = Path(tempfile.gettempdir()) / "scm_portal_data"
    else:
        data_dir = Path(tempfile.gettempdir()) / "scm_portal_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / WAREHOUSE_LAYOUT_STORE_NAME


def empty_warehouse_layout_store() -> dict:
    return {"version": 1, "locations": {}}


def write_warehouse_layout_log(message: str) -> None:
    try:
        log_path = warehouse_layout_store_path().parent / "warehouse3d_layout_save.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")
    except Exception:
        pass


def warehouse_layout_has_data(payload: dict | None) -> bool:
    if not isinstance(payload, dict):
        return False
    locations = payload.get("locations")
    if not isinstance(locations, dict):
        return False
    for floors in locations.values():
        if not isinstance(floors, dict):
            continue
        for floor_data in floors.values():
            if not isinstance(floor_data, dict):
                continue
            if floor_data.get("racks") or floor_data.get("fixtures") or floor_data.get("floor_size"):
                return True
    return False


def canonical_warehouse_building(building: object) -> str:
    name = str(building or "").strip()
    if name in LOCATIONS:
        return name
    return CANONICAL_LOCATION_BY_LEGACY.get(name, name)


def load_local_warehouse_layout_store() -> dict:
    path = warehouse_layout_store_path()
    if not path.exists():
        return empty_warehouse_layout_store()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_warehouse_layout_store()
    if not isinstance(payload, dict):
        return empty_warehouse_layout_store()
    locations = payload.get("locations")
    if not isinstance(locations, dict):
        payload["locations"] = {}
    payload["version"] = int(payload.get("version") or 1)
    return payload


def load_database_warehouse_layout_store() -> dict:
    if SessionLocal is None or WarehouseLayout is None or init_db is None:
        return empty_warehouse_layout_store()
    try:
        init_db(ensure_schema=False)
        with SessionLocal() as db:
            rows = (
                db.execute(
                    select(WarehouseLayout).where(WarehouseLayout.is_active.is_(True))
                )
                .scalars()
                .all()
            )
    except Exception as exc:
        write_warehouse_layout_log(f"SQLAlchemy layout load failed: {exc}")
        return empty_warehouse_layout_store()

    locations: dict[str, dict] = {}
    for row in rows:
        building = canonical_warehouse_building(row.building)
        if building not in LOCATIONS or row.floor not in LOCATIONS[building]["floors"]:
            continue
        if not isinstance(row.layout_data, dict):
            continue
        locations.setdefault(building, {})[row.floor] = row.layout_data
    return {"version": 1, "locations": locations}


def app_database_is_postgresql() -> bool:
    return bool(is_postgresql_url is not None and DATABASE_URL and is_postgresql_url(DATABASE_URL))


def stable_warehouse_id(*parts: object, prefix: str = "wh") -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def rack_shelf_no(item: dict, fallback: int) -> int:
    part = str(item.get("part") or item.get("shelf") or item.get("shelf_no") or "").strip()
    digits = "".join(ch for ch in part if ch.isdigit())
    if digits:
        return max(1, safe_int(digits, fallback))
    return max(1, fallback)


def ensure_warehouse_detail_tables(db) -> None:
    if WarehouseRack is None or WarehouseInventoryPosition is None:
        return
    if not app_database_is_postgresql():
        return
    bind = db.get_bind()
    WarehouseRack.__table__.create(bind=bind, checkfirst=True)
    WarehouseInventoryPosition.__table__.create(bind=bind, checkfirst=True)


def sync_warehouse_layout_detail_tables(db, layout_rows: list) -> None:
    if WarehouseRack is None or WarehouseInventoryPosition is None:
        return
    if not app_database_is_postgresql():
        return
    ensure_warehouse_detail_tables(db)
    for layout in layout_rows:
        floor_data = layout.layout_data if isinstance(layout.layout_data, dict) else {}
        racks = floor_data.get("racks") if isinstance(floor_data.get("racks"), list) else []
        current_rack_codes: set[str] = set()
        existing_racks = {
            rack.rack_code: rack
            for rack in db.execute(
                select(WarehouseRack).where(WarehouseRack.layout_id == layout.id)
            ).scalars()
        }
        for index, rack_data in enumerate(racks):
            if not isinstance(rack_data, dict):
                continue
            rack_code = str(rack_data.get("id") or rack_data.get("rack_code") or f"R-{index + 1:03d}").strip()
            if not rack_code:
                continue
            current_rack_codes.add(rack_code)
            rack = existing_racks.get(rack_code)
            if rack is None:
                rack = WarehouseRack(
                    id=stable_warehouse_id(layout.id, rack_code, prefix="rack"),
                    layout_id=layout.id,
                    rack_code=rack_code,
                )
                db.add(rack)
            rack.rack_name = str(rack_data.get("name") or rack_data.get("label") or rack_code).strip()
            rack.x = safe_float(rack_data.get("x"))
            rack.y = safe_float(rack_data.get("y"))
            rack.z = safe_float(rack_data.get("z"))
            rack.rotation = safe_float(rack_data.get("rotation"))
            rack.width = safe_float(rack_data.get("width", rack_data.get("w")))
            rack.depth = safe_float(rack_data.get("depth", rack_data.get("h")))
            rack.height = safe_float(rack_data.get("height"), safe_float(rack_data.get("levels"), 1.0))
            rack.shelf_count = max(1, safe_int(rack_data.get("shelf_count", rack_data.get("levels", rack_data.get("level_count"))), 1))
            rack.rack_type = str(rack_data.get("rack_type") or rack_data.get("type") or "").strip()
            rack.sort_order = index
            rack.rack_data = rack_data
            rack.updated_at = datetime.utcnow()
            db.flush()

            items = rack_data.get("items") if isinstance(rack_data.get("items"), list) else []
            aggregated: dict[tuple[int, str, str], dict] = {}
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                shelf_no = rack_shelf_no(item, item_index + 1)
                sku = str(item.get("sku") or item.get("barcode") or item.get("product_code") or "").strip()
                item_name = str(item.get("item_name") or item.get("product_name") or item.get("name") or "").strip()
                if not sku and not item_name:
                    continue
                key = (shelf_no, sku, item_name)
                quantity = safe_int(item.get("quantity", item.get("qty", item.get("stock"))), 0)
                if key not in aggregated:
                    aggregated[key] = {"quantity": 0, "sort_order": item_index, "position_data": dict(item)}
                aggregated[key]["quantity"] += quantity

            current_position_ids: set[str] = set()
            existing_positions = {
                position.id: position
                for position in db.execute(
                    select(WarehouseInventoryPosition).where(WarehouseInventoryPosition.rack_id == rack.id)
                ).scalars()
            }
            for (shelf_no, sku, item_name), position_data in aggregated.items():
                position_id = stable_warehouse_id(rack.id, shelf_no, sku, item_name, prefix="pos")
                current_position_ids.add(position_id)
                position = existing_positions.get(position_id)
                if position is None:
                    position = WarehouseInventoryPosition(
                        id=position_id,
                        rack_id=rack.id,
                        shelf_no=shelf_no,
                        sku=sku,
                        item_name=item_name,
                    )
                    db.add(position)
                position.quantity = max(0, safe_int(position_data["quantity"], 0))
                position.sort_order = safe_int(position_data["sort_order"], 0)
                position.position_data = position_data["position_data"]
                position.updated_at = datetime.utcnow()
            for position in existing_positions.values():
                if position.id not in current_position_ids:
                    db.delete(position)

        for rack_code, rack in existing_racks.items():
            if rack_code in current_rack_codes:
                continue
            for position in db.execute(
                select(WarehouseInventoryPosition).where(WarehouseInventoryPosition.rack_id == rack.id)
            ).scalars():
                db.delete(position)
            db.delete(rack)


def save_database_warehouse_layout_store(payload: dict) -> int:
    if SessionLocal is None or WarehouseLayout is None or init_db is None:
        return 0
    locations = payload.get("locations") if isinstance(payload, dict) else None
    if not isinstance(locations, dict):
        return 0

    saved = 0
    try:
        init_db(ensure_schema=False)
        with SessionLocal() as db:
            saved_keys: list[tuple[str, str]] = []
            for building, floors in locations.items():
                building = canonical_warehouse_building(building)
                if building not in LOCATIONS or not isinstance(floors, dict):
                    continue
                for floor, floor_data in floors.items():
                    if floor not in LOCATIONS[building]["floors"] or not isinstance(floor_data, dict):
                        continue
                    if not (floor_data.get("racks") or floor_data.get("fixtures") or floor_data.get("floor_size")):
                        continue
                    existing = db.execute(
                        select(WarehouseLayout).where(
                            WarehouseLayout.building == building,
                            WarehouseLayout.floor == floor,
                        )
                    ).scalar_one_or_none()
                    if existing is None:
                        db.add(
                            WarehouseLayout(
                                building=building,
                                floor=floor,
                                layout_data=floor_data,
                                is_active=True,
                            )
                        )
                    else:
                        existing.layout_data = floor_data
                        existing.is_active = True
                        existing.updated_at = datetime.utcnow()
                    saved_keys.append((building, floor))
                    saved += 1
            db.flush()
            if saved_keys:
                layout_rows = (
                    db.execute(
                        select(WarehouseLayout).where(
                            WarehouseLayout.building.in_([key[0] for key in saved_keys]),
                            WarehouseLayout.floor.in_([key[1] for key in saved_keys]),
                            WarehouseLayout.is_active.is_(True),
                        )
                    )
                    .scalars()
                    .all()
                )
                sync_warehouse_layout_detail_tables(db, layout_rows)
            db.commit()
            verified = (
                db.execute(select(WarehouseLayout).where(WarehouseLayout.is_active.is_(True)))
                .scalars()
                .all()
            )
            if saved and not verified:
                write_warehouse_layout_log("SQLAlchemy layout save verification failed: no active rows after commit.")
                if record_save_failure is not None:
                    record_save_failure("warehouse3d layout verification")
                return 0
            if saved and record_save_success is not None:
                record_save_success("warehouse3d layout")
    except Exception as exc:
        write_warehouse_layout_log(f"SQLAlchemy layout save failed: {exc}")
        if record_save_failure is not None:
            record_save_failure("warehouse3d layout", exc)
        return 0
    return saved


def load_warehouse_layout_store() -> dict:
    db_payload = load_database_warehouse_layout_store()

    if app_database_is_postgresql():
        if warehouse_layout_has_data(db_payload):
            return db_payload

        local_payload = load_local_warehouse_layout_store()
        if warehouse_layout_has_data(local_payload):
            saved = save_database_warehouse_layout_store(local_payload)
            if saved:
                write_warehouse_layout_log(f"Migrated legacy local warehouse layout JSON to Supabase: {saved} rows.")
                return load_database_warehouse_layout_store()
        return db_payload

    local_payload = load_local_warehouse_layout_store()

    if supabase_store is None:
        return db_payload if warehouse_layout_has_data(db_payload) else local_payload

    if warehouse_layout_has_data(db_payload):
        return db_payload

    try:
        if not supabase_store.is_enabled():
            return local_payload
        remote_payload = supabase_store.load_warehouse_layout_store()
    except Exception:
        return local_payload

    if warehouse_layout_has_data(local_payload):
        merged = merge_warehouse_layout_store(remote_payload, local_payload)
        save_database_warehouse_layout_store(merged)
        try:
            supabase_store.save_warehouse_layout_store(merged)
        except Exception:
            pass
        return merged

    if warehouse_layout_has_data(remote_payload):
        save_database_warehouse_layout_store(remote_payload)
        return remote_payload
    return local_payload


def backup_warehouse_layout_store(path: Path) -> None:
    if not path.exists() or path.stat().st_size <= 0:
        return
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not warehouse_layout_has_data(current):
        return
    backup_path = path.with_name("warehouse3d_layouts.previous.json")
    backup_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_warehouse_layout_store(existing: dict, incoming: dict) -> dict:
    merged = empty_warehouse_layout_store()
    existing_locations = existing.get("locations") if isinstance(existing, dict) else None
    if isinstance(existing_locations, dict):
        for building, floors in existing_locations.items():
            building = canonical_warehouse_building(building)
            if building in LOCATIONS and isinstance(floors, dict):
                merged["locations"].setdefault(building, {}).update(floors)
    incoming_locations = incoming.get("locations") if isinstance(incoming, dict) else None
    if not isinstance(incoming_locations, dict):
        return merged

    for building, floors in incoming_locations.items():
        building = canonical_warehouse_building(building)
        if building not in LOCATIONS or not isinstance(floors, dict):
            continue
        merged["locations"].setdefault(building, {})
        for floor, floor_data in floors.items():
            if floor not in LOCATIONS[building]["floors"] or not isinstance(floor_data, dict):
                continue
            clean_floor_data = {}
            if isinstance(floor_data.get("racks"), list):
                clean_floor_data["racks"] = floor_data["racks"]
            if isinstance(floor_data.get("fixtures"), list):
                clean_floor_data["fixtures"] = floor_data["fixtures"]
            if isinstance(floor_data.get("floor_size"), dict):
                clean_floor_data["floor_size"] = floor_data["floor_size"]
            if clean_floor_data:
                merged["locations"][building][floor] = clean_floor_data
    return merged


def save_warehouse_layout_store(payload: dict) -> Path:
    path = warehouse_layout_store_path()
    existing = load_database_warehouse_layout_store() if app_database_is_postgresql() else load_local_warehouse_layout_store()
    store = empty_warehouse_layout_store()
    if isinstance(payload, dict):
        locations = payload.get("locations")
        if isinstance(locations, dict):
            store["locations"] = locations
        else:
            store["locations"] = {
                key: value
                for key, value in payload.items()
                if key in LOCATIONS and isinstance(value, dict)
            }

    if not warehouse_layout_has_data(store) and warehouse_layout_has_data(existing):
        return path

    store = merge_warehouse_layout_store(existing, store)
    saved_db_rows = save_database_warehouse_layout_store(store)
    if saved_db_rows:
        write_warehouse_layout_log(f"Saved layout to SQLAlchemy DB: {saved_db_rows} rows.")
        return path
    if app_database_is_postgresql() and warehouse_layout_has_data(store):
        message = "Supabase PostgreSQL mode is enabled, but warehouse_layouts could not be saved."
        write_warehouse_layout_log(message)
        if record_save_failure is not None:
            record_save_failure("warehouse3d layout", RuntimeError(message))
        raise RuntimeError(message)

    backup_warehouse_layout_store(path)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    if supabase_store is not None:
        try:
            if supabase_store.is_enabled():
                supabase_store.save_warehouse_layout_store(store)
                write_warehouse_layout_log("Saved layout to Supabase.")
        except Exception as exc:
            write_warehouse_layout_log(f"Supabase layout save failed: {exc}")
    return path


class WarehouseLayoutApiHandler(BaseHTTPRequestHandler):
    server_version = "SCMWarehouseLayout/1.0"

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.split("?", 1)[0] != "/warehouse3d-layout":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        with _WAREHOUSE_LAYOUT_API_LOCK:
            payload = load_warehouse_layout_store()
        self._send_json(200, {"ok": True, "layout": payload})

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/warehouse3d-layout":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw_body = self.rfile.read(min(length, 8_000_000))
            payload = json.loads(raw_body.decode("utf-8-sig") if raw_body else "{}")
            with _WAREHOUSE_LAYOUT_API_LOCK:
                save_warehouse_layout_store(payload)
                saved = load_warehouse_layout_store()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, RuntimeError) as exc:
            write_warehouse_layout_log(f"POST save failed: {exc}")
            self._send_json(500 if isinstance(exc, RuntimeError) else 400, {"ok": False, "error": str(exc)})
            return
        write_warehouse_layout_log("POST save succeeded.")
        self._send_json(200, {"ok": True, "layout": saved})

    def log_message(self, format: str, *args) -> None:
        return


def ensure_warehouse_layout_api_server() -> int | None:
    global _WAREHOUSE_LAYOUT_API_PORT
    global _WAREHOUSE_LAYOUT_API_SERVER

    if _WAREHOUSE_LAYOUT_API_SERVER is not None and _WAREHOUSE_LAYOUT_API_PORT is not None:
        return _WAREHOUSE_LAYOUT_API_PORT

    for host in ("127.0.0.1", "localhost", "0.0.0.0"):
        for port in WAREHOUSE_LAYOUT_API_PORTS:
            try:
                server = ThreadingHTTPServer((host, port), WarehouseLayoutApiHandler)
            except OSError:
                continue
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            _WAREHOUSE_LAYOUT_API_SERVER = server
            _WAREHOUSE_LAYOUT_API_PORT = port
            write_warehouse_layout_log(f"Layout API started at http://{host}:{port}.")
            return port
    write_warehouse_layout_log("Layout API could not start.")
    return None


def render_warehouse_layout_sync_tools() -> dict:
    return load_warehouse_layout_store()


def handle_warehouse3d_layout_save_request(save_request: dict | None) -> bool:
    if not isinstance(save_request, dict):
        return False
    if save_request.get("action") != "save_layout":
        return False

    request_id = str(save_request.get("request_id") or "").strip()
    if not request_id:
        return False
    if st.session_state.get("warehouse3d_last_save_request_id") == request_id:
        return False

    st.session_state["warehouse3d_last_save_request_id"] = request_id
    payload = save_request.get("payload")
    if not isinstance(payload, dict) or not warehouse_layout_has_data(payload):
        st.session_state["warehouse3d_save_notice"] = ("error", "저장할 3D 창고 배치 데이터가 없습니다.")
        return True

    try:
        save_warehouse_layout_store(payload)
    except Exception as exc:
        write_warehouse_layout_log(f"Streamlit component save failed: {exc}")
        st.session_state["warehouse3d_save_notice"] = ("error", f"서버 Supabase 저장 실패: {exc}")
        return True

    st.session_state["warehouse3d_save_notice"] = ("success", "서버 Supabase 저장 완료")
    return True


def warehouse_layout_supabase_browser_config() -> dict:
    if config_text_value is None:
        return {"enabled": False, "url": "", "key": "", "postgresql": app_database_is_postgresql()}
    url, _ = config_text_value("SUPABASE_URL")
    key, _ = config_text_value("SUPABASE_KEY")
    url = url.rstrip("/")
    return {"enabled": bool(url and key), "url": url, "key": key, "postgresql": app_database_is_postgresql()}

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
    with perf_span("warehouse3d.inject_css"):
        inject_warehouse3d_css()
    st.markdown('<div class="warehouse3d-title">3D 창고관리</div>', unsafe_allow_html=True)

    with perf_span("warehouse3d.available_check"):
        available = warehouse_available()
    if not available:
        st.error(WAREHOUSE_IMPORT_ERROR or "창고관리 DB를 초기화하지 못했습니다.")
        return

    with perf_span("warehouse3d.fetch_inventory"):
        inventory_rows, work_date = fetch_latest_warehouse_inventory()
    building_col, _ = st.columns([1.05, 2.3], gap="small")
    building_options = list(LOCATIONS)
    if st.session_state.get("warehouse3d_building") not in building_options:
        st.session_state["warehouse3d_building"] = building_options[0]
    with building_col:
        building = st.selectbox("위치 선택", building_options, key="warehouse3d_building")
    with perf_span("warehouse3d.layout_sync_tools"):
        shared_layout_store = render_warehouse_layout_sync_tools()
    default_floor = LOCATIONS[building]["default_floor"]
    floor = default_floor
    drawing_mode = "3D 배치"
    drawing = {"name": "", "source": "", "src": "", "kind": "", "available": False}
    with perf_span("warehouse3d.data_processing.layout"):
        racks = build_rack_layout(inventory_rows, floor)
        summary = warehouse_summary(racks, inventory_rows)
    with perf_span("warehouse3d.summary_render"):
        render_summary(summary, work_date)
    save_notice = st.session_state.pop("warehouse3d_save_notice", None)
    if isinstance(save_notice, tuple) and len(save_notice) == 2:
        tone, message = save_notice
        if tone == "success":
            st.success(message)
        else:
            st.error(message)

    selected_view = lazy_tab_selector(["3D 배치", "재고 위치표"], "warehouse3d_view")
    if selected_view == "3D 배치":
        with perf_span("warehouse3d.component_html_build", component="scene3d"):
            scene_html = warehouse_scene3d_html(
                building=building,
                floor=floor,
                drawing_mode=drawing_mode,
                drawing=drawing,
                racks=racks,
                zones=FLOOR_ZONES.get(floor, []),
                inventory_rows=inventory_rows,
                shared_layout_store=shared_layout_store,
            )
        with perf_span("warehouse3d.component_render", component="scene3d"):
            save_request = warehouse3d_scene_component(
                html=scene_html,
                height=860,
                key=f"warehouse3d_scene_{building}",
                default=None,
            )
        if handle_warehouse3d_layout_save_request(save_request):
            st.rerun()
    else:
        with perf_span("warehouse3d.component_html_build", component="stock_position"):
            stock_html = warehouse_stock_position_html(
                building=building,
                inventory_rows=inventory_rows,
                shared_layout_store=shared_layout_store,
            )
        with perf_span("warehouse3d.component_render", component="stock_position"):
            components.html(
                stock_html,
                height=760,
                scrolling=True,
            )
    return

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
                shared_layout_store=shared_layout_store,
            ),
            height=860,
            scrolling=True,
        )
    with stock_tab:
        components.html(
            warehouse_stock_position_html(
                building=building,
                inventory_rows=inventory_rows,
                shared_layout_store=shared_layout_store,
            ),
            height=760,
            scrolling=True,
        )


def warehouse_location_floor_options() -> list[dict]:
    return [
        {
            "building": building,
            "floor": floor,
            "key": f"{building}::{floor}",
            "label": f"{building} {floor}",
        }
        for building, config in LOCATIONS.items()
        for floor in config["floors"]
    ]


def warehouse_stock_position_html(building: str, inventory_rows: list[dict], shared_layout_store: dict | None = None) -> str:
    default_floor = LOCATIONS[building]["default_floor"]
    location_floors = warehouse_location_floor_options()
    default_location_key = f"{building}::{default_floor}"
    floor_payload = json.dumps(
        {option["key"]: build_rack_layout(inventory_rows, option["floor"]) for option in location_floors},
        ensure_ascii=False,
    )
    legacy_location_map_payload = json.dumps({"밑창고1": "창고1", "옆창고2": "창고2"}, ensure_ascii=False)
    shared_layout_payload = json.dumps(shared_layout_store or empty_warehouse_layout_store(), ensure_ascii=False)
    location_floors_payload = json.dumps(warehouse_location_floor_options(), ensure_ascii=False)

    return f"""
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">

        <style>
            * {{ box-sizing: border-box; letter-spacing: 0; }}
            body {{
                background: transparent;
                color: #1f2937;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                margin: 0;
                overflow: auto;
            }}
            .stock-board {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                display: flex;
                flex-direction: column;
                height: 684px;
                min-height: 0;
                padding: 0.86rem;
            }}
            .stock-head {{
                align-items: center;
                display: flex;
                gap: 0.7rem;
                justify-content: space-between;
                margin-bottom: 0.7rem;
            }}
            h3 {{
                color: #1f2937;
                font-size: 1rem;
                margin: 0;
            }}
            .stock-head span {{
                color: #64748b;
                font-size: 0.74rem;
                font-weight: 850;
            }}
            .stock-tools {{
                display: grid;
                gap: 0.45rem;
                grid-template-columns: minmax(180px, 1fr) 170px 86px;
                margin-bottom: 0.7rem;
            }}
            input,
            select,
            button {{
                background: #e7ebe6;
                border: 1px solid #c5cec7;
                border-radius: 9px;
                color: #303a42;
                font-size: 0.78rem;
                font-weight: 850;
                min-height: 34px;
                outline: 0;
                padding: 0 0.6rem;
            }}
            button {{ cursor: pointer; }}
            .stock-table {{
                border: 1px solid #e2e8f0;
                border-radius: 10px;
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
                border-bottom: 1px solid #e2e8f0;
                color: #1f2937;
                padding: 0.5rem;
                text-align: left;
            }}
            th {{
                background: #f8fafc;
                color: #475569;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #64748b;
                padding: 1rem;
                text-align: center;
            }}
            .stock-foot {{
                color: #64748b;
                font-size: 0.72rem;
                font-weight: 850;
                margin-top: 0.62rem;
            }}
            .floor-context {{
                color: #64748b;
                font-size: 0.74rem;
                font-weight: 850;
                white-space: nowrap;
            }}
            body {{
                color: #334155;
            }}
            .stock-board {{
                background: #F2EFEA;
                border-color: #D8D2C8;
            }}
            input,
            select,
            button {{
                background: #FAF8F5;
                border-color: #CFC7BC;
                color: #1F2933;
            }}
            .stock-table {{
                border-color: #D8D2C8;
            }}
            th {{
                background: #EDE8E1;
                color: #2F4659;
            }}
            td {{
                background: #FAF8F5;
                border-bottom-color: #E2DCD4;
                color: #1F2933;
            }}
            tr:nth-child(even) td {{
                background: #F2EFEA;
            }}
        </style>
    </head>
    <body>
        <section class="stock-board">
            <div class="stock-head">
                <h3>재고 위치표</h3>
                <span class="floor-context" id="floorContext"></span>
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
            const locationFloors = {location_floors_payload};
            const defaultRacksByLocationFloor = {floor_payload};
            const sharedLayoutStore = {shared_layout_payload};
            const legacyLocationMap = {legacy_location_map_payload};
            const stockBody = document.getElementById("stockBody");
            const stockFoot = document.getElementById("stockFoot");
            const stockSearch = document.getElementById("stockSearch");
            const floorFilter = document.getElementById("floorFilter");
            const floorContext = document.getElementById("floorContext");
            const defaultLocationKey = {json.dumps(default_location_key, ensure_ascii=False)};

            function escapeHtml(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;");
            }}

            function storageKeyFor(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:${{floorName}}`;
            }}

            function fixtureStorageKeyFor(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:fixtures:${{floorName}}`;
            }}

            function loadJson(key, fallback) {{
                try {{
                    const value = JSON.parse(localStorage.getItem(key) || "null");
                    return value ?? fallback;
                }} catch (error) {{
                    return fallback;
                }}
            }}

            function sharedFloorData(buildingName, floorName) {{
                return sharedLayoutStore?.locations?.[buildingName]?.[floorName] || null;
            }}

            function loadJsonKeys(keys, fallback = null) {{
                for (const key of keys) {{
                    const value = loadJson(key, null);
                    if (value !== null) return value;
                }}
                return fallback;
            }}

            function uniqueKeys(keys) {{
                return keys.filter(Boolean).filter((key, index, list) => list.indexOf(key) === index);
            }}

            function layoutStorageKeyCandidates(buildingName, floorName) {{
                const legacyName = legacyLocationMap[buildingName] || "";
                return uniqueKeys([
                    storageKeyFor(buildingName, floorName),
                    `warehouseRackLayout:${{activeBuilding}}:${{buildingName}}:${{floorName}}`,
                    legacyName ? storageKeyFor(legacyName, floorName) : "",
                    legacyName ? `warehouseRackLayout:${{activeBuilding}}:${{legacyName}}:${{floorName}}` : "",
                ]);
            }}

            function fixtureStorageKeyCandidates(buildingName, floorName) {{
                const legacyName = legacyLocationMap[buildingName] || "";
                return uniqueKeys([
                    fixtureStorageKeyFor(buildingName, floorName),
                    `warehouseRackLayout:${{activeBuilding}}:${{buildingName}}:fixtures:${{floorName}}`,
                    legacyName ? fixtureStorageKeyFor(legacyName, floorName) : "",
                    legacyName ? `warehouseRackLayout:${{activeBuilding}}:${{legacyName}}:fixtures:${{floorName}}` : "",
                ]);
            }}

            function loadRacks(buildingName, floorName, optionKey) {{
                let saved = sharedFloorData(buildingName, floorName)?.racks;
                if (!Array.isArray(saved)) {{
                    saved = loadJsonKeys(layoutStorageKeyCandidates(buildingName, floorName), null);
                }}
                return Array.isArray(saved) ? saved : (defaultRacksByLocationFloor[optionKey] || []);
            }}

            function loadFixtures(buildingName, floorName) {{
                let saved = sharedFloorData(buildingName, floorName)?.fixtures;
                if (!Array.isArray(saved)) {{
                    saved = loadJsonKeys(fixtureStorageKeyCandidates(buildingName, floorName), null);
                }}
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

            function addRow(rows, buildingName, floorName, location, shape, name, barcode, qty, stack = 1) {{
                const product = String(name || "").trim();
                const itemBarcode = String(barcode || "").trim();
                const count = Number(qty || 0);
                if (!product || !count) return;
                const type = shapeLabel(shape);
                const typeText = type === "파렛트" ? `${{type}} ${{stackLabel(stack)}}` : type;
                const key = `${{buildingName}}::${{floorName}}::${{location}}::${{typeText}}::${{itemBarcode || product}}`;
                const existing = rows.get(key);
                if (existing) {{
                    existing.qty += count;
                    return;
                }}
                rows.set(key, {{
                    building: buildingName,
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
                locationFloors.forEach(option => {{
                    const buildingName = option.building;
                    const floorName = option.floor;
                    loadRacks(buildingName, floorName, option.key).forEach(rack => {{
                        const parts = partOptionsFor(rack);
                        (rack.items || []).forEach((item, index) => {{
                            const part = item.part || parts[index % parts.length] || "1단";
                            const location = `${{rack.id || "랙"}} / ${{part}}`;
                            addRow(rows, buildingName, floorName, location, item.shape || "box", item.name, item.barcode, quantityOf(item), item.stack || 1);
                            if ((item.shape === "pallet" || item.shape === "wrapped_pallet") && Array.isArray(item.items)) {{
                                item.items.forEach(innerItem => {{
                                    addRow(rows, buildingName, floorName, `${{location}} / 파렛트 내부`, innerItem.shape || "box", innerItem.name, innerItem.barcode, quantityOf(innerItem), innerItem.stack || 1);
                                }});
                            }}
                        }});
                    }});
                    loadFixtures(buildingName, floorName).forEach(fixture => {{
                        if (!["box", "pallet", "wrapped_pallet"].includes(fixture.type)) return;
                        const location = `바닥 X${{Number(fixture.x || 0).toFixed(0)}} Y${{Number(fixture.y || 0).toFixed(0)}}`;
                        addRow(rows, buildingName, floorName, location, fixture.type, fixture.label, fixture.barcode, Number(fixture.qty || 1), fixture.stack || 1);
                        if (fixture.type === "pallet" && Array.isArray(fixture.items)) {{
                            fixture.items.forEach(innerItem => {{
                                addRow(rows, buildingName, floorName, `${{location}} / 파렛트 내부`, innerItem.shape || "box", innerItem.name, innerItem.barcode, quantityOf(innerItem), innerItem.stack || 1);
                            }});
                        }}
                    }});
                }});
                return Array.from(rows.values()).sort((a, b) =>
                    a.building.localeCompare(b.building, "ko-KR") ||
                    a.floor.localeCompare(b.floor, "ko-KR") ||
                    a.location.localeCompare(b.location, "ko-KR") ||
                    a.name.localeCompare(b.name, "ko-KR")
                );
            }}

            function renderFloorFilter() {{
                floorFilter.innerHTML = '<option value="ALL">전체 위치</option>' + locationFloors.map(option =>
                    `<option value="${{escapeHtml(option.key)}}">${{escapeHtml(option.label)}}</option>`
                ).join("");
                floorFilter.value = locationFloors.some(option => option.key === defaultLocationKey) ? defaultLocationKey : "ALL";
                const selected = locationFloors.find(option => option.key === floorFilter.value);
                floorContext.textContent = selected ? `${{selected.label}} 기준` : "전체 위치 기준";
            }}

            function renderRows() {{
                const query = stockSearch.value.trim().toLowerCase();
                const selectedKey = floorFilter.value || "ALL";
                const selected = locationFloors.find(option => option.key === selectedKey);
                floorContext.textContent = selected ? `${{selected.label}} 기준` : "전체 위치 기준";
                const rows = collectRows().filter(row => {{
                    if (selected && (row.building !== selected.building || row.floor !== selected.floor)) return false;
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
        init_db(ensure_schema=False)
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
        if InventoryDaily is None:
            return [], ""
        work_date = db.scalar(
            select(func.max(InventoryDaily.work_date)).where(InventoryDaily.source_type == "창고")
        )
        if not work_date:
            return [], ""
        rows = [
            services.daily_to_dict(row)
            for row in db.execute(
                select(InventoryDaily)
                .where(InventoryDaily.source_type == "창고", InventoryDaily.work_date == work_date)
                .order_by(InventoryDaily.category, InventoryDaily.product_name, InventoryDaily.barcode)
            ).scalars()
        ]
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
                    background: #ffffff;
                    border: 1px solid #e2e8f0;
                    border-radius: 12px;
                    min-height: 58px;
                    padding: 0.58rem 0.68rem;
                }}
                .warehouse3d-kpi span {{
                    color: #64748b;
                    display: block;
                    font-size: 0.72rem;
                    font-weight: 900;
                    margin-bottom: 0.28rem;
                }}
                .warehouse3d-kpi strong {{
                    color: #1f2937;
                    display: block;
                    font-size: 0.94rem;
                    font-weight: 950;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                }}
                .warehouse3d-kpi {{
                    background: #F2EFEA;
                    border-color: #D8D2C8;
                }}
                .warehouse3d-kpi span {{
                    color: #64748B;
                }}
                .warehouse3d-kpi strong {{
                    color: #1F2933;
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
        height=72,
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
                color: #1f2937;
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
                background: #F2EFEA;
                border: 1px solid #D8D2C8;
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
                color: #1F2933;
                font-size: 0.96rem;
                margin: 0 0 0.56rem;
            }}
            .building-name {{
                color: #64748B;
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
                background: #FAF8F5;
                border: 1px solid #CFC7BC;
                border-radius: 7px;
                color: #2F4659;
                cursor: pointer;
                min-height: 34px;
                font-size: 0.78rem;
                font-weight: 900;
                padding: 0.52rem 0.6rem;
                text-align: center;
            }}
            .floor-chip.active {{
                background: #E8E3DC;
                border-color: #C6BDB0;
                color: #2F4659;
                box-shadow: inset 3px 0 0 #4F6F8F;
            }}
            .zone-tags {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.32rem;
                margin-top: 0.8rem;
            }}
            .zone-tags span {{
                background: #E8E3DC;
                border: 1px solid #D8D2C8;
                border-radius: 999px;
                color: #2F4659;
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
                color: #64748B;
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
                background: #FAF8F5;
                border: 1px solid #CFC7BC;
                border-radius: 7px;
                color: #1F2933;
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
                background: #E8E3DC;
                border-color: #B9AEA0;
            }}
            .floor-plan {{
                background:
                    linear-gradient(rgba(79, 111, 143, 0.10) 1px, transparent 1px),
                    linear-gradient(90deg, rgba(79, 111, 143, 0.10) 1px, transparent 1px),
                    #FAF8F5;
                background-size: 36px 36px;
                border: 1px solid #D8D2C8;
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
                color: #2F4659;
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
                background: #F2EFEA;
                border: 1px solid #D8D2C8;
                border-radius: 8px;
                padding: 0.82rem 1rem;
            }}
            .plan-label {{
                background: #EDE8E1;
                border: 1px solid #D8D2C8;
                border-radius: 6px;
                color: #2F4659;
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
                background: linear-gradient(145deg, #E8E3DC, #DCD6CE);
                border: 1px solid #C6BDB0;
                border-radius: 5px;
                box-shadow: 0 8px 18px rgba(45, 38, 30, 0.12);
                color: #1F2933;
                cursor: pointer;
                min-height: 34px;
                padding: 0.42rem;
                position: absolute;
                touch-action: none;
                transition: transform 0.12s ease, border-color 0.12s ease;
            }}
            .rack:hover,
            .rack.active {{
                border-color: #4F6F8F;
                outline: 2px solid rgba(79, 111, 143, 0.20);
            }}
            .rack.short {{
                background: linear-gradient(145deg, #F3E8E4, #E8D5CF);
                border-color: #D9BBB2;
            }}
            .rack.empty {{
                background: linear-gradient(145deg, #FAF8F5, #EEEAE4);
                border-style: dashed;
            }}
            .rack strong {{
                display: block;
                font-size: 0.72rem;
            }}
            .rack span {{
                color: #64748B;
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
                background: #FAF8F5;
                border: 1px solid #D8D2C8;
                border-radius: 7px;
                margin-bottom: 0.7rem;
                padding: 0.68rem;
            }}
            .rack-detail strong {{
                color: #1F2933;
                display: block;
                font-size: 1.05rem;
            }}
            .rack-detail span {{
                color: #64748B;
                display: block;
                font-size: 0.76rem;
                font-weight: 800;
                margin-top: 0.26rem;
            }}
            .item-list {{
                border: 1px solid #D8D2C8;
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
                border-bottom: 1px solid #E2DCD4;
                color: #1F2933;
                padding: 0.42rem;
                text-align: left;
            }}
            th {{
                background: #EDE8E1;
                color: #2F4659;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #64748B;
                text-align: center;
            }}
            @media (max-width: 980px) {{
                body {{
                    overflow: auto;
                }}
                .warehouse-scene {{
                    gap: 0.55rem;
                    grid-template-columns: 1fr;
                    height: auto;
                    min-height: 0;
                }}
                .building-panel,
                .rack-panel,
                .detail-panel {{
                    padding: 0.62rem;
                }}
                .building-stack {{
                    flex-direction: row;
                    flex-wrap: wrap;
                }}
                .floor-chip {{
                    flex: 1 1 70px;
                    min-width: 70px;
                }}
                .scene-head {{
                    align-items: flex-start;
                    flex-direction: column;
                    gap: 0.2rem;
                }}
                .scene-tools {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .floor-plan {{
                    height: clamp(420px, 72vh, 560px);
                }}
                .assign-box {{
                    grid-template-columns: 1fr;
                }}
                .item-list table {{
                    min-width: 560px;
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
            const sharedLayoutStore = {json.dumps(empty_warehouse_layout_store(), ensure_ascii=False)};
            const legacyLocationMap = {json.dumps(LEGACY_LOCATION_MAP, ensure_ascii=False)};
            const inventory = {inventory_payload};
            const baseStorageKey = {json.dumps(base_storage_key, ensure_ascii=False)};
            const activeBuilding = {json.dumps(building, ensure_ascii=False)};
            const locationFocus = {{
                "로긴": ["제조", "출입", "옥상"],
                "포장부서": ["포장", "작업", "부자재", "반제품"],
                "밑창고1": ["피킹", "완제품", "랙 배치", "장기보관"],
                "옆창고2": ["예비", "저회전", "임시 보관"],
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
                return storageKeyForLocation(activeBuilding, floorName);
            }}

            function fixtureStorageKeyFor(floorName) {{
                return fixtureStorageKeyForLocation(activeBuilding, floorName);
            }}

            function floorSizeStorageKeyFor(floorName) {{
                return floorSizeStorageKeyForLocation(activeBuilding, floorName);
            }}

            function storageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:${{floorName}}`;
            }}

            function fixtureStorageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:fixtures:${{floorName}}`;
            }}

            function floorSizeStorageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:floorSize:${{floorName}}`;
            }}

            function uniqueKeys(keys) {{
                return keys.filter(Boolean).filter((key, index, list) => list.indexOf(key) === index);
            }}

            function layoutStorageKeyCandidates(buildingName, floorName) {{
                const legacyName = legacyLocationMap[buildingName] || "";
                return uniqueKeys([
                    storageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}${{floorName}}` : "",
                    legacyName ? storageKeyForLocation(legacyName, floorName) : "",
                    legacyName ? `${{baseStorageKey}}${{legacyName}}:${{floorName}}` : "",
                ]);
            }}

            function fixtureStorageKeyCandidates(buildingName, floorName) {{
                const legacyName = legacyLocationMap[buildingName] || "";
                return uniqueKeys([
                    fixtureStorageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:fixtures:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}fixtures:${{floorName}}` : "",
                    legacyName ? fixtureStorageKeyForLocation(legacyName, floorName) : "",
                    legacyName ? `${{baseStorageKey}}${{legacyName}}:fixtures:${{floorName}}` : "",
                ]);
            }}

            function floorSizeStorageKeyCandidates(buildingName, floorName) {{
                const legacyName = legacyLocationMap[buildingName] || "";
                return uniqueKeys([
                    floorSizeStorageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:floorSize:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}floorSize:${{floorName}}` : "",
                    legacyName ? floorSizeStorageKeyForLocation(legacyName, floorName) : "",
                    legacyName ? `${{baseStorageKey}}${{legacyName}}:floorSize:${{floorName}}` : "",
                ]);
            }}

            function sharedFloorData(buildingName, floorName) {{
                return sharedLayoutStore?.locations?.[buildingName]?.[floorName] || null;
            }}

            function readJsonFromLocalStorage(key, fallback = null) {{
                try {{
                    const value = JSON.parse(localStorage.getItem(key) || "null");
                    return value ?? fallback;
                }} catch (error) {{
                    return fallback;
                }}
            }}

            function readJsonFromLocalStorageKeys(keys, fallback = null) {{
                for (const key of keys) {{
                    const value = readJsonFromLocalStorage(key, null);
                    if (value !== null) return value;
                }}
                return fallback;
            }}

            function writeJsonToLocalStorage(key, value) {{
                localStorage.setItem(key, JSON.stringify(value));
            }}

            function hasSharedFloorData(buildingName, floorName) {{
                const shared = sharedFloorData(buildingName, floorName);
                return Boolean(
                    Array.isArray(shared?.racks) ||
                    Array.isArray(shared?.fixtures) ||
                    (shared?.floor_size && Number.isFinite(Number(shared.floor_size.width)) && Number.isFinite(Number(shared.floor_size.depth)))
                );
            }}

            function hasBrowserStoredFloorData(buildingName, floorName) {{
                const browserRacks = readJsonFromLocalStorageKeys(layoutStorageKeyCandidates(buildingName, floorName), null);
                const browserFixtures = readJsonFromLocalStorageKeys(fixtureStorageKeyCandidates(buildingName, floorName), null);
                const browserFloorSize = readJsonFromLocalStorageKeys(floorSizeStorageKeyCandidates(buildingName, floorName), null);
                return Boolean(
                    Array.isArray(browserRacks) ||
                    Array.isArray(browserFixtures) ||
                    (browserFloorSize && Number.isFinite(Number(browserFloorSize.width)) && Number.isFinite(Number(browserFloorSize.depth)))
                );
            }}

            function shouldMigrateBrowserLayoutToDatabase() {{
                return !hasSharedFloorData(activeBuilding, activeFloor) && hasBrowserStoredFloorData(activeBuilding, activeFloor);
            }}

            function hydrateBrowserLayoutFromSharedStore() {{
                locationFloors.forEach(option => {{
                    const shared = sharedFloorData(option.building, option.floor);
                    if (!shared || typeof shared !== "object") return;
                    if (Array.isArray(shared.racks)) {{
                        writeJsonToLocalStorage(storageKeyForLocation(option.building, option.floor), normalizeRackIds(shared.racks));
                    }}
                    if (Array.isArray(shared.fixtures)) {{
                        writeJsonToLocalStorage(fixtureStorageKeyForLocation(option.building, option.floor), shared.fixtures);
                    }}
                    if (shared.floor_size && Number.isFinite(Number(shared.floor_size.width)) && Number.isFinite(Number(shared.floor_size.depth))) {{
                        writeJsonToLocalStorage(floorSizeStorageKeyForLocation(option.building, option.floor), shared.floor_size);
                    }}
                }});
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
                const sharedSize = sharedFloorData(activeBuilding, floorName)?.floor_size;
                if (sharedSize && Number.isFinite(Number(sharedSize.width)) && Number.isFinite(Number(sharedSize.depth))) {{
                    return {{
                        width: clamp(Number(sharedSize.width), base.width * 0.45, base.width * 2.6),
                        depth: clamp(Number(sharedSize.depth), base.depth * 0.45, base.depth * 2.6),
                        x: Number.isFinite(Number(sharedSize.x)) ? Number(sharedSize.x) : 0,
                        z: Number.isFinite(Number(sharedSize.z)) ? Number(sharedSize.z) : 0,
                    }};
                }}
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
    shared_layout_store: dict | None = None,
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
    shared_layout_payload = json.dumps(shared_layout_store or empty_warehouse_layout_store(), ensure_ascii=False)
    location_floors_payload = json.dumps(warehouse_location_floor_options(), ensure_ascii=False)
    legacy_location_map_payload = json.dumps(LEGACY_LOCATION_MAP, ensure_ascii=False)
    supabase_browser_config_payload = json.dumps(warehouse_layout_supabase_browser_config(), ensure_ascii=False)
    vendor_sources = warehouse3d_vendor_sources()
    three_source_payload = json.dumps(vendor_sources.get("three", ""))
    controls_source_payload = json.dumps(vendor_sources.get("controls", ""))
    layout_api_port = None
    layout_api_port_payload = json.dumps(layout_api_port)
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
        <style>
            * {{ box-sizing: border-box; letter-spacing: 0; }}
            body {{
                background: transparent;
                color: #d7ddd9;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                margin: 0;
                overflow: auto;
            }}
            .warehouse-scene {{
                display: grid;
                gap: 0.72rem;
                grid-template-columns: 160px minmax(0, 1.18fr) minmax(410px, 0.92fr);
                height: 684px;
            }}
            .panel {{
                background: #edf0ec;
                border: 1px solid #cfd6d0;
                border-radius: 12px;
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
                color: #273038;
                font-size: 0.96rem;
                margin: 0 0 0.56rem;
            }}
            .building-name,
            .scene-head span {{
                color: #6d7772;
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
                background: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 9px;
                color: #1f2937;
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
                background: #dde4de;
                border-color: #aebbb2;
            }}
            .floor-chip {{
                color: #3f4a50;
                font-weight: 900;
                padding: 0.52rem 0.6rem;
                text-align: center;
            }}
            .floor-chip.active {{
                background: #dde4de;
                border-color: #b6c0b8;
                box-shadow: inset 3px 0 0 #657681;
                color: #43515a;
            }}
            .zone-tags {{
                display: flex;
                flex-wrap: wrap;
                gap: 0.32rem;
                margin-top: 0.8rem;
            }}
            .zone-tags span {{
                background: #e0e5df;
                border: 1px solid #cbd4cd;
                border-radius: 999px;
                color: #647068;
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
            .layout-save-status {{
                align-items: center;
                color: #50645f;
                display: inline-flex;
                flex: 1 0 100%;
                font-size: 0.74rem;
                font-weight: 900;
                line-height: 1.35;
                min-height: 0;
                min-width: 0;
                overflow-wrap: anywhere;
                padding: 0.04rem 0.28rem 0.1rem;
                white-space: normal;
                width: 100%;
                word-break: break-word;
            }}
            .layout-save-status:empty {{
                display: none;
            }}
            .fixture-name-toggle {{
                align-items: center;
                background: #e0e5df;
                border: 1px solid #cbd4cd;
                border-radius: 9px;
                color: #46545c;
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
                accent-color: #657681;
                margin: 0;
            }}
            .floor-size-tools {{
                align-items: center;
                background: rgba(236, 240, 235, 0.94);
                border: 1px solid #c8d1ca;
                border-radius: 10px;
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
                color: #59645f;
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
                background: rgba(236, 240, 235, 0.94);
                border: 1px solid #c8d1ca;
                border-radius: 10px;
                display: flex;
                gap: 0.36rem;
                padding: 0.34rem;
                position: absolute;
                right: 1rem;
                top: 1rem;
                z-index: 3;
            }}
            .zoom-tools span {{
                color: #59645f;
                font-size: 0.68rem;
                font-weight: 900;
                margin-right: 0.1rem;
            }}
            .zoom-tools button {{
                min-height: 26px;
                padding: 0 0.42rem;
            }}
            .zoom-tools button.active {{
                background: #657681;
                border-color: #657681;
                color: #f2f4f1;
            }}
            .nav-tools {{
                background: rgba(236, 240, 235, 0.94);
                border: 1px solid #c8d1ca;
                border-radius: 10px;
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
                    linear-gradient(180deg, #edf2ed, #dfe7df);
                border: 1px solid #c8d1ca;
                border-radius: 12px;
                min-height: 0;
                overflow: hidden;
                position: relative;
            }}
            #warehouseCanvas {{
                background: #b9c4ba;
                display: block;
                height: 100%;
                width: 100%;
            }}
            .model-label {{
                background: rgba(236, 240, 235, 0.92);
                border: 1px solid #c8d1ca;
                border-radius: 9px;
                color: #46545c;
                font-size: 0.68rem;
                font-weight: 900;
                left: 1rem;
                padding: 0.32rem 0.42rem;
                position: absolute;
                top: 1rem;
                z-index: 3;
            }}
            .model-help {{
                background: rgba(236, 240, 235, 0.92);
                border: 1px solid #c8d1ca;
                border-radius: 9px;
                bottom: 1rem;
                color: #6b756f;
                font-size: 0.68rem;
                font-weight: 900;
                left: 1rem;
                padding: 0.32rem 0.42rem;
                position: absolute;
                z-index: 3;
            }}
            .model-error {{
                align-items: center;
                color: #1f2937;
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
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                margin-bottom: 0.58rem;
                padding: 0.62rem;
            }}
            .rack-detail strong {{
                color: #1f2937;
                display: block;
                font-size: 1.05rem;
            }}
            .rack-detail span {{
                color: #64748b;
                display: block;
                font-size: 0.76rem;
                font-weight: 800;
                margin-top: 0.26rem;
            }}
            .assign-box {{
                display: grid;
                gap: 0.36rem;
                grid-template-columns: minmax(0, 1.1fr) minmax(0, 0.85fr) 56px 74px 82px 62px 64px;
                margin-bottom: 0.5rem;
            }}
            #itemSelect {{
                display: none !important;
            }}
            .detail-tools {{
                flex: 0 0 auto;
            }}
            .stock-guide {{
                background: #eef3f7;
                border: 1px solid #dbe3ec;
                border-radius: 10px;
                color: #475569;
                font-size: 0.7rem;
                font-weight: 850;
                line-height: 1.45;
                margin-bottom: 0.5rem;
                padding: 0.52rem 0.62rem;
            }}
            .nudge-grid {{
                display: grid;
                gap: 0.3rem;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                margin-bottom: 0.5rem;
            }}
            .fixture-box {{
                display: grid;
                gap: 0.36rem;
                grid-template-columns: minmax(0, 1fr) repeat(4, 76px);
                margin-bottom: 0.5rem;
            }}
            .move-to-rack-box {{
                display: grid;
                gap: 0.36rem;
                grid-template-columns: minmax(0, 1fr) 82px 116px;
                margin-bottom: 0.5rem;
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
                color: #334155;
                font-size: 0.72rem;
                font-weight: 900;
                margin: 0.1rem 0 0.36rem;
            }}
            .item-list {{
                border: 1px solid #e2e8f0;
                border-radius: 10px;
                flex: 1 1 auto;
                min-height: 122px;
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
                border-bottom: 1px solid #e2e8f0;
                color: #1f2937;
                padding: 0.38rem 0.42rem;
                text-align: left;
            }}
            th {{
                background: #f8fafc;
                color: #475569;
                font-weight: 900;
                position: sticky;
                top: 0;
            }}
            .empty {{
                color: #64748b;
                text-align: center;
            }}
            body {{
                color: #334155;
            }}
            .panel {{
                background: #F2EFEA;
                border-color: #D8D2C8;
            }}
            .building-panel h3,
            .model-panel h3,
            .detail-panel h3,
            .rack-detail strong,
            .tool-label,
            th,
            td {{
                color: #1F2933;
            }}
            .building-name,
            .scene-head span,
            .rack-detail span,
            .stock-guide,
            .empty {{
                color: #64748B;
            }}
            .floor-chip,
            button,
            select,
            input {{
                background: #FAF8F5;
                border-color: #CFC7BC;
                color: #1F2933;
            }}
            button:hover,
            .floor-chip.active {{
                background: #E8E3DC;
                border-color: #B9AEA0;
                color: #2F4659;
            }}
            .zone-tags span,
            .fixture-name-toggle,
            .stock-guide,
            .rack-detail,
            .floor-size-tools,
            .zoom-tools,
            .nav-tools,
            .model-label,
            .model-help {{
                background: rgba(242, 239, 234, 0.94);
                border-color: #D8D2C8;
                color: #2F4659;
            }}
            .model-viewport {{
                background:
                    linear-gradient(180deg, #FAF8F5, #F0EAE1);
                border-color: #D8D2C8;
            }}
            .item-list {{
                border-color: #D8D2C8;
            }}
            th {{
                background: #EDE8E1;
                color: #2F4659;
            }}
            td {{
                background: #FAF8F5;
                border-bottom-color: #E2DCD4;
                color: #1F2933;
            }}
            tr:nth-child(even) td {{
                background: #F2EFEA;
            }}
            @media (max-width: 980px) {{
                body {{
                    overflow: auto;
                }}
                .warehouse-scene {{
                    gap: 0.55rem;
                    grid-template-columns: 1fr;
                    height: auto;
                    min-height: 0;
                }}
                .building-panel,
                .model-panel,
                .detail-panel {{
                    padding: 0.62rem;
                }}
                .building-stack {{
                    flex-direction: row;
                    flex-wrap: wrap;
                }}
                .floor-chip {{
                    flex: 1 1 70px;
                    min-width: 70px;
                }}
                .scene-head {{
                    align-items: flex-start;
                    flex-direction: column;
                    gap: 0.2rem;
                }}
                .scene-tools {{
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .scene-tools select,
                .scene-tools button,
                #rackTypeSelect,
                #rackLevelSelect,
                #rackBottomSelect,
                #rackStackTargetSelect,
                .layout-save-status {{
                    flex: initial;
                    min-width: 0;
                    width: 100%;
                }}
                .model-viewport {{
                    height: clamp(520px, 72vh, 650px);
                    min-height: 520px;
                }}
                .floor-size-tools {{
                    grid-template-columns: auto minmax(48px, 58px) auto minmax(48px, 58px);
                    left: 0.5rem;
                    max-width: calc(100% - 1rem);
                    top: 0.5rem;
                }}
                .floor-size-tools button {{
                    min-width: 0;
                }}
                .zoom-tools {{
                    left: 0.5rem;
                    max-width: calc(100% - 1rem);
                    overflow-x: auto;
                    right: auto;
                    top: 4.2rem;
                }}
                .nav-tools {{
                    bottom: 0.55rem;
                    right: 0.55rem;
                    top: auto;
                }}
                .model-label {{
                    display: none;
                }}
                .model-help {{
                    bottom: 0.55rem;
                    left: 0.55rem;
                    max-width: calc(100% - 7rem);
                }}
                .detail-panel {{
                    min-height: 360px;
                }}
                .assign-box,
                .fixture-box,
                .move-to-rack-box,
                .move-floor-box {{
                    grid-template-columns: 1fr;
                }}
                .nudge-grid {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .item-list table {{
                    min-width: 620px;
                }}
            }}
            @media (max-width: 560px) {{
                .scene-tools {{
                    grid-template-columns: 1fr;
                }}
                .model-viewport {{
                    height: clamp(500px, 78vh, 620px);
                    min-height: 500px;
                }}
                .floor-size-tools {{
                    grid-template-columns: auto minmax(48px, 1fr) auto minmax(48px, 1fr);
                    width: calc(100% - 1rem);
                }}
                .floor-size-tools button {{
                    grid-column: span 2;
                }}
                .zoom-tools {{
                    top: 6.55rem;
                    width: calc(100% - 1rem);
                }}
                .zoom-tools button {{
                    flex: 1 0 auto;
                }}
                .model-help {{
                    display: none;
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
                    <button type="button" id="saveLayoutFile">Supabase 저장</button>
                    <span id="layoutSaveStatus" class="layout-save-status"></span>
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
                    <div class="model-help">렉/박스 클릭 후 드래그 이동 · 빈 화면 드래그 회전 · 모서리 핸들 크기 조절 · 방향키 이동</div>
                    <div class="model-error" id="modelError">3D 화면을 초기화하지 못했습니다.<br>잠시 후 새로고침하거나 관리자에게 오류 내용을 전달해주세요.</div>
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
            const threeSource = {three_source_payload};
            const controlsSource = {controls_source_payload};

            function escapeWarehouse3dError(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;")
                    .replaceAll("'", "&#039;");
            }}

            async function loadLocalWarehouse3dModules() {{
                if (!threeSource || !controlsSource) {{
                    throw new Error("로컬 Three.js 파일을 찾지 못했습니다.");
                }}
                const threeUrl = URL.createObjectURL(new Blob([threeSource], {{ type: "text/javascript" }}));
                const patchedControlsSource = controlsSource.replace(/from\\s+['"]three['"]\\s*;/g, `from ${{JSON.stringify(threeUrl)}};`);
                if (patchedControlsSource === controlsSource && /from\\s+['"]three['"]/.test(controlsSource)) {{
                    throw new Error("OrbitControls의 Three.js import 경로를 로컬 파일로 바꾸지 못했습니다.");
                }}
                const controlsUrl = URL.createObjectURL(new Blob([patchedControlsSource], {{ type: "text/javascript" }}));
                try {{
                    const threeModule = await import(threeUrl);
                    const controlsModule = await import(controlsUrl);
                    return {{ THREE: threeModule, OrbitControls: controlsModule.OrbitControls }};
                }} finally {{
                    setTimeout(() => {{
                        URL.revokeObjectURL(threeUrl);
                        URL.revokeObjectURL(controlsUrl);
                    }}, 1000);
                }}
            }}

            try {{
            const {{ THREE, OrbitControls }} = await loadLocalWarehouse3dModules();

            const defaultRacks = {payload};
            const defaultRacksByFloor = {floor_payload};
            const zonesByFloor = {zones_payload};
            const floorModels = {floor_model_payload};
            let sharedLayoutStore = {shared_layout_payload};
            const locationFloors = {location_floors_payload};
            const legacyLocationMap = {legacy_location_map_payload};
            const supabaseBrowserConfig = {supabase_browser_config_payload};
            const inventory = {inventory_payload};
            const baseStorageKey = {json.dumps(base_storage_key, ensure_ascii=False)};
            const layoutApiPort = {layout_api_port_payload};
            const layoutApiUrls = resolveLayoutApiUrls(layoutApiPort);
            const activeBuilding = {json.dumps(building, ensure_ascii=False)};
            const locationFocus = {{
                "로긴": ["제조", "출입", "옥상"],
                "포장부서": ["포장", "작업", "부자재", "반제품"],
                "밑창고1": ["피킹", "완제품", "랙 배치", "장기보관"],
                "옆창고2": ["예비", "저회전", "임시 보관"],
            }};
            const floors = {json.dumps(floors, ensure_ascii=False)};
            let activeFloor = {json.dumps(floor, ensure_ascii=False)};
            hydrateBrowserLayoutFromSharedStore();
            let racks = loadLayout(activeFloor);
            let fixtures = [];
            let selectedRackId = racks[0]?.id || "";
            let selectedFixtureId = "";
            let selectedRackItemKey = "";
            let layoutSaveTimer = null;
            let layoutSaveInProgress = false;

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
            const saveLayoutFileButton = document.getElementById("saveLayoutFile");
            const layoutSaveStatus = document.getElementById("layoutSaveStatus");
            const placementScale = 1.45;

            const scene = new THREE.Scene();
            const screenSceneBackground = new THREE.Color(0xb9c4ba);
            const screenSceneFog = null;
            scene.background = screenSceneBackground;
            scene.fog = screenSceneFog;

            const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, alpha: false, preserveDrawingBuffer: true }});
            const screenPixelRatio = Math.min(window.devicePixelRatio || 1, 2);
            renderer.setPixelRatio(screenPixelRatio);
            renderer.setClearColor(screenSceneBackground, 1);
            if ("outputColorSpace" in renderer && THREE.SRGBColorSpace) {{
                renderer.outputColorSpace = THREE.SRGBColorSpace;
            }} else if ("outputEncoding" in renderer && THREE.sRGBEncoding) {{
                renderer.outputEncoding = THREE.sRGBEncoding;
            }}
            renderer.toneMapping = THREE.ACESFilmicToneMapping;
            renderer.toneMappingExposure = 1.08;

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

            const ambient = new THREE.HemisphereLight(0xe2e6e0, 0xb8c0b8, 1.12);
            scene.add(ambient);
            const keyLight = new THREE.DirectionalLight(0xe8ebe5, 1.42);
            keyLight.position.set(18, 28, 22);
            scene.add(keyLight);
            const fillLight = new THREE.DirectionalLight(0xaeb8af, 0.48);
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
                entrance: {{ label: "출입구", w: 4.2, d: 0.45, h: 0.34, color: 0x58799a }},
                door: {{ label: "문", w: 2.2, d: 0.32, h: 1.55, color: 0x8aa0b4 }},
                shutter: {{ label: "셔터", w: 4.8, d: 0.38, h: 1.8, color: 0xa8b3bf }},
                dock: {{ label: "상차도크", w: 5.4, d: 1.6, h: 0.42, color: 0x6f879f }},
                exit: {{ label: "비상구", w: 2.8, d: 0.38, h: 1.45, color: 0x6f927d }},
                elevator: {{ label: "엘리베이터", w: 2.6, d: 2.4, h: 2.3, color: 0x8c99a6 }},
                desk: {{ label: "책상", w: 2.4, d: 1.25, h: 0.82, color: 0xb8874f }},
                wall: {{ label: "벽/칸막이", w: 6.8, d: 0.18, h: 1.35, color: 0x9fb7b2 }},
                aisle: {{ label: "통로", w: 8.0, d: 2.0, h: 0.08, color: 0x9fb1c3 }},
                zone: {{ label: "작업구역", w: 6.2, d: 4.0, h: 0.08, color: 0xb78b5a }},
                box: {{ label: "박스", w: 1.2, d: 1.0, h: 0.72, color: 0xb78b5a }},
                pallet: {{ label: "파렛트", w: 1.55, d: 1.55, h: 1.35, color: 0xa88661 }},
                wrapped_pallet: {{ label: "랩핑 파렛트", w: 1.55, d: 1.55, h: 1.45, color: 0x8da3b8 }},
            }};
            const outsideFixtureTypes = new Set(["entrance", "door", "shutter", "dock", "exit"]);
            const fixtureLabelStorageKey = `${{baseStorageKey}}fixtureLabels`;
            let showFixtureLabels = localStorage.getItem(fixtureLabelStorageKey) !== "hidden";

            function fixtureAllowsOutside(type) {{
                return outsideFixtureTypes.has(type);
            }}

            const materials = {{
                slab: new THREE.MeshStandardMaterial({{ color: 0xe9eef3, roughness: 0.84, metalness: 0.02, transparent: true, opacity: 0.9 }}),
                activeSlab: new THREE.MeshStandardMaterial({{ color: 0xe3ebf1, roughness: 0.68, metalness: 0.03, transparent: true, opacity: 0.94 }}),
                wall: new THREE.MeshStandardMaterial({{ color: 0xcbd5e1, roughness: 0.86, transparent: true, opacity: 0.58 }}),
                rack: new THREE.MeshStandardMaterial({{ color: 0x58799a, roughness: 0.78, metalness: 0.05 }}),
                rackEmpty: new THREE.MeshStandardMaterial({{ color: 0x94a3b8, roughness: 0.82, transparent: true, opacity: 0.72 }}),
                rackShort: new THREE.MeshStandardMaterial({{ color: 0xb66a6a, roughness: 0.75, metalness: 0.04 }}),
                rackPost: new THREE.MeshStandardMaterial({{ color: 0xf4f7f3, roughness: 0.58, metalness: 0.28 }}),
                rackShelf: new THREE.MeshStandardMaterial({{ color: 0xe7ece7, roughness: 0.62, metalness: 0.18 }}),
                rackBrace: new THREE.MeshStandardMaterial({{ color: 0xbecac5, roughness: 0.7, metalness: 0.25 }}),
                heavyPost: new THREE.MeshStandardMaterial({{ color: 0x58799a, roughness: 0.42, metalness: 0.24 }}),
                heavyBeam: new THREE.MeshStandardMaterial({{ color: 0xb78b5a, roughness: 0.46, metalness: 0.18 }}),
                heavyDeck: new THREE.MeshStandardMaterial({{ color: 0xf1e4d1, roughness: 0.7, metalness: 0.04 }}),
                heavyBrace: new THREE.MeshStandardMaterial({{ color: 0x36556f, roughness: 0.5, metalness: 0.22 }}),
                itemBox: new THREE.MeshStandardMaterial({{ color: 0x6f927d, roughness: 0.72, metalness: 0.04 }}),
                itemBoxShort: new THREE.MeshStandardMaterial({{ color: 0xb66a6a, roughness: 0.72, metalness: 0.04 }}),
                itemBoxSelected: new THREE.MeshStandardMaterial({{ color: 0xb78b5a, emissive: 0x3a2815, roughness: 0.68, metalness: 0.04 }}),
                hitbox: new THREE.MeshBasicMaterial({{ color: 0xffffff, transparent: true, opacity: 0, depthWrite: false }}),
                room: new THREE.MeshStandardMaterial({{ color: 0xdbe3ec, roughness: 0.82, transparent: true, opacity: 0.62 }}),
                column: new THREE.MeshStandardMaterial({{ color: 0xf8fafc, roughness: 0.66, metalness: 0.14, transparent: true, opacity: 0.94 }}),
                entrance: new THREE.MeshStandardMaterial({{ color: 0x58799a, emissive: 0x1f3445, roughness: 0.5 }}),
                locked: new THREE.MeshStandardMaterial({{ color: 0xb78b5a, emissive: 0x4a3720, roughness: 0.45 }}),
                resizeHandle: new THREE.MeshStandardMaterial({{ color: 0x58799a, emissive: 0x1f3445, roughness: 0.36, metalness: 0.1 }}),
                roofGarden: new THREE.MeshStandardMaterial({{ color: 0x6f927d, roughness: 0.86, metalness: 0.02, transparent: true, opacity: 0.72 }}),
                roofEquip: new THREE.MeshStandardMaterial({{ color: 0xa7c3c0, roughness: 0.68, metalness: 0.32, transparent: true, opacity: 0.88 }}),
                roofDetail: new THREE.MeshStandardMaterial({{ color: 0xd8c88c, roughness: 0.82, metalness: 0.05, transparent: true, opacity: 0.5 }}),
                selected: new THREE.LineBasicMaterial({{ color: 0xb78b5a }}),
                edge: new THREE.LineBasicMaterial({{ color: 0x6f879f, transparent: true, opacity: 0.76 }}),
                floorEdge: new THREE.LineBasicMaterial({{ color: 0x36556f, transparent: true, opacity: 0.9 }}),
                floorGridMinor: new THREE.LineBasicMaterial({{ color: 0x8fa4af, transparent: true, opacity: 0.62, depthWrite: false }}),
                floorGridMajor: new THREE.LineBasicMaterial({{ color: 0x58799a, transparent: true, opacity: 0.88, depthWrite: false }}),
            }};

            function escapeHtml(value) {{
                return String(value ?? "")
                    .replaceAll("&", "&amp;")
                    .replaceAll("<", "&lt;")
                    .replaceAll(">", "&gt;")
                    .replaceAll('"', "&quot;");
            }}

            function setLayoutSaveStatus(message, tone = "muted") {{
                if (!layoutSaveStatus) return;
                layoutSaveStatus.textContent = message || "";
                layoutSaveStatus.title = message || "";
                layoutSaveStatus.style.color = tone === "error" ? "#9f3d3d" : tone === "ok" ? "#3d684f" : "#50645f";
            }}

            function resolveLayoutApiUrls(port) {{
                if (!port) return [];
                const urls = [`http://127.0.0.1:${{port}}/warehouse3d-layout`, `http://localhost:${{port}}/warehouse3d-layout`];
                try {{
                    const sourceUrl = document.referrer ? new URL(document.referrer) : new URL(window.location.href);
                    const hosts = [sourceUrl.hostname, window.location.hostname]
                        .filter(Boolean)
                        .filter((host, index, list) => list.indexOf(host) === index);
                    hosts.forEach(host => urls.push(`http://${{host}}:${{port}}/warehouse3d-layout`));
                    return urls.filter((url, index, list) => list.indexOf(url) === index);
                }} catch (error) {{
                    return urls;
                }}
            }}

            function scheduleServerLayoutSave(delay = 520) {{
                return;
            }}

            async function persistWarehouseLayoutToServer() {{
                if (layoutSaveInProgress) {{
                    setLayoutSaveStatus("Supabase 저장 대기", "muted");
                    return false;
                }}
                layoutSaveInProgress = true;
                setLayoutSaveStatus("Supabase 저장 중...", "muted");
                try {{
                    const payload = collectWarehouseLayoutBackup();
                    const requestId = `${{Date.now()}}-${{Math.random().toString(16).slice(2)}}`;
                    window.parent.postMessage({{
                        type: "warehouse3d:save_layout",
                        request_id: requestId,
                        requested_at: new Date().toISOString(),
                        payload,
                    }}, "*");
                    sharedLayoutStore = payload;
                    setLayoutSaveStatus("Supabase 저장 요청 전송됨", "ok");
                    return true;
                }} catch (error) {{
                    console.warn("Warehouse layout Supabase save failed", error);
                    setLayoutSaveStatus(`Supabase 저장 실패: ${{error?.message || error}}`, "error");
                    return false;
                }} finally {{
                    layoutSaveInProgress = false;
                }}
            }}

            function stableWarehouseHashId(prefix, ...parts) {{
                const text = parts.map(part => String(part ?? "").trim()).join("|");
                let hashA = 2166136261;
                let hashB = 0x9e3779b9;
                for (let index = 0; index < text.length; index += 1) {{
                    const code = text.charCodeAt(index);
                    hashA ^= code;
                    hashA = Math.imul(hashA, 16777619) >>> 0;
                    hashB = Math.imul(hashB ^ code, 2246822519) >>> 0;
                }}
                return `${{prefix}}_${{hashA.toString(16).padStart(8, "0")}}${{hashB.toString(16).padStart(8, "0")}}`;
            }}

            function layoutStableId(buildingName, floorName) {{
                return stableWarehouseHashId("wl", buildingName, floorName);
            }}

            function numberOrZero(value) {{
                const numberValue = Number(value);
                return Number.isFinite(numberValue) ? numberValue : 0;
            }}

            function integerOrZero(value) {{
                const numberValue = Number(value);
                return Number.isFinite(numberValue) ? Math.trunc(numberValue) : 0;
            }}

            function shelfNumber(item, fallback) {{
                const text = String(item?.part || item?.shelf || item?.shelf_no || "");
                const digits = text.replace(/[^0-9]/g, "");
                return Math.max(1, integerOrZero(digits || fallback || 1));
            }}

            function supabaseHeaders(prefer = "") {{
                const headers = {{
                    "apikey": supabaseBrowserConfig.key,
                    "Authorization": `Bearer ${{supabaseBrowserConfig.key}}`,
                    "Content-Type": "application/json",
                }};
                if (prefer) headers.Prefer = prefer;
                return headers;
            }}

            async function supabaseRest(path, options = {{}}) {{
                const response = await fetch(`${{supabaseBrowserConfig.url}}/rest/v1/${{path}}`, {{
                    ...options,
                    headers: {{ ...supabaseHeaders(options.prefer || ""), ...(options.headers || {{}}) }},
                }});
                if (!response.ok) {{
                    const detail = await response.text().catch(() => "");
                    throw new Error(`Supabase 요청 실패 (${{response.status}}) ${{detail}}`);
                }}
                return response;
            }}

            async function fetchExistingWarehouseLayoutIds() {{
                const response = await supabaseRest("warehouse_layouts?select=id,building,floor", {{ method: "GET" }});
                const rows = await response.json().catch(() => []);
                const ids = new Map();
                (Array.isArray(rows) ? rows : []).forEach(row => {{
                    if (row?.id && row?.building && row?.floor) ids.set(`${{row.building}}|${{row.floor}}`, row.id);
                }});
                return ids;
            }}

            function rackStableId(layoutId, rackCode) {{
                return stableWarehouseHashId("rack", layoutId, rackCode);
            }}

            function positionStableId(rackId, shelfNo, sku, itemName) {{
                return stableWarehouseHashId("pos", rackId, shelfNo, sku, itemName);
            }}

            function rackRowsFromPayload(payload, layoutIdByKey) {{
                const rows = [];
                Object.entries(payload?.locations || {{}}).forEach(([buildingName, floors]) => {{
                    Object.entries(floors || {{}}).forEach(([floorName, floorData]) => {{
                        const layoutId = layoutIdByKey.get(`${{buildingName}}|${{floorName}}`) || layoutStableId(buildingName, floorName);
                        (Array.isArray(floorData?.racks) ? floorData.racks : []).forEach((rack, index) => {{
                            const rackCode = String(rack?.id || rack?.rack_code || `R-${{String(index + 1).padStart(3, "0")}}`).trim();
                            if (!rackCode) return;
                            rows.push({{
                                id: rackStableId(layoutId, rackCode),
                                layout_id: layoutId,
                                rack_code: rackCode,
                                rack_name: String(rack?.name || rack?.label || rackCode).trim(),
                                x: numberOrZero(rack?.x),
                                y: numberOrZero(rack?.y),
                                z: numberOrZero(rack?.z),
                                rotation: numberOrZero(rack?.rotation),
                                width: numberOrZero(rack?.width ?? rack?.w),
                                depth: numberOrZero(rack?.depth ?? rack?.h),
                                height: numberOrZero(rack?.height ?? rack?.levels ?? 1),
                                shelf_count: Math.max(1, integerOrZero(rack?.shelf_count ?? rack?.levels ?? rack?.level_count ?? 1)),
                                rack_type: String(rack?.rack_type || rack?.type || "").trim(),
                                sort_order: index,
                                rack_data: rack,
                                updated_at: new Date().toISOString(),
                            }});
                        }});
                    }});
                }});
                return rows;
            }}

            function positionRowsFromPayload(payload, rackIdByKey, layoutIdByKey) {{
                const rows = [];
                Object.entries(payload?.locations || {{}}).forEach(([buildingName, floors]) => {{
                    Object.entries(floors || {{}}).forEach(([floorName, floorData]) => {{
                        const layoutId = layoutIdByKey.get(`${{buildingName}}|${{floorName}}`) || layoutStableId(buildingName, floorName);
                        (Array.isArray(floorData?.racks) ? floorData.racks : []).forEach((rack, rackIndex) => {{
                            const rackCode = String(rack?.id || rack?.rack_code || `R-${{String(rackIndex + 1).padStart(3, "0")}}`).trim();
                            const rackId = rackIdByKey.get(`${{layoutId}}|${{rackCode}}`) || rackStableId(layoutId, rackCode);
                            const aggregated = new Map();
                            (Array.isArray(rack?.items) ? rack.items : []).forEach((item, itemIndex) => {{
                                const shelfNo = shelfNumber(item, itemIndex + 1);
                                const sku = String(item?.sku || item?.barcode || item?.product_code || "").trim();
                                const itemName = String(item?.item_name || item?.product_name || item?.name || "").trim();
                                if (!sku && !itemName) return;
                                const key = `${{shelfNo}}|${{sku}}|${{itemName}}`;
                                const quantity = Math.max(0, integerOrZero(item?.quantity ?? item?.qty ?? item?.stock));
                                const existing = aggregated.get(key) || {{ quantity: 0, sort_order: itemIndex, position_data: item }};
                                existing.quantity += quantity;
                                aggregated.set(key, existing);
                            }});
                            aggregated.forEach((value, key) => {{
                                const [shelfNoText, sku, itemName] = key.split("|");
                                const shelfNo = Math.max(1, integerOrZero(shelfNoText));
                                rows.push({{
                                    id: positionStableId(rackId, shelfNo, sku, itemName),
                                    rack_id: rackId,
                                    shelf_no: shelfNo,
                                    sku,
                                    item_name: itemName,
                                    quantity: value.quantity,
                                    sort_order: value.sort_order,
                                    position_data: value.position_data,
                                    updated_at: new Date().toISOString(),
                                }});
                            }});
                        }});
                    }});
                }});
                return rows;
            }}

            async function deleteMissingSupabaseRows(tableName, parentColumn, parentId, idColumn, currentIds) {{
                const listResponse = await supabaseRest(`${{tableName}}?select=${{idColumn}}&${{parentColumn}}=eq.${{encodeURIComponent(parentId)}}`, {{ method: "GET" }});
                const existing = await listResponse.json().catch(() => []);
                const staleIds = (Array.isArray(existing) ? existing : [])
                    .map(row => row?.[idColumn])
                    .filter(Boolean)
                    .filter(id => !currentIds.has(id));
                if (!staleIds.length) return;
                await supabaseRest(`${{tableName}}?${{idColumn}}=in.(${{staleIds.map(encodeURIComponent).join(",")}})`, {{
                    method: "DELETE",
                    prefer: "return=minimal",
                }});
            }}

            async function persistWarehouseDetailsToSupabase(payload, layoutIdByKey) {{
                const rackRows = rackRowsFromPayload(payload, layoutIdByKey);
                const rackIdByKey = new Map(rackRows.map(row => [`${{row.layout_id}}|${{row.rack_code}}`, row.id]));
                const layoutIds = [...new Set([...layoutIdByKey.values()])];
                if (rackRows.length) {{
                    const rackResponse = await supabaseRest("warehouse_racks?on_conflict=layout_id,rack_code", {{
                        method: "POST",
                        prefer: "resolution=merge-duplicates,return=representation",
                        body: JSON.stringify(rackRows),
                    }});
                    const savedRacks = await rackResponse.json().catch(() => []);
                    if (!Array.isArray(savedRacks) || savedRacks.length < rackRows.length) {{
                        throw new Error("랙 저장 검증 실패: 저장 행 수가 맞지 않습니다.");
                    }}
                    savedRacks.forEach(row => {{
                        if (row?.layout_id && row?.rack_code && row?.id) rackIdByKey.set(`${{row.layout_id}}|${{row.rack_code}}`, row.id);
                    }});
                }}
                for (const layoutId of layoutIds) {{
                    const currentRackIds = new Set(rackRows.filter(row => row.layout_id === layoutId).map(row => row.id));
                    await deleteMissingSupabaseRows("warehouse_racks", "layout_id", layoutId, "id", currentRackIds);
                }}

                const positionRows = positionRowsFromPayload(payload, rackIdByKey, layoutIdByKey);
                if (positionRows.length) {{
                    const positionResponse = await supabaseRest("warehouse_inventory_positions?on_conflict=rack_id,shelf_no,sku,item_name", {{
                        method: "POST",
                        prefer: "resolution=merge-duplicates,return=representation",
                        body: JSON.stringify(positionRows),
                    }});
                    const savedPositions = await positionResponse.json().catch(() => []);
                    if (!Array.isArray(savedPositions) || savedPositions.length < positionRows.length) {{
                        throw new Error("재고 위치 저장 검증 실패: 저장 행 수가 맞지 않습니다.");
                    }}
                }}
                for (const rackId of rackIdByKey.values()) {{
                    const currentPositionIds = new Set(positionRows.filter(row => row.rack_id === rackId).map(row => row.id));
                    await deleteMissingSupabaseRows("warehouse_inventory_positions", "rack_id", rackId, "id", currentPositionIds);
                }}
            }}

            async function persistWarehouseLayoutToSupabase(payload) {{
                if (!supabaseBrowserConfig?.enabled) return false;
                const rows = [];
                Object.entries(payload?.locations || {{}}).forEach(([buildingName, floors]) => {{
                    Object.entries(floors || {{}}).forEach(([floorName, floorData]) => {{
                        if (!floorData || typeof floorData !== "object") return;
                        if (!Array.isArray(floorData.racks) && !Array.isArray(floorData.fixtures) && !floorData.floor_size) return;
                        rows.push({{
                            id: layoutStableId(buildingName, floorName),
                            building: buildingName,
                            floor: floorName,
                            layout_data: floorData,
                            is_active: true,
                            updated_at: new Date().toISOString(),
                        }});
                    }});
                }});
                if (!rows.length) return false;
                const existingLayoutIds = await fetchExistingWarehouseLayoutIds();
                rows.forEach(row => {{
                    const existingId = existingLayoutIds.get(`${{row.building}}|${{row.floor}}`);
                    if (existingId) row.id = existingId;
                }});
                const response = await fetch(`${{supabaseBrowserConfig.url}}/rest/v1/warehouse_layouts?on_conflict=building,floor`, {{
                    method: "POST",
                    headers: {{
                        "apikey": supabaseBrowserConfig.key,
                        "Authorization": `Bearer ${{supabaseBrowserConfig.key}}`,
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates,return=representation",
                    }},
                    body: JSON.stringify(rows),
                }});
                if (!response.ok) {{
                    const detail = await response.text().catch(() => "");
                    throw new Error(`Supabase 저장 실패 (${{response.status}}) ${{detail}}`);
                }}
                const savedRows = await response.json().catch(() => []);
                if (!Array.isArray(savedRows) || savedRows.length < rows.length) {{
                    throw new Error("Supabase 저장 검증 실패: 저장 행 수가 맞지 않습니다.");
                }}
                const layoutIdByKey = new Map();
                rows.forEach(row => layoutIdByKey.set(`${{row.building}}|${{row.floor}}`, row.id));
                savedRows.forEach(row => {{
                    if (row?.building && row?.floor && row?.id) layoutIdByKey.set(`${{row.building}}|${{row.floor}}`, row.id);
                }});
                await persistWarehouseDetailsToSupabase(payload, layoutIdByKey);
                return true;
            }}

            async function persistWarehouseLayoutToLocalApi(payload, throwOnFailure = false) {{
                if (!layoutApiUrls.length) return false;
                let lastError = null;
                for (const apiUrl of layoutApiUrls) {{
                    try {{
                        const response = await fetch(apiUrl, {{
                            method: "POST",
                            headers: {{ "Content-Type": "application/json" }},
                            body: JSON.stringify(payload),
                        }});
                        if (!response.ok) {{
                            throw new Error(`저장 요청 실패 (${{response.status}})`);
                        }}
                        return true;
                    }} catch (error) {{
                        lastError = error;
                    }}
                }}
                if (throwOnFailure) throw lastError || new Error("저장 API 연결 실패");
                return false;
            }}

            function storageKeyFor(floorName) {{
                return storageKeyForLocation(activeBuilding, floorName);
            }}

            function fixtureStorageKeyFor(floorName) {{
                return fixtureStorageKeyForLocation(activeBuilding, floorName);
            }}

            function floorSizeStorageKeyFor(floorName) {{
                return floorSizeStorageKeyForLocation(activeBuilding, floorName);
            }}

            function storageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:${{floorName}}`;
            }}

            function fixtureStorageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:fixtures:${{floorName}}`;
            }}

            function floorSizeStorageKeyForLocation(buildingName, floorName) {{
                return `warehouseRackLayout:${{buildingName}}:floorSize:${{floorName}}`;
            }}

            function uniqueKeys(keys) {{
                return keys.filter(Boolean).filter((key, index, list) => list.indexOf(key) === index);
            }}

            function layoutStorageKeyCandidates(buildingName, floorName) {{
                return uniqueKeys([
                    storageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}${{floorName}}` : "",
                ]);
            }}

            function fixtureStorageKeyCandidates(buildingName, floorName) {{
                return uniqueKeys([
                    fixtureStorageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:fixtures:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}fixtures:${{floorName}}` : "",
                ]);
            }}

            function floorSizeStorageKeyCandidates(buildingName, floorName) {{
                return uniqueKeys([
                    floorSizeStorageKeyForLocation(buildingName, floorName),
                    `${{baseStorageKey}}${{buildingName}}:floorSize:${{floorName}}`,
                    buildingName === activeBuilding ? `${{baseStorageKey}}floorSize:${{floorName}}` : "",
                ]);
            }}

            function sharedFloorData(buildingName, floorName) {{
                return sharedLayoutStore?.locations?.[buildingName]?.[floorName] || null;
            }}

            function readJsonFromLocalStorage(key, fallback = null) {{
                try {{
                    const value = JSON.parse(localStorage.getItem(key) || "null");
                    return value ?? fallback;
                }} catch (error) {{
                    return fallback;
                }}
            }}

            function readJsonFromLocalStorageKeys(keys, fallback = null) {{
                for (const key of keys) {{
                    const value = readJsonFromLocalStorage(key, null);
                    if (value !== null) return value;
                }}
                return fallback;
            }}

            function writeJsonToLocalStorage(key, value) {{
                localStorage.setItem(key, JSON.stringify(value));
            }}

            function hasSharedFloorData(buildingName, floorName) {{
                const shared = sharedFloorData(buildingName, floorName);
                return Boolean(
                    Array.isArray(shared?.racks) ||
                    Array.isArray(shared?.fixtures) ||
                    (shared?.floor_size && Number.isFinite(Number(shared.floor_size.width)) && Number.isFinite(Number(shared.floor_size.depth)))
                );
            }}

            function hasBrowserStoredFloorData(buildingName, floorName) {{
                const browserRacks = readJsonFromLocalStorageKeys(layoutStorageKeyCandidates(buildingName, floorName), null);
                const browserFixtures = readJsonFromLocalStorageKeys(fixtureStorageKeyCandidates(buildingName, floorName), null);
                const browserFloorSize = readJsonFromLocalStorageKeys(floorSizeStorageKeyCandidates(buildingName, floorName), null);
                return Boolean(
                    Array.isArray(browserRacks) ||
                    Array.isArray(browserFixtures) ||
                    (browserFloorSize && Number.isFinite(Number(browserFloorSize.width)) && Number.isFinite(Number(browserFloorSize.depth)))
                );
            }}

            function shouldMigrateBrowserLayoutToDatabase() {{
                return !hasSharedFloorData(activeBuilding, activeFloor) && hasBrowserStoredFloorData(activeBuilding, activeFloor);
            }}

            function hydrateBrowserLayoutFromSharedStore() {{
                locationFloors.forEach(option => {{
                    const shared = sharedFloorData(option.building, option.floor);
                    if (!shared || typeof shared !== "object") return;
                    if (Array.isArray(shared.racks)) {{
                        writeJsonToLocalStorage(storageKeyForLocation(option.building, option.floor), normalizeRackIds(shared.racks));
                    }}
                    if (Array.isArray(shared.fixtures)) {{
                        writeJsonToLocalStorage(fixtureStorageKeyForLocation(option.building, option.floor), shared.fixtures);
                    }}
                    if (shared.floor_size && Number.isFinite(Number(shared.floor_size.width)) && Number.isFinite(Number(shared.floor_size.depth))) {{
                        writeJsonToLocalStorage(floorSizeStorageKeyForLocation(option.building, option.floor), shared.floor_size);
                    }}
                }});
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
                const sharedSize = sharedFloorData(activeBuilding, floorName)?.floor_size;
                if (sharedSize && Number.isFinite(Number(sharedSize.width)) && Number.isFinite(Number(sharedSize.depth))) {{
                    return {{
                        width: clamp(Number(sharedSize.width), base.width * 0.45, base.width * 2.6),
                        depth: clamp(Number(sharedSize.depth), base.depth * 0.45, base.depth * 2.6),
                        x: Number.isFinite(Number(sharedSize.x)) ? Number(sharedSize.x) : 0,
                        z: Number.isFinite(Number(sharedSize.z)) ? Number(sharedSize.z) : 0,
                    }};
                }}
                try {{
                    const saved = readJsonFromLocalStorageKeys(floorSizeStorageKeyCandidates(activeBuilding, floorName), null);
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
                writeJsonToLocalStorage(floorSizeStorageKeyFor(floorName), size);
                scheduleServerLayoutSave();
            }}

            function currentFloorSize() {{
                return loadFloorSize(activeFloor);
            }}

            function layoutFloorSize() {{
                return currentFloorSize();
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
                floorSizeStorageKeyCandidates(activeBuilding, activeFloor).forEach(key => localStorage.removeItem(key));
                scheduleServerLayoutSave();
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
                const shared = sharedFloorData(activeBuilding, floorName)?.racks;
                if (Array.isArray(shared)) return normalizeRackIds(shared);
                try {{
                    const saved = readJsonFromLocalStorageKeys(layoutStorageKeyCandidates(activeBuilding, floorName), null);
                    if (Array.isArray(saved)) return normalizeRackIds(saved);
                }} catch (error) {{}}
                return normalizeRackIds(defaultLayout(floorName));
            }}

            function saveLayout() {{
                racks = normalizeRackIds(racks);
                writeJsonToLocalStorage(storageKeyFor(activeFloor), racks);
                scheduleServerLayoutSave();
            }}

            function saveLayoutFor(floorName, floorRacks) {{
                writeJsonToLocalStorage(storageKeyFor(floorName), normalizeRackIds(floorRacks));
                scheduleServerLayoutSave();
            }}

            function loadFixtures(floorName) {{
                const shared = sharedFloorData(activeBuilding, floorName)?.fixtures;
                if (Array.isArray(shared)) return shared.map(normalizeFixture);
                try {{
                    const saved = readJsonFromLocalStorageKeys(fixtureStorageKeyCandidates(activeBuilding, floorName), null);
                    if (Array.isArray(saved)) return saved.map(normalizeFixture);
                }} catch (error) {{}}
                return [];
            }}

            function saveFixtures() {{
                writeJsonToLocalStorage(fixtureStorageKeyFor(activeFloor), fixtures);
                scheduleServerLayoutSave();
            }}

            function saveFixturesFor(floorName, floorFixtures) {{
                writeJsonToLocalStorage(fixtureStorageKeyFor(floorName), floorFixtures);
                scheduleServerLayoutSave();
            }}

            function collectWarehouseLayoutBackup() {{
                saveLayout();
                saveFixtures();
                const locations = {{}};
                locationFloors.forEach(option => {{
                    const buildingName = option.building;
                    const floorName = option.floor;
                    const racksValue = readJsonFromLocalStorageKeys(layoutStorageKeyCandidates(buildingName, floorName), null);
                    const fixturesValue = readJsonFromLocalStorageKeys(fixtureStorageKeyCandidates(buildingName, floorName), null);
                    const floorSizeValue = readJsonFromLocalStorageKeys(floorSizeStorageKeyCandidates(buildingName, floorName), null);
                    const shared = sharedFloorData(buildingName, floorName) || {{}};
                    const floorData = {{}};
                    if (Array.isArray(racksValue)) {{
                        floorData.racks = normalizeRackIds(racksValue);
                    }} else if (Array.isArray(shared.racks)) {{
                        floorData.racks = normalizeRackIds(shared.racks);
                    }}
                    if (Array.isArray(fixturesValue)) {{
                        floorData.fixtures = fixturesValue;
                    }} else if (Array.isArray(shared.fixtures)) {{
                        floorData.fixtures = shared.fixtures;
                    }}
                    if (floorSizeValue && Number.isFinite(Number(floorSizeValue.width)) && Number.isFinite(Number(floorSizeValue.depth))) {{
                        floorData.floor_size = floorSizeValue;
                    }} else if (shared.floor_size && Number.isFinite(Number(shared.floor_size.width)) && Number.isFinite(Number(shared.floor_size.depth))) {{
                        floorData.floor_size = shared.floor_size;
                    }}
                    if (Object.keys(floorData).length) {{
                        locations[buildingName] = locations[buildingName] || {{}};
                        locations[buildingName][floorName] = floorData;
                    }}
                }});
                return {{
                    version: 1,
                    exported_at: new Date().toISOString(),
                    locations,
                }};
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
                    x: Number(size.x || 0) + (Number(position.x || 0) - 50) * size.width / 100,
                    z: Number(size.z || 0) + (Number(position.y || 0) - 50) * size.depth / 100,
                    w: Math.max(1.8, Number(rack.w || 10.8) * size.width / 100),
                    d: Math.max(1.4, Number(rack.h || 8.4) * size.depth / 100),
                }};
            }}

            function worldToRack(x, z) {{
                return worldToPercent(x, z, false);
            }}

            function worldToPercent(x, z, allowOutside = false) {{
                const size = layoutFloorSize();
                const min = allowOutside ? -24 : 0;
                const max = allowOutside ? 124 : 100;
                return {{
                    x: clamp((x - Number(size.x || 0)) / Math.max(1, size.width) * 100 + 50, min, max),
                    y: clamp((z - Number(size.z || 0)) / Math.max(1, size.depth) * 100 + 50, min, max),
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

            function rackEdgeExtentsPercent(rack) {{
                const size = layoutFloorSize();
                const rotation = Math.abs(Number(rack?.rotation || 0)) % 180;
                const rawW = Math.max(1, Number(rack?.w || 10.8));
                const rawH = Math.max(1, Number(rack?.h || 8.4));
                if (rotation === 90) {{
                    return {{
                        halfX: rawH * Math.max(1, size.depth) / Math.max(1, size.width) / 2,
                        halfY: rawW * Math.max(1, size.width) / Math.max(1, size.depth) / 2,
                    }};
                }}
                return {{ halfX: rawW / 2, halfY: rawH / 2 }};
            }}

            function fixtureEdgeExtentsPercent(fixture) {{
                const size = layoutFloorSize();
                const rotation = Math.abs(Number(fixture?.rotation || 0)) % 180;
                const rawW = Math.max(0.1, Number(fixture?.w || fixtureDefaults[fixture?.type]?.w || 1));
                const rawD = Math.max(0.1, Number(fixture?.d || fixtureDefaults[fixture?.type]?.d || 1));
                const halfX = (rotation === 90 ? rawD : rawW) / Math.max(1, size.width) * 50;
                const halfY = (rotation === 90 ? rawW : rawD) / Math.max(1, size.depth) * 50;
                return {{ halfX, halfY }};
            }}

            function snapObjectEdges(position, extents, allowOutside = false) {{
                const thresholdX = 2.8;
                const thresholdY = 4.2;
                const minCenterX = allowOutside ? -24 : extents.halfX;
                const maxCenterX = allowOutside ? 124 : 100 - extents.halfX;
                const minCenterY = allowOutside ? -24 : extents.halfY;
                const maxCenterY = allowOutside ? 124 : 100 - extents.halfY;
                let x = clamp(position.x, minCenterX, maxCenterX);
                let y = clamp(position.y, minCenterY, maxCenterY);
                if (!allowOutside) {{
                    if (Math.abs(x - extents.halfX) <= thresholdX) x = extents.halfX;
                    if (Math.abs(x - (100 - extents.halfX)) <= thresholdX) x = 100 - extents.halfX;
                    if (Math.abs(y - extents.halfY) <= thresholdY) y = extents.halfY;
                    if (Math.abs(y - (100 - extents.halfY)) <= thresholdY) y = 100 - extents.halfY;
                }}
                x = snapValue(x, [minCenterX, 50, maxCenterX], thresholdX);
                y = snapValue(y, [minCenterY, 50, maxCenterY], thresholdY);
                return {{ x, y }};
            }}

            function snapRackPercentPosition(rack, position) {{
                return snapObjectEdges(position, rackEdgeExtentsPercent(rack), false);
            }}

            function snapFixturePercentPosition(fixture, position, allowOutside = false) {{
                return snapObjectEdges(position, fixtureEdgeExtentsPercent(fixture), allowOutside);
            }}

            function fixtureToWorld(fixture) {{
                const size = layoutFloorSize();
                return {{
                    x: Number(size.x || 0) + (Number(fixture.x || 0) - 50) * size.width / 100,
                    z: Number(size.z || 0) + (Number(fixture.y || 0) - 50) * size.depth / 100,
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

            function makeLabel(text, position, scale = 1, emphasis = false) {{
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
                const labelCanvas = document.createElement("canvas");
                const pixelRatio = emphasis ? 3 : 2.2;
                const baseFontSize = lines.length > 1 ? 26 : 32;
                const measureCtx = labelCanvas.getContext("2d");
                measureCtx.font = `900 ${{baseFontSize}}px Pretendard, Arial, sans-serif`;
                const measuredTextWidth = Math.max(...lines.map(line => measureCtx.measureText(line).width), 1);
                const horizontalPadding = emphasis ? 44 : 38;
                const logicalWidth = Math.min(620, Math.max(104, Math.ceil(measuredTextWidth + horizontalPadding)));
                const logicalHeight = lines.length > 1 ? 116 : 78;
                labelCanvas.width = Math.round(logicalWidth * pixelRatio);
                labelCanvas.height = Math.round(logicalHeight * pixelRatio);
                const ctx = labelCanvas.getContext("2d");
                ctx.scale(pixelRatio, pixelRatio);
                ctx.clearRect(0, 0, logicalWidth, logicalHeight);
                ctx.fillStyle = emphasis ? "rgba(232, 194, 122, 0.98)" : "rgba(250, 248, 244, 0.98)";
                ctx.strokeStyle = emphasis ? "rgba(96, 72, 38, 0.96)" : "rgba(31, 48, 64, 0.92)";
                ctx.lineWidth = emphasis ? 8 : 7;
                ctx.shadowColor = emphasis ? "rgba(82, 58, 30, 0.28)" : "rgba(15, 23, 42, 0.24)";
                ctx.shadowBlur = emphasis ? 12 : 12;
                ctx.shadowOffsetY = emphasis ? 5 : 5;
                if (ctx.roundRect) {{
                    ctx.roundRect(10, 14, logicalWidth - 20, logicalHeight - 28, 12);
                }} else {{
                    ctx.rect(10, 14, logicalWidth - 20, logicalHeight - 28);
                }}
                ctx.fill();
                ctx.shadowColor = "transparent";
                ctx.shadowBlur = 0;
                ctx.shadowOffsetY = 0;
                ctx.stroke();
                ctx.fillStyle = emphasis ? "#24303c" : "#0f172a";
                ctx.textAlign = "center";
                ctx.textBaseline = "middle";
                let fontSize = baseFontSize;
                ctx.font = `900 ${{fontSize}}px Pretendard, Arial, sans-serif`;
                while (fontSize > 18 && lines.some(line => ctx.measureText(line).width > logicalWidth - 42)) {{
                    fontSize -= 2;
                    ctx.font = `900 ${{fontSize}}px Pretendard, Arial, sans-serif`;
                }}
                const lineHeight = fontSize * 1.22;
                const startY = logicalHeight / 2 - ((lines.length - 1) * lineHeight) / 2;
                ctx.lineJoin = "round";
                ctx.strokeStyle = emphasis ? "rgba(255, 250, 240, 0.62)" : "rgba(255, 255, 255, 0.82)";
                ctx.lineWidth = emphasis ? 4 : 3;
                lines.forEach((line, index) => {{
                    const textY = startY + index * lineHeight;
                    ctx.strokeText(line, logicalWidth / 2, textY);
                    ctx.fillText(line, logicalWidth / 2, textY);
                }});
                const texture = new THREE.CanvasTexture(labelCanvas);
                texture.minFilter = THREE.LinearFilter;
                texture.magFilter = THREE.LinearFilter;
                texture.anisotropy = renderer?.capabilities?.getMaxAnisotropy?.() || 1;
                texture.needsUpdate = true;
                const sprite = new THREE.Sprite(new THREE.SpriteMaterial({{ map: texture, transparent: true, depthTest: false }}));
                sprite.renderOrder = emphasis ? 30 : 20;
                sprite.position.copy(position);
                const emphasisScale = emphasis ? 1.08 : 1;
                sprite.scale.set(Math.max(3.0, logicalWidth / 82) * scale * emphasisScale, Math.max(1.08, logicalHeight / 82) * scale * emphasisScale, 1);
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
                const palette = [0x6f927d, 0x58799a, 0xb78b5a, 0x8c99a6, 0x94a3b8, 0xb66a6a];
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
                const color = Number(fixture.color || fixtureDefaults[fixture.type]?.color || 0x58799a);
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
                    const lineMaterial = new THREE.MeshStandardMaterial({{ color: 0x58799a, emissive: 0x1f3445, roughness: 0.38, metalness: 0.1 }});
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
                    group.add(makeLabel(labelText, new THREE.Vector3(0, visualHeight + 0.46, 0), 0.52, fixture.id === selectedFixtureId));
                }}

                const hitHeight = Math.max(0.5, visualHeight);
                const hitbox = rackPart(world.w, hitHeight, world.d, materials.hitbox, 0, hitHeight / 2, 0);
                hitbox.userData.fixtureId = fixture.id;
                group.add(hitbox);
                group.userData.hitbox = hitbox;

                if (fixture.locked) {{
                    group.add(rackPart(Math.min(0.82, world.w * 0.42), 0.1, 0.22, materials.locked, 0, visualHeight + 0.18, 0));
                    if (shouldShowFixtureLabel) {{
                        group.add(makeLabel("고정", new THREE.Vector3(0, visualHeight + 0.42, 0), 0.32));
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
                    group.add(makeLabel(rackLabelText(rack), new THREE.Vector3(0, rackHeight + 0.58, halfD + 0.08), 0.58, rack.id === selectedRackId && !selectedRackItemKey));
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
                                group.add(makeLabel(shortLabel(item.name, 10), new THREE.Vector3(x, y + (stackCount - 1) * layerStep + 0.08 + layerBoxH * palletBoxLevels + 0.34, z), 0.32, itemKey === selectedRackItemKey));
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
                                group.add(makeLabel(shortLabel(item.name, 10), new THREE.Vector3(x, y + boxH + 0.26, z), 0.27, itemKey === selectedRackItemKey));
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

            function makeFloorCellGrid(width, depth, centerX, centerZ) {{
                const group = new THREE.Group();
                const y = 0.105;
                const left = centerX - width / 2;
                const right = centerX + width / 2;
                const top = centerZ - depth / 2;
                const bottom = centerZ + depth / 2;
                const minor = [];
                const major = [];
                const pushLine = (target, x1, z1, x2, z2) => {{
                    target.push(x1, y, z1, x2, y, z2);
                }};

                for (let i = 0; i <= Math.floor(width); i += 1) {{
                    const x = left + i;
                    pushLine(i % 5 === 0 ? major : minor, x, top, x, bottom);
                }}
                if (right - (left + Math.floor(width)) > 0.04) {{
                    pushLine(major, right, top, right, bottom);
                }}

                for (let i = 0; i <= Math.floor(depth); i += 1) {{
                    const z = top + i;
                    pushLine(i % 5 === 0 ? major : minor, left, z, right, z);
                }}
                if (bottom - (top + Math.floor(depth)) > 0.04) {{
                    pushLine(major, left, bottom, right, bottom);
                }}

                const addLines = (positions, material, renderOrder) => {{
                    if (!positions.length) return;
                    const geometry = new THREE.BufferGeometry();
                    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
                    const lines = new THREE.LineSegments(geometry, material);
                    lines.renderOrder = renderOrder;
                    group.add(lines);
                }};

                addLines(minor, materials.floorGridMinor, 4);
                addLines(major, materials.floorGridMajor, 5);
                return group;
            }}

            function buildWarehouseModel() {{
                clearGroup(buildingGroup);
                floorResizeHandles.length = 0;
                const size = currentFloorSize();
                const length = size.width;
                const depth = size.depth;
                const centerX = Number(size.x || 0);
                const centerZ = Number(size.z || 0);
                const floorThickness = 0.16;

                const slab = makeBox(length, floorThickness, depth, materials.activeSlab, new THREE.Vector3(centerX, 0, centerZ));
                buildingGroup.add(slab);
                buildingGroup.add(makeFloorCellGrid(length, depth, centerX, centerZ));
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
                        : '<tr><td colspan="6" class="empty">시설물은 선택 후 바로 드래그해서 위치를 옮기고, 시설물 배치 도구에서 회전/삭제할 수 있습니다.</td></tr>';
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
                const nextPosition = snapRackPercentPosition(resizingRack, {{
                    x: resizeState.start.x + shiftWorldX / Math.max(1, size.width) * 100,
                    y: resizeState.start.y + shiftWorldZ / Math.max(1, size.depth) * 100,
                }});
                resizingRack.x = nextPosition.x;
                resizingRack.y = nextPosition.y;
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
                const nextPosition = snapFixturePercentPosition(
                    resizingFixture,
                    worldToPercent(resizeState.startWorld.x + shiftWorldX, resizeState.startWorld.z + shiftWorldZ, allowOutside),
                    allowOutside
                );
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
                    if (pickRackItem(event)) {{
                        canvas.style.cursor = "pointer";
                        return;
                    }}
                    canvas.style.cursor = pickRack(event) || pickFixture(event) ? "grab" : "default";
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
                    const next = snapRackPercentPosition(
                        draggingRack,
                        worldToRack(dragPoint.x + dragOffset.x, dragPoint.z + dragOffset.z)
                    );
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
                    const next = snapFixturePercentPosition(
                        draggingFixture,
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
                const allowOutside = fixtureAllowsOutside(fixture.type);
                const next = snapFixturePercentPosition(fixture, {{ x: fixture.x, y: fixture.y }}, allowOutside);
                fixture.x = next.x;
                fixture.y = next.y;
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
                const next = snapRackPercentPosition(rack, {{ x: rack.x, y: rack.y }});
                rack.x = next.x;
                rack.y = next.y;
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
                const current = {{ x: Number(rack.x || 50), y: Number(rack.y || 50) }};
                if (direction === "left") current.x -= step;
                if (direction === "right") current.x += step;
                if (direction === "up") current.y -= step;
                if (direction === "down") current.y += step;
                const next = snapRackPercentPosition(rack, current);
                rack.x = next.x;
                rack.y = next.y;
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
                const next = snapFixturePercentPosition(fixture, current, allowOutside);
                fixture.x = next.x;
                fixture.y = next.y;
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
                event.stopPropagation();
                if (typeof event.stopImmediatePropagation === "function") event.stopImmediatePropagation();
                if (draggingRack || draggingFixture || resizingRack || resizingFixture || resizingFloor) return;
                const direction = event.deltaY < 0 ? 1 : -1;
                const wheelStep = event.shiftKey ? zoomStep * 2 : zoomStep;
                setZoom(zoomLevel + direction * wheelStep);
                controls.update();
                renderer.render(scene, camera);
            }}, {{ passive: false, capture: true }});

            function warehousePrintBounds() {{
                const bounds = new THREE.Box3();
                const itemBounds = new THREE.Box3();
                [buildingGroup, fixtureGroup, rackGroup].forEach(group => {{
                    group.traverse(object => {{
                        if (!(object.isMesh || object.isLine || object.isLineSegments)) return;
                        itemBounds.setFromObject(object);
                        if (!itemBounds.isEmpty()) bounds.union(itemBounds);
                    }});
                }});
                if (bounds.isEmpty()) {{
                    const size = currentFloorSize();
                    const centerX = Number(size.x || 0);
                    const centerZ = Number(size.z || 0);
                    bounds.set(
                        new THREE.Vector3(centerX - size.width / 2, 0, centerZ - size.depth / 2),
                        new THREE.Vector3(centerX + size.width / 2, 3, centerZ + size.depth / 2)
                    );
                }}
                bounds.expandByScalar(0.35);
                return bounds;
            }}

            function projectedBoundsForPrint(bounds) {{
                const corners = [
                    new THREE.Vector3(bounds.min.x, bounds.min.y, bounds.min.z),
                    new THREE.Vector3(bounds.min.x, bounds.min.y, bounds.max.z),
                    new THREE.Vector3(bounds.min.x, bounds.max.y, bounds.min.z),
                    new THREE.Vector3(bounds.min.x, bounds.max.y, bounds.max.z),
                    new THREE.Vector3(bounds.max.x, bounds.min.y, bounds.min.z),
                    new THREE.Vector3(bounds.max.x, bounds.min.y, bounds.max.z),
                    new THREE.Vector3(bounds.max.x, bounds.max.y, bounds.min.z),
                    new THREE.Vector3(bounds.max.x, bounds.max.y, bounds.max.z),
                ];
                camera.updateMatrixWorld(true);
                camera.updateProjectionMatrix();
                const projected = corners.map(point => point.clone().project(camera)).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
                if (!projected.length) return null;
                const minX = Math.min(...projected.map(point => point.x));
                const maxX = Math.max(...projected.map(point => point.x));
                const minY = Math.min(...projected.map(point => point.y));
                const maxY = Math.max(...projected.map(point => point.y));
                return {{
                    minX,
                    maxX,
                    minY,
                    maxY,
                    width: Math.max(0.01, maxX - minX),
                    height: Math.max(0.01, maxY - minY),
                }};
            }}

            function frameWarehouseForPrint(width, height) {{
                const bounds = warehousePrintBounds();
                const center = new THREE.Vector3();
                const sphere = new THREE.Sphere();
                bounds.getCenter(center);
                bounds.getBoundingSphere(sphere);

                const viewDirection = new THREE.Vector3().subVectors(camera.position, controls.target);
                if (viewDirection.lengthSq() < 0.0001) viewDirection.set(26, 15, 30);
                viewDirection.normalize();

                const aspect = width / Math.max(1, height);
                const fov = THREE.MathUtils.degToRad(camera.fov);
                const fitDistance = sphere.radius / Math.tan(fov / 2);
                const distance = Math.max(26, fitDistance * 0.96);

                controls.target.copy(center);
                camera.position.copy(center).addScaledVector(viewDirection, distance);
                camera.aspect = aspect;
                camera.zoom = 1;
                camera.near = 0.1;
                camera.far = Math.max(220, distance + sphere.radius * 4);
                camera.updateProjectionMatrix();
                camera.updateMatrixWorld(true);
                const projected = projectedBoundsForPrint(bounds);
                if (projected) {{
                    const targetWidth = 1.92;
                    const targetHeight = 1.84;
                    const fitZoom = Math.min(targetWidth / projected.width, targetHeight / projected.height);
                    camera.zoom = clamp(fitZoom, 1.0, 5.5);
                    camera.updateProjectionMatrix();
                }}
                controls.update();
                return bounds;
            }}

            function croppedWarehousePrintImage(width, height, bounds) {{
                const projected = projectedBoundsForPrint(bounds);
                if (!projected) return canvas.toDataURL("image/png");
                const padX = Math.round(width * 0.01);
                const padY = Math.round(height * 0.012);
                const left = Math.max(0, Math.floor(((Math.max(-1, projected.minX) + 1) / 2) * width) - padX);
                const right = Math.min(width, Math.ceil(((Math.min(1, projected.maxX) + 1) / 2) * width) + padX);
                const top = Math.max(0, Math.floor(((1 - Math.min(1, projected.maxY)) / 2) * height) - padY);
                const bottom = Math.min(height, Math.ceil(((1 - Math.max(-1, projected.minY)) / 2) * height) + padY);
                const cropWidth = Math.max(1, right - left);
                const cropHeight = Math.max(1, bottom - top);
                const output = document.createElement("canvas");
                output.width = cropWidth;
                output.height = cropHeight;
                const context = output.getContext("2d");
                context.drawImage(canvas, left, top, cropWidth, cropHeight, 0, 0, cropWidth, cropHeight);
                return output.toDataURL("image/png");
            }}

            function captureWarehousePrintImage() {{
                const width = 3508;
                const height = 2480;
                const previousPixelRatio = renderer.getPixelRatio();
                const previousBackground = scene.background;
                const previousFog = scene.fog;
                const previousCameraPosition = camera.position.clone();
                const previousCameraAspect = camera.aspect;
                const previousCameraZoom = camera.zoom;
                const previousCameraNear = camera.near;
                const previousCameraFar = camera.far;
                const previousTarget = controls.target.clone();

                renderer.setPixelRatio(1);
                renderer.setSize(width, height, false);
                const printBounds = frameWarehouseForPrint(width, height);
                scene.background = new THREE.Color(0xffffff);
                scene.fog = null;
                renderer.render(scene, camera);
                const imageUrl = croppedWarehousePrintImage(width, height, printBounds);

                scene.background = previousBackground;
                scene.fog = previousFog;
                camera.position.copy(previousCameraPosition);
                camera.aspect = previousCameraAspect;
                camera.zoom = previousCameraZoom;
                camera.near = previousCameraNear;
                camera.far = previousCameraFar;
                camera.updateProjectionMatrix();
                controls.target.copy(previousTarget);
                controls.update();
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
                            @page {{ size: A4 landscape; margin: 4mm; }}
                            * {{ box-sizing: border-box; }}
                            html,
                            body {{
                                background: #ffffff;
                                color: #10201d;
                                font-family: "Malgun Gothic", Arial, sans-serif;
                                height: 202mm;
                                margin: 0;
                                overflow: hidden;
                                width: 289mm;
                            }}
                            .print-shell {{
                                display: grid;
                                gap: 1.5mm;
                                grid-template-rows: auto minmax(0, 1fr) auto;
                                height: 202mm;
                                overflow: hidden;
                            }}
                            header {{
                                align-items: flex-end;
                                border-bottom: 1px solid #9db5ae;
                                display: flex;
                                justify-content: space-between;
                                padding-bottom: 1mm;
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
            saveLayoutFileButton?.addEventListener("click", () => {{
                persistWarehouseLayoutToServer();
            }});
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
            if (shouldMigrateBrowserLayoutToDatabase()) {{
                scheduleServerLayoutSave(900);
            }} else if (hasSharedFloorData(activeBuilding, activeFloor)) {{
                setLayoutSaveStatus("Supabase 배치 불러옴", "ok");
            }}
            animate();
            window.addEventListener("resize", resizeRenderer);
            }} catch (error) {{
                console.error("Warehouse 3D startup failed", error);
                const modelError = document.getElementById("modelError");
                if (modelError) {{
                    modelError.innerHTML = `3D 초기화 오류<br><small>${{escapeWarehouse3dError(error?.message || error)}}</small>`;
                    modelError.style.display = "flex";
                }}
            }}
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
            color: #172033;
            font-size: 1.34rem;
            font-weight: 900;
            letter-spacing: 0;
            margin: 0 0 0.62rem;
        }
        .warehouse3d-kpi-grid {
            display: grid;
            gap: 0.62rem;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            margin: 0.46rem 0 0.54rem;
        }
        .warehouse3d-kpi {
            background: #F2EFEA;
            border: 1px solid #D8D2C8;
            border-radius: 12px;
            min-height: 58px;
            padding: 0.58rem 0.68rem;
        }
        .warehouse3d-kpi span {
            color: #64748B;
            display: block;
            font-size: 0.72rem;
            font-weight: 900;
            margin-bottom: 0.28rem;
        }
        .warehouse3d-kpi strong {
            color: #1F2933;
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
