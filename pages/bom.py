from __future__ import annotations

from html import escape
from io import BytesIO

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy import delete, select

try:
    from backend.database import SessionLocal, init_db
    from backend.models import CategoryBomItem
except (ModuleNotFoundError, RuntimeError) as exc:
    SessionLocal = None
    init_db = None
    CategoryBomItem = None
    BOM_IMPORT_ERROR = str(exc)
else:
    BOM_IMPORT_ERROR = ""


COST_COLUMN = "제품 원가 / 입고가"
BARCODE_COLUMN = "88바코드"
SPEC_COLUMN = "규격"
LEGACY_BARCODE_SPEC_COLUMN = "88바코드/규격"
BOM_COLUMNS = ["상품명", "유형", "담당자", "거래처", "필요 재고", BARCODE_COLUMN, SPEC_COLUMN, COST_COLUMN]
EDITOR_COLUMNS = ["삭제", *BOM_COLUMNS]
EDITOR_DISPLAY_COLUMNS = ["표시", *EDITOR_COLUMNS]
ITEM_TYPES = ["완제품", "본품", "부품", "부속품", "원자재", "포장재", "인쇄물", "박스", "사은품", "타사소싱", "기타"]
GROUP_ITEM_TYPES = {"완제품"}
DEFAULT_CATEGORY = "와이어 주방용품"
DEFAULT_MANAGER = "송광선 대리"
DEFAULT_VENDOR = "케이리빙"
IMPORT_HEADER_ALIASES = {
    "상품명": ["상품명", "제품명", "품목명", "자재명"],
    "유형": ["유형", "구분", "분류", "자재유형"],
    "담당자": ["담당자", "담당", "관리자"],
    "거래처": ["거래처", "공급처", "업체", "vendor"],
    "필요 재고": ["필요재고", "필요 재고", "필요수량", "수량", "소요량", "requiredstock"],
    BARCODE_COLUMN: ["88바코드", "바코드", "옵션바코드", "barcode"],
    SPEC_COLUMN: ["규격", "사양", "스펙", "spec"],
    LEGACY_BARCODE_SPEC_COLUMN: ["88바코드/규격", "바코드/규격", "barcode/spec"],
    COST_COLUMN: ["제품 원가 / 입고가", "제품원가", "입고가", "원가", "비고", "메모", "memo"],
}


def render_bom_page() -> None:
    inject_bom_css()
    st.markdown('<div class="bom-title">카테고리 BOM 관리</div>', unsafe_allow_html=True)

    if not bom_available():
        st.error(BOM_IMPORT_ERROR or "BOM DB를 초기화하지 못했습니다.")
        return

    categories = fetch_categories()
    category = render_category_controls(categories)
    draft_key = f"bom_editor_draft_{category}"

    if draft_key not in st.session_state:
        st.session_state[draft_key] = rows_to_editor(fetch_bom_rows(category))

    current_df = prepare_editor_df(st.session_state[draft_key])
    render_bom_editor(category, draft_key, current_df)


def render_category_controls(categories: list[str]) -> str:
    options = merge_unique([DEFAULT_CATEGORY, *categories])
    select_col, input_col, upload_col, template_col, download_col = st.columns(
        [1.1, 1.35, 1.1, 0.95, 0.95],
        gap="small",
    )
    with select_col:
        selected = st.selectbox("카테고리", options=options, key="bom_category_select")
    with input_col:
        typed = st.text_input(
            "새 카테고리명",
            placeholder="예: 접시정리대 / 주방 선반 / 와이어 수납",
            key="bom_category_input",
        )
    category = typed.strip() or selected

    with upload_col:
        uploaded = st.file_uploader("BOM 엑셀 업로드", type=["xlsx", "xls"], key="bom_upload")
        if st.button("엑셀 반영", key="bom_import_btn", use_container_width=True):
            if uploaded is None:
                st.warning("먼저 BOM 엑셀 파일을 업로드하세요.")
            else:
                try:
                    imported = import_excel(uploaded.getvalue())
                except Exception as exc:
                    st.warning(f"엑셀 반영 실패: {exc}")
                else:
                    result = save_category_bom(category, strip_delete_column(imported))
                    if result["ok"]:
                        st.session_state[f"bom_editor_draft_{category}"] = imported
                        clear_bom_editor_buffer(category)
                        st.success(f'엑셀 데이터를 저장하고 편집표에 반영했습니다. ({result["count"]}행)')
                        st.rerun()
                    else:
                        st.warning(result["message"])
    with template_col:
        st.write("")
        st.download_button(
            "양식 다운로드",
            data=bom_excel(sample_template_df(), f"{category} BOM"),
            file_name=f"{safe_filename(category)}_BOM_양식.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="bom_template_download",
        )
    with download_col:
        st.write("")
        saved_df = rows_to_editor(fetch_bom_rows(category)).drop(columns=["삭제"], errors="ignore")
        st.download_button(
            "BOM 다운로드",
            data=bom_excel(saved_df if not saved_df.empty else sample_template_df(), f"{category} BOM"),
            file_name=f"{safe_filename(category)}_BOM.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="bom_download",
        )
    return category


def render_bom_editor(category: str, draft_key: str, current_df: pd.DataFrame) -> None:
    with st.container(key="bom_editor_panel"):
        st.markdown('<span id="bom_editor_panel"></span>', unsafe_allow_html=True)
        edit_buffer_key = f"bom_editor_buffer_{safe_key(category)}"
        if edit_buffer_key not in st.session_state:
            st.session_state[edit_buffer_key] = prepare_editor_df(current_df)
        edit_buffer = prepare_editor_df(st.session_state[edit_buffer_key])
        view_tab, edit_tab = st.tabs([f"{category} BOM", "BOM 등록/수정"])

        with view_tab:
            render_bom_outline_view(current_df, category)

        with edit_tab:
            st.markdown('<div class="bom-subtitle">BOM 등록/수정 폼</div>', unsafe_allow_html=True)
            render_bom_item_form(category, draft_key, current_df)

            st.markdown('<div class="bom-subtitle">BOM 작성표</div>', unsafe_allow_html=True)
            st.caption("완제품 행을 먼저 만들고 아래에 부품/부속품/포장재/인쇄물/박스를 이어서 입력하세요. 선택 삭제는 왼쪽 삭제 체크 후 누르면 됩니다.")

            action_cols = st.columns([0.9, 0.9, 1.0, 4.2], gap="small")
            with action_cols[0]:
                if st.button("예시 불러오기", key="bom_sample_btn", use_container_width=True):
                    st.session_state[edit_buffer_key] = prepare_editor_df(sample_template_df())
                    st.rerun()
            with action_cols[1]:
                if st.button("빈 행 추가", key="bom_blank_row_btn", use_container_width=True):
                    st.session_state[edit_buffer_key] = append_blank_rows(edit_buffer, 5)
                    st.rerun()
            with action_cols[2]:
                if st.button("전체 삭제", key="bom_full_delete_btn", use_container_width=True):
                    delete_category_bom(category)
                    st.session_state[draft_key] = prepare_editor_df(pd.DataFrame(columns=BOM_COLUMNS))
                    clear_bom_editor_buffer(category)
                    st.success("현재 카테고리 BOM을 전체 삭제했습니다.")
                    st.rerun()

            with st.form(key=f"bom_editor_form_{safe_key(category)}", clear_on_submit=False):
                edited = st.data_editor(
                    style_bom_editor_df(editor_display_df(edit_buffer)),
                    hide_index=True,
                    use_container_width=True,
                    height=420,
                    num_rows="dynamic",
                    key=f"bom_editor_{safe_key(category)}",
                    column_order=EDITOR_DISPLAY_COLUMNS,
                    column_config={
                        "표시": st.column_config.TextColumn("표시", width=86, disabled=True),
                        "삭제": st.column_config.CheckboxColumn("삭제", width=52, default=False),
                        "상품명": st.column_config.TextColumn("상품명", width="large", default=""),
                        "유형": st.column_config.SelectboxColumn("유형", options=ITEM_TYPES, default="부품"),
                        "담당자": st.column_config.TextColumn("담당자", default=DEFAULT_MANAGER),
                        "거래처": st.column_config.TextColumn("거래처", default=DEFAULT_VENDOR),
                        "필요 재고": st.column_config.NumberColumn("필요 재고", min_value=0, step=1, default=1),
                        BARCODE_COLUMN: st.column_config.TextColumn(BARCODE_COLUMN, width="medium", default=""),
                        SPEC_COLUMN: st.column_config.TextColumn(SPEC_COLUMN, width="medium", default=""),
                        COST_COLUMN: st.column_config.TextColumn(COST_COLUMN, width="medium", default=""),
                    },
                )
                save_col, delete_col, spacer = st.columns([1.0, 1.0, 5.4], gap="small")
                with save_col:
                    save_submitted = st.form_submit_button("BOM 저장", type="primary", use_container_width=True)
                with delete_col:
                    selected_delete_submitted = st.form_submit_button("선택 삭제", use_container_width=True)
                with spacer:
                    st.empty()
                if save_submitted:
                    next_df = prepare_editor_df(edited)
                    result = save_category_bom(category, strip_delete_column(next_df))
                    if result["ok"]:
                        st.session_state[draft_key] = next_df
                        st.session_state[edit_buffer_key] = next_df
                        st.success(f'{result["message"]} ({result["count"]}행)')
                        st.rerun()
                    else:
                        st.session_state[edit_buffer_key] = next_df
                        st.warning(result["message"])
                elif selected_delete_submitted:
                    deleted = count_checked_rows(edited)
                    next_df = prepare_editor_df(drop_checked_rows(edited))
                    if deleted:
                        st.session_state[draft_key] = next_df
                        st.session_state[edit_buffer_key] = next_df
                        save_category_bom(category, strip_delete_column(next_df))
                        st.success(f"선택한 행을 삭제했습니다. ({deleted}행)")
                        st.rerun()
                    else:
                        st.warning("삭제 체크된 행이 없습니다.")

            clear_col, spacer = st.columns([1.0, 6.4], gap="small")
            with clear_col:
                if st.button("작성 초기화", key="bom_draft_clear_btn", use_container_width=True):
                    st.session_state[edit_buffer_key] = rows_to_editor(fetch_bom_rows(category))
                    st.rerun()
            with spacer:
                st.empty()


def render_bom_item_form(category: str, draft_key: str, current_df: pd.DataFrame) -> None:
    clean_df = strip_delete_column(prepare_editor_df(current_df))
    target_options = ["new", *[str(index) for index in clean_df.index]]
    selected_target = st.selectbox(
        "등록/수정 대상",
        options=target_options,
        format_func=lambda value: "신규 등록" if value == "new" else bom_row_option_label(clean_df, int(value)),
        key=f"bom_form_target_{safe_key(category)}",
    )
    selected_index = None if selected_target == "new" else int(selected_target)
    source = default_bom_form_row()
    if selected_index is not None and selected_index in clean_df.index:
        source.update(clean_df.loc[selected_index].to_dict())
    product_options = bom_parent_product_options(clean_df)
    default_parent = default_parent_option(clean_df, selected_index, product_options)

    form_key = f"bom_item_form_{safe_key(category)}_{selected_target}"
    with st.form(form_key, clear_on_submit=False):
        name_col, type_col, parent_col, stock_col = st.columns([1.45, 0.72, 1.2, 0.55], gap="small")
        with name_col:
            item_name = st.text_input("상품명", value=str(source.get("상품명", "")), key=f"{form_key}_name")
        with type_col:
            item_type = st.selectbox(
                "유형",
                options=ITEM_TYPES,
                index=ITEM_TYPES.index(source.get("유형")) if source.get("유형") in ITEM_TYPES else ITEM_TYPES.index("부품"),
                key=f"{form_key}_type",
            )
        with parent_col:
            parent_target = st.selectbox(
                "하위 등록할 완제품",
                options=product_options,
                index=product_options.index(default_parent) if default_parent in product_options else 0,
                format_func=lambda value: "맨 아래에 등록" if value == "end" else bom_parent_option_label(clean_df, int(value)),
                help="부품/부속품/포장재 등 구성품은 선택한 완제품 바로 아래 묶음에 등록됩니다.",
                key=f"{form_key}_parent",
            )
        with stock_col:
            required_stock = st.number_input(
                "필요 재고",
                min_value=0,
                step=1,
                value=int(source.get("필요 재고", 1) or 0),
                key=f"{form_key}_stock",
            )

        manager_col, vendor_col = st.columns(2, gap="small")
        with manager_col:
            manager = st.text_input("담당자", value=str(source.get("담당자", "")), key=f"{form_key}_manager")
        with vendor_col:
            vendor = st.text_input("거래처", value=str(source.get("거래처", "")), key=f"{form_key}_vendor")

        barcode_col, spec_col, cost_col = st.columns(3, gap="small")
        with barcode_col:
            barcode = st.text_input(BARCODE_COLUMN, value=str(source.get(BARCODE_COLUMN, "")), key=f"{form_key}_barcode")
        with spec_col:
            spec = st.text_input(SPEC_COLUMN, value=str(source.get(SPEC_COLUMN, "")), key=f"{form_key}_spec")
        with cost_col:
            cost_value = st.text_input(COST_COLUMN, value=str(source.get(COST_COLUMN, "")), key=f"{form_key}_cost")

        register_col, update_col, delete_col, spacer = st.columns([0.9, 0.9, 0.9, 2.8], gap="small")
        with register_col:
            register_submitted = st.form_submit_button("신규 등록", type="primary", use_container_width=True)
        with update_col:
            update_submitted = st.form_submit_button("수정 저장", use_container_width=True)
        with delete_col:
            delete_submitted = st.form_submit_button("선택 삭제", use_container_width=True)
        with spacer:
            st.empty()

    row = {
        "상품명": item_name,
        "유형": item_type,
        "담당자": manager,
        "거래처": vendor,
        "필요 재고": required_stock,
        BARCODE_COLUMN: barcode,
        SPEC_COLUMN: spec,
        COST_COLUMN: cost_value,
    }
    if register_submitted:
        if not str(item_name).strip() and not str(barcode).strip() and not str(spec).strip():
            st.warning("상품명, 88바코드 또는 규격을 입력하세요.")
            return
        if not is_group_item_type(item_type) and parent_target == "end" and has_group_items(clean_df):
            st.warning("구성품을 넣을 완제품을 선택하세요.")
            return
        next_df = insert_bom_form_row(clean_df, row, parent_target)
        message = (
            "신규 완제품 BOM을 등록했습니다."
            if is_group_item_type(item_type)
            else "선택한 완제품 아래에 구성품을 등록했습니다."
        )
        save_bom_draft(category, draft_key, next_df, message)

    if update_submitted:
        if selected_index is None:
            st.warning("수정할 품목을 먼저 선택하세요.")
            return
        next_df = clean_df.copy()
        next_df.loc[selected_index, BOM_COLUMNS] = [row[column] for column in BOM_COLUMNS]
        if not is_group_item_type(item_type):
            next_df = move_bom_row_under_parent(next_df, selected_index, parent_target)
        save_bom_draft(category, draft_key, next_df, "선택한 BOM 품목을 수정 저장했습니다.")

    if delete_submitted:
        if selected_index is None:
            st.warning("삭제할 품목을 먼저 선택하세요.")
            return
        next_df = clean_df.drop(index=selected_index).reset_index(drop=True)
        save_bom_draft(category, draft_key, next_df, "선택한 BOM 품목을 삭제했습니다.")


def default_bom_form_row() -> dict:
    return {
        "상품명": "",
        "유형": "부품",
        "담당자": DEFAULT_MANAGER,
        "거래처": DEFAULT_VENDOR,
        "필요 재고": 1,
        BARCODE_COLUMN: "",
        SPEC_COLUMN: "",
        COST_COLUMN: "",
    }


def bom_row_option_label(df: pd.DataFrame, index: int) -> str:
    if index not in df.index:
        return f"{index + 1}. 품목 없음"
    row = df.loc[index]
    item_name = str(row.get("상품명", "")).strip() or "상품명 없음"
    item_type = str(row.get("유형", "")).strip() or "유형 없음"
    barcode = str(row.get(BARCODE_COLUMN, "")).strip()
    spec = str(row.get(SPEC_COLUMN, "")).strip()
    details = " / ".join(value for value in [barcode, spec] if value)
    detail_text = f" / {details}" if details else ""
    return f"{index + 1}. [{item_type}] {item_name}{detail_text}"


def bom_parent_product_options(df: pd.DataFrame) -> list[str]:
    options = [str(index) for index, row in df.iterrows() if is_group_item_type(row.get("유형", ""))]
    return options or ["end"]


def bom_parent_option_label(df: pd.DataFrame, index: int) -> str:
    if index not in df.index:
        return "완제품 없음"
    name = str(df.loc[index].get("상품명", "")).strip() or "상품명 없음"
    return f"{index + 1}. {name}"


def default_parent_option(df: pd.DataFrame, selected_index: int | None, options: list[str]) -> str:
    if not options or options == ["end"]:
        return "end"
    if selected_index is not None:
        parent_index = parent_product_index_for_row(df, selected_index)
        if parent_index is not None and str(parent_index) in options:
            return str(parent_index)
    return options[-1]


def parent_product_index_for_row(df: pd.DataFrame, row_index: int) -> int | None:
    for index in reversed([idx for idx in df.index if idx < row_index]):
        if is_group_item_type(df.loc[index].get("유형", "")):
            return int(index)
    if row_index in df.index and is_group_item_type(df.loc[row_index].get("유형", "")):
        return int(row_index)
    return None


def has_group_items(df: pd.DataFrame) -> bool:
    return any(is_group_item_type(row.get("유형", "")) for _, row in df.iterrows())


def insert_bom_form_row(df: pd.DataFrame, row: dict, parent_target: str) -> pd.DataFrame:
    clean_df = strip_delete_column(prepare_editor_df(df))
    row_df = prepare_editor_df(pd.DataFrame([row]))
    if is_group_item_type(row.get("유형", "")) or parent_target == "end":
        return prepare_editor_df(pd.concat([clean_df, row_df], ignore_index=True))
    insert_at = child_insert_position(clean_df, int(parent_target))
    upper = clean_df.iloc[:insert_at]
    lower = clean_df.iloc[insert_at:]
    return prepare_editor_df(pd.concat([upper, row_df, lower], ignore_index=True))


def move_bom_row_under_parent(df: pd.DataFrame, row_index: int, parent_target: str) -> pd.DataFrame:
    clean_df = strip_delete_column(prepare_editor_df(df)).reset_index(drop=True)
    if parent_target == "end" or row_index not in clean_df.index:
        return clean_df
    row = clean_df.loc[row_index].to_dict()
    original_parent = parent_product_index_for_row(clean_df, row_index)
    if original_parent == int(parent_target):
        return clean_df
    without_row = clean_df.drop(index=row_index).reset_index(drop=True)
    adjusted_parent = int(parent_target) - (1 if row_index < int(parent_target) else 0)
    return insert_bom_form_row(without_row, row, str(adjusted_parent))


def child_insert_position(df: pd.DataFrame, parent_index: int) -> int:
    insert_at = len(df)
    for index in df.index:
        if index <= parent_index:
            continue
        if is_group_item_type(df.loc[index].get("유형", "")):
            insert_at = int(index)
            break
    return insert_at


def save_bom_draft(category: str, draft_key: str, df: pd.DataFrame, success_message: str) -> None:
    prepared = prepare_editor_df(df)
    if not category.strip():
        st.warning("카테고리명을 입력하세요.")
        return
    if not editor_to_rows(prepared):
        delete_category_bom(category)
        st.session_state[draft_key] = prepare_editor_df(pd.DataFrame(columns=BOM_COLUMNS))
        clear_bom_editor_buffer(category)
        st.success(f"{success_message} (0행)")
        st.rerun()
    result = save_category_bom(category, strip_delete_column(prepared))
    if result["ok"]:
        st.session_state[draft_key] = prepared
        clear_bom_editor_buffer(category)
        st.success(f'{success_message} ({result["count"]}행)')
        st.rerun()
    else:
        st.warning(result["message"])


def clear_bom_editor_buffer(category: str) -> None:
    st.session_state.pop(f"bom_editor_buffer_{safe_key(category)}", None)


def bom_available() -> bool:
    if init_db is None or SessionLocal is None or CategoryBomItem is None:
        return False
    try:
        init_db()
    except Exception as exc:
        global BOM_IMPORT_ERROR
        BOM_IMPORT_ERROR = f"BOM DB 초기화 실패: {exc}"
        return False
    return True


def with_db(action):
    if SessionLocal is None:
        return None
    db = SessionLocal()
    try:
        return action(db)
    except Exception as exc:
        db.rollback()
        st.error(f"처리 실패: {exc}")
        return None
    finally:
        db.close()


def fetch_categories() -> list[str]:
    return (
        with_db(
            lambda db: list(
                db.execute(select(CategoryBomItem.category_name).distinct().order_by(CategoryBomItem.category_name)).scalars()
            )
        )
        or []
    )


def fetch_bom_rows(category: str) -> list[CategoryBomItem]:
    return (
        with_db(
            lambda db: list(
                db.execute(
                    select(CategoryBomItem)
                    .where(CategoryBomItem.category_name == category)
                    .order_by(CategoryBomItem.sort_order, CategoryBomItem.id)
                ).scalars()
            )
        )
        or []
    )


def save_category_bom(category: str, df: pd.DataFrame) -> dict:
    rows = editor_to_rows(df)
    if not category.strip():
        return {"ok": False, "message": "카테고리명을 입력하세요.", "count": 0}
    if not rows:
        return {"ok": False, "message": "저장할 BOM 행이 없습니다.", "count": 0}

    def action(db):
        db.execute(delete(CategoryBomItem).where(CategoryBomItem.category_name == category))
        for index, row in enumerate(rows, start=1):
            db.add(CategoryBomItem(category_name=category, sort_order=index, **row))
        db.commit()
        return {"ok": True, "message": "BOM 저장 완료", "count": len(rows)}

    return with_db(action) or {"ok": False, "message": "저장 실패", "count": 0}


def delete_category_bom(category: str) -> None:
    with_db(lambda db: delete_category_action(db, category))


def delete_category_action(db, category: str) -> int:
    result = db.execute(delete(CategoryBomItem).where(CategoryBomItem.category_name == category))
    db.commit()
    return int(result.rowcount or 0)


def bom_row_barcode(row: CategoryBomItem) -> str:
    barcode = str(getattr(row, "barcode", "") or "").strip()
    if barcode:
        return barcode
    legacy_barcode, _ = split_legacy_barcode_spec(getattr(row, "barcode_spec", ""))
    return legacy_barcode


def bom_row_spec(row: CategoryBomItem) -> str:
    spec = str(getattr(row, "spec", "") or "").strip()
    if spec:
        return spec
    _, legacy_spec = split_legacy_barcode_spec(getattr(row, "barcode_spec", ""))
    return legacy_spec


def combine_barcode_spec(barcode: str, spec: str) -> str:
    barcode = str(barcode or "").strip()
    spec = str(spec or "").strip()
    if barcode and spec:
        return f"{barcode} || {spec}"
    return barcode or spec


def split_legacy_barcode_spec(value) -> tuple[str, str]:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return "", ""
    for separator in (" || ", " | "):
        if separator in text:
            barcode, spec = text.split(separator, 1)
            return barcode.strip(), spec.strip()
    if " / " in text:
        barcode, spec = text.split(" / ", 1)
        if looks_like_barcode(barcode):
            return barcode.strip(), spec.strip()
    if looks_like_barcode(text):
        return text, ""
    return "", text


def looks_like_barcode(value) -> bool:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return len(digits) >= 8 and all(ch.isdigit() or ch in {" ", "-", "."} for ch in text)


def rows_to_editor(rows: list[CategoryBomItem]) -> pd.DataFrame:
    data = [
        {
            "삭제": False,
            "상품명": row.item_name,
            "유형": row.item_type,
            "담당자": row.manager,
            "거래처": row.vendor,
            "필요 재고": row.required_stock,
            BARCODE_COLUMN: bom_row_barcode(row),
            SPEC_COLUMN: bom_row_spec(row),
            COST_COLUMN: row.memo,
        }
        for row in rows
    ]
    return prepare_editor_df(pd.DataFrame(data, columns=EDITOR_COLUMNS))


def prepare_editor_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None:
        df = pd.DataFrame(columns=EDITOR_COLUMNS)
    prepared = df.copy()
    if COST_COLUMN not in prepared.columns and "비고" in prepared.columns:
        prepared[COST_COLUMN] = prepared["비고"]
    if LEGACY_BARCODE_SPEC_COLUMN in prepared.columns:
        legacy_values = prepared[LEGACY_BARCODE_SPEC_COLUMN].map(split_legacy_barcode_spec)
        if BARCODE_COLUMN not in prepared.columns:
            prepared[BARCODE_COLUMN] = legacy_values.map(lambda value: value[0])
        else:
            prepared[BARCODE_COLUMN] = prepared[BARCODE_COLUMN].where(
                prepared[BARCODE_COLUMN].fillna("").astype(str).str.strip().ne(""),
                legacy_values.map(lambda value: value[0]),
            )
        if SPEC_COLUMN not in prepared.columns:
            prepared[SPEC_COLUMN] = legacy_values.map(lambda value: value[1])
        else:
            prepared[SPEC_COLUMN] = prepared[SPEC_COLUMN].where(
                prepared[SPEC_COLUMN].fillna("").astype(str).str.strip().ne(""),
                legacy_values.map(lambda value: value[1]),
            )
    for column in EDITOR_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = False if column == "삭제" else ""
    prepared = prepared[EDITOR_COLUMNS]
    prepared["삭제"] = prepared["삭제"].map(is_checked).astype(bool)
    for column in ["상품명", "담당자", "거래처", BARCODE_COLUMN, SPEC_COLUMN, COST_COLUMN]:
        prepared[column] = prepared[column].fillna("").map(lambda value: str(value).strip())
    prepared["유형"] = prepared["유형"].fillna("부품").map(lambda value: str(value).strip() or "부품")
    prepared["유형"] = prepared["유형"].map(lambda value: value if value in ITEM_TYPES else "기타")
    prepared["필요 재고"] = pd.to_numeric(prepared["필요 재고"], errors="coerce").fillna(1).astype(int)
    return prepared


def style_bom_editor_df(df: pd.DataFrame):
    prepared = df.copy()
    for column in EDITOR_DISPLAY_COLUMNS:
        if column not in prepared.columns:
            prepared[column] = ""
    prepared = prepared[EDITOR_DISPLAY_COLUMNS]

    def row_style(row: pd.Series) -> list[str]:
        if is_group_item_type(row.get("유형", "")):
            return [
                "background-color: rgba(210, 210, 210, 0.25); "
                "color: #ffffff; "
                "font-weight: 900;"
            ] * len(row)
        if str(row.get("표시", "")).strip().startswith("└"):
            return [
                "background-color: rgba(210, 210, 210, 0.08); "
                "color: #eefaf7;"
            ] * len(row)
        return [""] * len(row)

    return prepared.style.apply(row_style, axis=1)


def render_bom_outline_view(df: pd.DataFrame, category: str) -> None:
    groups = bom_outline_groups(df)
    if not groups:
        st.info("등록된 완제품 BOM이 없습니다. BOM 등록/수정 탭에서 완제품과 구성품을 등록하세요.")
        return
    st.markdown(f'<div class="bom-outline-title">{escape(category)} BOM</div>', unsafe_allow_html=True)
    components.html(bom_outline_html(groups, category), height=620, scrolling=False)


def bom_outline_html(groups: list[dict], category: str) -> str:
    families = merge_unique([derive_product_family(group["product"]) for group in groups])
    brands = merge_unique([derive_product_brand(group["product"]) for group in groups])
    family_options = outline_select_options(families)
    brand_options = outline_select_options(brands)
    cards = "".join(outline_group_card_html(group, index) for index, group in enumerate(groups, start=1))
    storage_key = f"scmBomOutlineStateV3:{safe_key(category)}"
    return f"""
    <!doctype html>
    <html lang="ko">
    <head>
        <meta charset="utf-8">
        <style>
            :root,
            html {{
                color-scheme: dark;
                font-family: "Pretendard", "Noto Sans KR", Arial, sans-serif;
                background: #031b18;
            }}
            * {{
                box-sizing: border-box;
                letter-spacing: 0;
            }}
            body {{
                background: #031b18;
                color: #f2fffb;
                margin: 0;
                overflow: hidden;
            }}
            .outline-shell {{
                background: rgba(6, 48, 43, 0.58);
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 8px;
                display: flex;
                flex-direction: column;
                height: 604px;
                overflow: hidden;
                padding: 0.72rem;
            }}
            .outline-toolbar {{
                background: rgba(6, 48, 43, 0.96);
                border-bottom: 1px solid rgba(126, 197, 185, 0.24);
                flex: 0 0 auto;
                margin: -0.72rem -0.72rem 0;
                padding: 0.72rem 0.72rem 0.66rem;
                position: sticky;
                top: 0;
                z-index: 4;
            }}
            .outline-controls {{
                display: grid;
                gap: 0.52rem;
                grid-template-columns: repeat(4, minmax(0, 1fr));
                margin-bottom: 0.56rem;
            }}
            .outline-field label {{
                color: #cfe8e2;
                display: block;
                font-size: 0.72rem;
                font-weight: 900;
                margin-bottom: 0.24rem;
            }}
            .outline-field input,
            .outline-field select {{
                background: #171a22;
                border: 1px solid rgba(126, 197, 185, 0.28);
                border-radius: 7px;
                color: #ffffff;
                font-size: 0.82rem;
                font-weight: 750;
                height: 38px;
                outline: 0;
                padding: 0 0.72rem;
                width: 100%;
            }}
            .outline-actions {{
                align-items: center;
                display: flex;
                gap: 0.48rem;
                justify-content: space-between;
                margin-bottom: 0.58rem;
            }}
            .outline-action-buttons {{
                display: flex;
                gap: 0.42rem;
            }}
            .outline-divider {{
                border-top: 1px solid rgba(126, 197, 185, 0.2);
                flex: 0 0 auto;
                margin: 0 -0.72rem 0.56rem;
            }}
            button {{
                background: #141720;
                border: 1px solid rgba(126, 197, 185, 0.32);
                border-radius: 7px;
                color: #ffffff;
                cursor: pointer;
                font-size: 0.78rem;
                font-weight: 900;
                height: 34px;
                padding: 0 0.82rem;
            }}
            button:hover {{
                border-color: rgba(117, 236, 219, 0.58);
            }}
            .outline-count {{
                color: #b2d5cd;
                font-size: 0.78rem;
                font-weight: 850;
            }}
            .outline-count strong {{
                color: #ffffff;
            }}
            .sku-list {{
                border: 1px solid rgba(126, 197, 185, 0.2);
                border-radius: 7px;
                flex: 1 1 auto;
                min-height: 0;
                overflow: auto;
                padding: 0.42rem;
            }}
            .sku-card {{
                background: rgba(8, 39, 36, 0.78);
                border: 1px solid rgba(126, 197, 185, 0.22);
                border-radius: 7px;
                margin-bottom: 0.42rem;
                overflow: hidden;
            }}
            .sku-card[open] {{
                border-color: rgba(117, 236, 219, 0.42);
            }}
            .sku-card[hidden] {{
                display: none;
            }}
            #emptyState[hidden] {{
                display: none;
            }}
            .sku-summary {{
                align-items: center;
                cursor: pointer;
                display: grid;
                gap: 0.6rem;
                grid-template-columns: minmax(0, 1fr) auto;
                list-style: none;
                padding: 0.66rem 0.72rem;
            }}
            .sku-summary::-webkit-details-marker {{
                display: none;
            }}
            .sku-main {{
                min-width: 0;
            }}
            .sku-title {{
                color: #ffffff;
                display: block;
                font-size: 0.9rem;
                font-weight: 950;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .sku-meta {{
                color: #b2d5cd;
                display: flex;
                flex-wrap: wrap;
                font-size: 0.72rem;
                font-weight: 800;
                gap: 0.5rem;
                margin-top: 0.22rem;
            }}
            .sku-actions {{
                display: flex;
                gap: 0.35rem;
            }}
            .sku-actions button {{
                height: 30px;
                padding: 0 0.62rem;
            }}
            .sku-body {{
                border-top: 1px solid rgba(126, 197, 185, 0.18);
                padding: 0.5rem 0.72rem 0.72rem;
            }}
            .product-row,
            .component-detail {{
                border: 1px solid rgba(210, 232, 228, 0.16);
                border-radius: 6px;
                margin-top: 0.36rem;
                overflow: hidden;
            }}
            .product-row {{
                background: rgba(210, 210, 210, 0.18);
            }}
            .component-detail {{
                background: rgba(2, 20, 19, 0.62);
            }}
            .component-detail summary {{
                align-items: center;
                cursor: pointer;
                display: grid;
                gap: 0.55rem;
                grid-template-columns: 88px minmax(0, 1fr) 92px 130px;
                list-style: none;
                padding: 0.48rem 0.55rem;
            }}
            .component-detail summary::-webkit-details-marker {{
                display: none;
            }}
            .component-detail[open] summary {{
                border-bottom: 1px solid rgba(210, 232, 228, 0.13);
            }}
            .chip {{
                background: rgba(22, 213, 198, 0.13);
                border: 1px solid rgba(22, 213, 198, 0.22);
                border-radius: 999px;
                color: #dffaf4;
                display: inline-flex;
                font-size: 0.68rem;
                font-weight: 950;
                justify-content: center;
                line-height: 1;
                padding: 0.24rem 0.42rem;
                white-space: nowrap;
            }}
            .name {{
                color: #ffffff;
                font-size: 0.78rem;
                font-weight: 900;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .muted {{
                color: #b2d5cd;
                font-size: 0.72rem;
                font-weight: 800;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .detail-grid {{
                display: grid;
                gap: 0;
                grid-template-columns: repeat(6, minmax(0, 1fr));
            }}
            .detail-cell {{
                border-right: 1px solid rgba(210, 232, 228, 0.12);
                padding: 0.44rem 0.52rem;
                min-width: 0;
            }}
            .detail-cell:last-child {{
                border-right: 0;
            }}
            .detail-cell span {{
                color: #8fb9b2;
                display: block;
                font-size: 0.62rem;
                font-weight: 900;
                margin-bottom: 0.12rem;
            }}
            .detail-cell b {{
                color: #f7fffc;
                display: block;
                font-size: 0.73rem;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            .empty {{
                color: #b2d5cd;
                font-size: 0.8rem;
                font-weight: 800;
                padding: 0.8rem;
                text-align: center;
            }}
            @media (max-width: 900px) {{
                .outline-controls {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
                .sku-summary {{
                    grid-template-columns: 1fr;
                }}
                .sku-actions {{
                    justify-content: flex-start;
                }}
                .component-detail summary {{
                    grid-template-columns: 74px minmax(0, 1fr);
                }}
                .component-detail summary .muted:nth-last-child(-n + 2) {{
                    display: none;
                }}
                .detail-grid {{
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                }}
            }}
        </style>
    </head>
    <body>
        <section class="outline-shell">
            <div class="outline-toolbar">
                <div class="outline-controls">
                    <div class="outline-field">
                        <label for="familyFilter">제품군 필터</label>
                        <select id="familyFilter">{family_options}</select>
                    </div>
                    <div class="outline-field">
                        <label for="brandFilter">브랜드 필터</label>
                        <select id="brandFilter">{brand_options}</select>
                    </div>
                    <div class="outline-field">
                        <label for="nameSearch">SKU명 검색</label>
                        <input id="nameSearch" type="search" placeholder="상품명 입력">
                    </div>
                    <div class="outline-field">
                        <label for="barcodeSearch">바코드 검색</label>
                        <input id="barcodeSearch" type="search" placeholder="88바코드 또는 규격 입력">
                    </div>
                </div>
                <div class="outline-actions">
                    <div class="outline-action-buttons">
                        <button type="button" id="expandAll">전체 1단계</button>
                        <button type="button" id="expandAllDetails">전체 2단계</button>
                        <button type="button" id="collapseAll">전체 접기</button>
                        <button type="button" id="resetFilters">필터 초기화</button>
                    </div>
                    <div class="outline-count"><strong id="visibleCount">0</strong> / {len(groups)} SKU 표시</div>
                </div>
            </div>
            <div class="outline-divider"></div>
            <div class="sku-list" id="skuList">
                <div class="empty" id="emptyState" hidden>조건에 맞는 SKU가 없습니다.</div>
                {cards}
            </div>
        </section>
        <script>
            const storageKey = "{storage_key}";
            const list = document.getElementById("skuList");
            const controls = {{
                family: document.getElementById("familyFilter"),
                brand: document.getElementById("brandFilter"),
                name: document.getElementById("nameSearch"),
                barcode: document.getElementById("barcodeSearch"),
            }};
            const cards = Array.from(document.querySelectorAll(".sku-card"));

            function readState() {{
                try {{
                    return JSON.parse(localStorage.getItem(storageKey) || "{{}}");
                }} catch (error) {{
                    return {{}};
                }}
            }}

            function writeState(patch) {{
                const state = {{ ...readState(), ...patch }};
                localStorage.setItem(storageKey, JSON.stringify(state));
            }}

            function currentOpenSkuIds() {{
                return cards.filter(card => card.open).map(card => card.dataset.id);
            }}

            function currentOpenChildIds() {{
                return Array.from(document.querySelectorAll(".component-detail[open]")).map(child => child.dataset.id);
            }}

            function saveOpenState() {{
                writeState({{
                    openSkuIds: currentOpenSkuIds(),
                    openChildIds: currentOpenChildIds(),
                }});
            }}

            function saveFilters() {{
                writeState({{
                    filters: {{
                        family: controls.family.value,
                        brand: controls.brand.value,
                        name: controls.name.value,
                        barcode: controls.barcode.value,
                    }},
                }});
            }}

            function normalize(value) {{
                return String(value || "").trim().toLowerCase();
            }}

            function cardMatches(card) {{
                const family = controls.family.value;
                const brand = controls.brand.value;
                const name = normalize(controls.name.value);
                const barcode = normalize(controls.barcode.value);
                return (!family || card.dataset.family === family)
                    && (!brand || card.dataset.brand === brand)
                    && (!name || normalize(card.dataset.name).includes(name))
                    && (!barcode || normalize(card.dataset.barcodes).includes(barcode));
            }}

            function applyFilters() {{
                let visible = 0;
                cards.forEach(card => {{
                    const matched = cardMatches(card);
                    card.hidden = !matched;
                    if (matched) visible += 1;
                }});
                document.getElementById("visibleCount").textContent = visible.toLocaleString("ko-KR");
                document.getElementById("emptyState").hidden = visible > 0;
                saveFilters();
            }}

            function restoreState() {{
                const state = readState();
                if (state.filters) {{
                    controls.family.value = state.filters.family || "";
                    controls.brand.value = state.filters.brand || "";
                    controls.name.value = state.filters.name || "";
                    controls.barcode.value = state.filters.barcode || "";
                }}
                const openSkuIds = new Set(state.openSkuIds || []);
                const openChildIds = new Set(state.openChildIds || []);
                cards.forEach(card => {{
                    card.open = openSkuIds.has(card.dataset.id);
                    card.querySelectorAll(".component-detail").forEach(child => {{
                        child.open = openChildIds.has(child.dataset.id);
                    }});
                }});
                applyFilters();
                requestAnimationFrame(() => {{
                    list.scrollTop = Number(state.scrollTop || 0);
                }});
            }}

            Object.values(controls).forEach(control => {{
                control.addEventListener("input", applyFilters);
                control.addEventListener("change", applyFilters);
            }});

            document.getElementById("expandAll").addEventListener("click", () => {{
                cards.forEach(card => {{
                    if (!card.hidden) {{
                        card.open = true;
                        card.querySelectorAll(".component-detail").forEach(child => {{
                            child.open = false;
                        }});
                    }}
                }});
                saveOpenState();
            }});

            document.getElementById("expandAllDetails").addEventListener("click", () => {{
                cards.forEach(card => {{
                    if (!card.hidden) {{
                        card.open = true;
                        card.querySelectorAll(".component-detail").forEach(child => {{
                            child.open = true;
                        }});
                    }}
                }});
                saveOpenState();
            }});

            document.getElementById("collapseAll").addEventListener("click", () => {{
                cards.forEach(card => {{
                    if (!card.hidden) {{
                        card.open = false;
                        card.querySelectorAll(".component-detail").forEach(child => {{
                            child.open = false;
                        }});
                    }}
                }});
                saveOpenState();
            }});

            document.getElementById("resetFilters").addEventListener("click", () => {{
                controls.family.value = "";
                controls.brand.value = "";
                controls.name.value = "";
                controls.barcode.value = "";
                applyFilters();
            }});

            document.addEventListener("click", event => {{
                const actionButton = event.target.closest("[data-action]");
                if (!actionButton) return;
                event.preventDefault();
                event.stopPropagation();
                const card = actionButton.closest(".sku-card");
                const action = actionButton.dataset.action;
                if (action === "children-step1") {{
                    card.open = true;
                    card.querySelectorAll(".component-detail").forEach(child => {{
                        child.open = false;
                    }});
                }}
                if (action === "children-step2") {{
                    card.open = true;
                    card.querySelectorAll(".component-detail").forEach(child => {{
                        child.open = true;
                    }});
                }}
                if (action === "children-close") {{
                    card.querySelectorAll(".component-detail").forEach(child => {{
                        child.open = false;
                    }});
                    card.open = false;
                }}
                saveOpenState();
            }});

            document.addEventListener("toggle", event => {{
                if (event.target.matches(".sku-card, .component-detail")) {{
                    saveOpenState();
                }}
            }}, true);

            let scrollTimer = null;
            list.addEventListener("scroll", () => {{
                clearTimeout(scrollTimer);
                scrollTimer = setTimeout(() => writeState({{ scrollTop: list.scrollTop }}), 80);
            }});

            restoreState();
        </script>
    </body>
    </html>
    """


def outline_select_options(values: list[str]) -> str:
    options = ['<option value="">전체</option>']
    for value in values:
        normalized = value or "미지정"
        options.append(f'<option value="{escape(normalized, quote=True)}">{escape(normalized)}</option>')
    return "".join(options)


def outline_group_card_html(group: dict, index: int) -> str:
    product = group["product"]
    children = group["children"]
    product_name = str(product.get("상품명", "") or "완제품").strip()
    barcode = str(product.get(BARCODE_COLUMN, "") or "").strip()
    spec = str(product.get(SPEC_COLUMN, "") or "").strip()
    family = derive_product_family(product)
    brand = derive_product_brand(product)
    group_id = safe_key(f"{index}_{product_name}_{barcode}_{spec}")
    all_barcodes = " ".join(
        [
            barcode,
            spec,
            *[str(child.get(BARCODE_COLUMN, "")) for child in children],
            *[str(child.get(SPEC_COLUMN, "")) for child in children],
        ]
    ).strip()
    children_html = "".join(outline_component_detail_html(child, group_id, child_index) for child_index, child in enumerate(children, start=1))
    if not children_html:
        children_html = '<div class="empty">구성품이 없습니다.</div>'
    return f"""
    <details class="sku-card"
        data-id="{escape(group_id, quote=True)}"
        data-family="{escape(family, quote=True)}"
        data-brand="{escape(brand, quote=True)}"
        data-name="{escape(product_name, quote=True)}"
        data-barcodes="{escape(all_barcodes, quote=True)}">
        <summary class="sku-summary">
            <div class="sku-main">
                <span class="sku-title">{index}. {escape(product_name)}</span>
                <span class="sku-meta">
                    <span>제품군 {escape(family)}</span>
                    <span>브랜드 {escape(brand)}</span>
                    <span>구성품 {len(children)}개</span>
                    <span>바코드 {escape(barcode or "-")}</span>
                    <span>규격 {escape(spec or "-")}</span>
                </span>
            </div>
            <div class="sku-actions">
                <button type="button" data-action="children-step1">1단계 구성품</button>
                <button type="button" data-action="children-step2">2단계 거래처</button>
                <button type="button" data-action="children-close">접기</button>
            </div>
        </summary>
        <div class="sku-body">
            {outline_product_row_html(product)}
            {children_html}
        </div>
    </details>
    """


def outline_product_row_html(product: dict) -> str:
    return f"""
    <div class="product-row">
        <div class="detail-grid">
            {outline_detail_cell("표시", "완제품")}
            {outline_detail_cell("상품명", product.get("상품명", ""))}
            {outline_detail_cell("유형", product.get("유형", ""))}
            {outline_detail_cell("필요 재고", product.get("필요 재고", ""))}
            {outline_detail_cell(BARCODE_COLUMN, product.get(BARCODE_COLUMN, ""))}
            {outline_detail_cell(SPEC_COLUMN, product.get(SPEC_COLUMN, ""))}
            {outline_detail_cell(COST_COLUMN, product.get(COST_COLUMN, ""))}
        </div>
    </div>
    """


def outline_component_detail_html(child: dict, group_id: str, child_index: int) -> str:
    child_id = safe_key(f"{group_id}_{child_index}_{child.get('상품명', '')}_{child.get(BARCODE_COLUMN, '')}_{child.get(SPEC_COLUMN, '')}")
    return f"""
    <details class="component-detail" data-id="{escape(child_id, quote=True)}">
        <summary>
            <span class="chip">{escape(str(child.get("유형", "구성품") or "구성품"))}</span>
            <span class="name">{escape(str(child.get("상품명", "")))}</span>
            <span class="muted">필요 {escape(str(child.get("필요 재고", "")))}</span>
            <span class="muted">{escape(str(child.get(BARCODE_COLUMN, "") or child.get(SPEC_COLUMN, "") or "-"))}</span>
        </summary>
        <div class="detail-grid">
            {outline_detail_cell("담당자", child.get("담당자", ""))}
            {outline_detail_cell("거래처", child.get("거래처", ""))}
            {outline_detail_cell("필요 재고", child.get("필요 재고", ""))}
            {outline_detail_cell(BARCODE_COLUMN, child.get(BARCODE_COLUMN, ""))}
            {outline_detail_cell(SPEC_COLUMN, child.get(SPEC_COLUMN, ""))}
            {outline_detail_cell(COST_COLUMN, child.get(COST_COLUMN, ""))}
            {outline_detail_cell("표시", "구성품")}
        </div>
    </details>
    """


def outline_detail_cell(label: str, value) -> str:
    return f'<div class="detail-cell"><span>{escape(str(label))}</span><b>{escape(str(value or "-"))}</b></div>'


def derive_product_brand(product: dict) -> str:
    name = str(product.get("상품명", "") or "").strip()
    if "/" in name:
        brand = name.split("/", 1)[0].strip()
        return brand or "미지정"
    first = name.split(" ", 1)[0].strip()
    return first if first and len(first) <= 16 else "미지정"


def derive_product_family(product: dict) -> str:
    name = str(product.get("상품명", "") or "").strip()
    target = name.split("/", 1)[1].strip() if "/" in name else name
    for separator in (":", "-", "(", "["):
        if separator in target:
            target = target.split(separator, 1)[0].strip()
            break
    return target or "미지정"


def bom_outline_groups(df: pd.DataFrame) -> list[dict]:
    clean_df = strip_delete_column(prepare_editor_df(df))
    groups = []
    current_group = None
    for _, row in clean_df.iterrows():
        has_content = bool(
            str(row.get("상품명", "")).strip()
            or str(row.get(BARCODE_COLUMN, "")).strip()
            or str(row.get(SPEC_COLUMN, "")).strip()
        )
        if not has_content:
            continue
        row_dict = row.to_dict()
        if is_group_item_type(row.get("유형")) or current_group is None:
            current_group = {"product": row_dict, "children": []}
            groups.append(current_group)
        else:
            current_group["children"].append(row_dict)
    return groups


def render_outline_group_table(product: dict, children: list[dict]) -> None:
    rows_html = outline_row_html(product, "완제품", "group")
    if children:
        for child in children:
            rows_html += outline_row_html(child, "└ 구성품", "child")
    else:
        rows_html += f'<tr><td colspan="{len(BOM_COLUMNS) + 1}" class="empty">구성품이 없습니다.</td></tr>'
    headers = "".join(f"<th>{escape(column)}</th>" for column in ["표시", *BOM_COLUMNS])
    st.markdown(
        f"""
        <div class="bom-outline-scroll">
            <table class="bom-outline-table">
                <thead><tr>{headers}</tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )


def outline_row_html(row: dict, mark: str, row_class: str) -> str:
    cells = [mark, *[row.get(column, "") for column in BOM_COLUMNS]]
    return f'<tr class="{row_class}">' + "".join(f"<td>{escape(str(value))}</td>" for value in cells) + "</tr>"


def editor_display_df(df: pd.DataFrame) -> pd.DataFrame:
    prepared = prepare_editor_df(df)
    display = prepared.copy()
    labels = []
    has_group = False
    for _, row in display.iterrows():
        has_content = bool(
            str(row.get("상품명", "")).strip()
            or str(row.get(BARCODE_COLUMN, "")).strip()
            or str(row.get(SPEC_COLUMN, "")).strip()
        )
        if is_group_item_type(row.get("유형")):
            labels.append("완제품")
            has_group = True
        elif has_content and has_group:
            labels.append("└ 구성품")
        else:
            labels.append("")
    display.insert(0, "표시", labels)
    return display[EDITOR_DISPLAY_COLUMNS]


def strip_delete_column(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=BOM_COLUMNS)
    return df.drop(columns=["삭제"], errors="ignore").reset_index(drop=True)


def count_checked_rows(df: pd.DataFrame) -> int:
    if df is None or df.empty or "삭제" not in df.columns:
        return 0
    return int(df["삭제"].map(is_checked).sum())


def is_group_item_type(value) -> bool:
    return str(value or "").strip() in GROUP_ITEM_TYPES


def count_meaningful_rows(df: pd.DataFrame) -> int:
    clean_df = strip_delete_column(prepare_editor_df(df))
    return int(
        (
            clean_df["상품명"].astype(str).str.strip().ne("")
            | clean_df[BARCODE_COLUMN].astype(str).str.strip().ne("")
            | clean_df[SPEC_COLUMN].astype(str).str.strip().ne("")
        ).sum()
    )


def drop_checked_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty or "삭제" not in df.columns:
        return prepare_editor_df(df)
    keep = ~df["삭제"].map(is_checked)
    return prepare_editor_df(df.loc[keep].drop(columns=["삭제"], errors="ignore").reset_index(drop=True))


def append_blank_rows(df: pd.DataFrame, count: int) -> pd.DataFrame:
    blank = pd.DataFrame(
        [
            {
                "상품명": "",
                "유형": "부품",
                "담당자": DEFAULT_MANAGER,
                "거래처": DEFAULT_VENDOR,
                "필요 재고": 1,
                BARCODE_COLUMN: "",
                SPEC_COLUMN: "",
                COST_COLUMN: "",
            }
            for _ in range(count)
        ]
    )
    return prepare_editor_df(pd.concat([strip_delete_column(df), blank], ignore_index=True))


def editor_to_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, row in strip_delete_column(prepare_editor_df(df)).iterrows():
        item_name = str(row.get("상품명", "")).strip()
        barcode = str(row.get(BARCODE_COLUMN, "")).strip()
        spec = str(row.get(SPEC_COLUMN, "")).strip()
        if not item_name and not barcode and not spec:
            continue
        rows.append(
            {
                "item_name": item_name,
                "item_type": str(row.get("유형", "")).strip() or "부품",
                "manager": str(row.get("담당자", "")).strip(),
                "vendor": str(row.get("거래처", "")).strip(),
                "required_stock": int(row.get("필요 재고", 0) or 0),
                "barcode": barcode,
                "spec": spec,
                "barcode_spec": combine_barcode_spec(barcode, spec),
                "memo": str(row.get(COST_COLUMN, "")).strip(),
            }
        )
    return rows


def sample_template_df() -> pd.DataFrame:
    rows = [
        ["모노 / 높이조절 접시정리대: 소(25cm)", "완제품", "", "", 1, "8809722100614", "", ""],
        ["높이조절 접시정리대 소", "부품", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["부속품 패키지(접시대 다리4 높이조절 캡4)", "부속품", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["비닐 포장재", "포장재", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["상품 설명서", "인쇄물", "송광선 대리, 박지혜 주임", "성원 에드피아", 1, "", "", ""],
        ["로긴 통합 택배박스", "박스", DEFAULT_MANAGER, "서울지공", 1, "", "", ""],
        ["모노 / 높이조절 접시정리대: 와이드(50cm)", "완제품", "", "", 1, "8809722100638", "", ""],
        ["높이조절 접시정리대 와이드", "부품", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["부속품 패키지(접시대 다리4 높이조절 캡4)", "부속품", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["비닐 포장재", "포장재", DEFAULT_MANAGER, DEFAULT_VENDOR, 1, "", "", ""],
        ["상품 설명서", "인쇄물", "송광선 대리, 박지혜 주임", "성원 에드피아", 1, "", "", ""],
        ["로긴 통합 택배박스", "박스", DEFAULT_MANAGER, "서울지공", 1, "", "", ""],
    ]
    return pd.DataFrame(rows, columns=BOM_COLUMNS)


def import_excel(file_bytes: bytes) -> pd.DataFrame:
    df = select_import_sheet(file_bytes)
    rename_map = {}
    for column in df.columns:
        target = match_bom_column(column)
        if target:
            rename_map[column] = target
    df = df.rename(columns=rename_map)
    if LEGACY_BARCODE_SPEC_COLUMN in df.columns:
        legacy_values = df[LEGACY_BARCODE_SPEC_COLUMN].map(split_legacy_barcode_spec)
        if BARCODE_COLUMN not in df.columns:
            df[BARCODE_COLUMN] = legacy_values.map(lambda value: value[0])
        else:
            df[BARCODE_COLUMN] = df[BARCODE_COLUMN].where(
                df[BARCODE_COLUMN].fillna("").astype(str).str.strip().ne(""),
                legacy_values.map(lambda value: value[0]),
            )
        if SPEC_COLUMN not in df.columns:
            df[SPEC_COLUMN] = legacy_values.map(lambda value: value[1])
        else:
            df[SPEC_COLUMN] = df[SPEC_COLUMN].where(
                df[SPEC_COLUMN].fillna("").astype(str).str.strip().ne(""),
                legacy_values.map(lambda value: value[1]),
            )
    missing = [column for column in BOM_COLUMNS if column not in df.columns]
    for column in missing:
        df[column] = ""
    imported = df[BOM_COLUMNS].copy()
    imported["유형"] = imported.apply(infer_import_item_type, axis=1)
    meaningful = (
        imported["상품명"].map(clean_import_value).ne("")
        | imported[BARCODE_COLUMN].map(clean_import_value).ne("")
        | imported[SPEC_COLUMN].map(clean_import_value).ne("")
    )
    imported = imported.loc[meaningful].reset_index(drop=True)
    return prepare_editor_df(imported)


def select_import_sheet(file_bytes: bytes) -> pd.DataFrame:
    sheets = pd.read_excel(BytesIO(file_bytes), sheet_name=None, engine="openpyxl")
    best_df = pd.DataFrame()
    best_score = -1
    for sheet_df in sheets.values():
        candidate = normalize_import_sheet(sheet_df)
        score = score_import_sheet(candidate)
        if score > best_score:
            best_df = candidate
            best_score = score
    if best_score <= 0:
        raise ValueError("BOM 양식의 헤더를 찾지 못했습니다. 상품명/유형/필요 재고/88바코드 컬럼을 확인해주세요.")
    return best_df


def normalize_import_sheet(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if score_import_headers(df.columns) >= 2:
        normalized = df.copy()
        normalized.columns = unique_columns([str(column).strip() for column in normalized.columns])
        return normalized
    scan_limit = min(len(df), 12)
    for index in range(scan_limit):
        row_values = ["" if pd.isna(value) else str(value).strip() for value in df.iloc[index].tolist()]
        if score_import_headers(row_values) >= 2:
            normalized = df.iloc[index + 1 :].reset_index(drop=True).copy()
            normalized.columns = unique_columns(row_values)
            return normalized
    return df


def score_import_sheet(df: pd.DataFrame) -> int:
    if df.empty:
        return 0
    return score_import_headers(df.columns)


def score_import_headers(headers) -> int:
    return len({target for header in headers if (target := match_bom_column(header))})


def match_bom_column(column) -> str:
    normalized = normalize_header(column)
    for target, aliases in IMPORT_HEADER_ALIASES.items():
        if normalized in {normalize_header(alias) for alias in aliases}:
            return target
    return ""


def normalize_header(value) -> str:
    return str(value).strip().replace(" ", "").replace("\n", "").lower()


def unique_columns(headers: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for index, header in enumerate(headers, start=1):
        name = str(header).strip() or f"column_{index}"
        seen[name] = seen.get(name, 0) + 1
        result.append(name if seen[name] == 1 else f"{name}_{seen[name]}")
    return result


def infer_import_item_type(row: pd.Series) -> str:
    item_type = clean_import_value(row.get("유형", ""))
    if item_type:
        return item_type
    barcode = clean_import_value(row.get(BARCODE_COLUMN, ""))
    spec = clean_import_value(row.get(SPEC_COLUMN, ""))
    manager = clean_import_value(row.get("담당자", ""))
    vendor = clean_import_value(row.get("거래처", ""))
    if (barcode or spec) and not manager and not vendor:
        return "완제품"
    return "부품"


def clean_import_value(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    return "" if text.lower() in {"nan", "nat", "none"} else text


def bom_excel(df: pd.DataFrame, title: str) -> bytes:
    clean_df = strip_delete_column(prepare_editor_df(df))
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        sheet_name = "BOM"
        clean_df.to_excel(writer, index=False, sheet_name=sheet_name, startrow=1)
        workbook = writer.book
        worksheet = writer.sheets[sheet_name]
        title_format = workbook.add_format(
            {"bold": True, "font_size": 15, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "align": "center", "valign": "vcenter"}
        )
        header_format = workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1, "align": "center", "valign": "vcenter"}
        )
        cell_format = workbook.add_format({"border": 1, "border_color": "#D9D9D9", "valign": "vcenter"})
        group_format = workbook.add_format({"border": 1, "border_color": "#A6A6A6", "bg_color": "#D9D9D9", "valign": "vcenter"})
        number_format = workbook.add_format({"border": 1, "border_color": "#D9D9D9", "num_format": "0", "align": "center", "valign": "vcenter"})
        last_col = len(BOM_COLUMNS) - 1
        worksheet.merge_range(0, 0, 0, last_col, title, title_format)
        widths = {"상품명": 46, "유형": 14, "담당자": 18, "거래처": 18, "필요 재고": 10, BARCODE_COLUMN: 18, SPEC_COLUMN: 18, COST_COLUMN: 22}
        for idx, column in enumerate(BOM_COLUMNS):
            worksheet.write(1, idx, column, header_format)
            worksheet.set_column(idx, idx, widths.get(column, 16), number_format if column == "필요 재고" else cell_format)
        worksheet.outline_settings(True, False, False, False)
        for row_index, (_, row) in enumerate(clean_df.iterrows(), start=2):
            if is_group_item_type(row.get("유형")):
                worksheet.set_row(row_index, None, group_format)
            else:
                worksheet.set_row(row_index, None, None, {"level": 1})
        worksheet.freeze_panes(2, 0)
        worksheet.autofilter(1, 0, max(len(clean_df) + 1, 1), last_col)
    return output.getvalue()


def is_checked(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "checked", "삭제"}


def merge_unique(values: list[str]) -> list[str]:
    seen = set()
    merged = []
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            merged.append(normalized)
    return merged


def safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value)[:80] or "default"


def safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in value)[:80] or "BOM"


def inject_bom_css() -> None:
    st.markdown(
        """
        <style>
        div[data-testid="stAppViewContainer"],
        section[data-testid="stSidebar"] + div,
        .stApp {
            background:
                radial-gradient(circle at 48% 4%, rgba(18, 155, 139, 0.2), transparent 34%),
                linear-gradient(135deg, #031b18 0%, #062b26 46%, #073a34 100%) !important;
            color: #f2fffb !important;
            color-scheme: dark;
        }
        .bom-title {
            color: #ffffff;
            font-size: 1.34rem;
            font-weight: 950;
            margin: 0.1rem 0 0.75rem;
        }
        .bom-subtitle {
            color: #ffffff;
            font-size: 1.02rem;
            font-weight: 900;
            margin: 0.15rem 0 0.35rem;
        }
        .bom-outline-title {
            color: #dffaf4;
            font-size: 0.86rem;
            font-weight: 900;
            margin: 0.7rem 0 0.35rem;
        }
        div[data-testid="stExpander"]:has(.bom-outline-scroll) {
            background: rgba(6, 48, 43, 0.5);
            border-color: rgba(126, 197, 185, 0.24);
        }
        .bom-outline-scroll {
            border: 1px solid rgba(126, 197, 185, 0.22);
            border-radius: 6px;
            overflow-x: auto;
        }
        .bom-outline-table {
            border-collapse: collapse;
            font-size: 0.78rem;
            min-width: 1060px;
            width: 100%;
        }
        .bom-outline-table th,
        .bom-outline-table td {
            border: 1px solid rgba(210, 232, 228, 0.18);
            color: #f7fffc;
            padding: 0.36rem 0.5rem;
            white-space: nowrap;
        }
        .bom-outline-table th {
            background: rgba(31, 78, 120, 0.86);
            text-align: center;
        }
        .bom-outline-table tr.group td {
            background: rgba(210, 210, 210, 0.25);
            font-weight: 900;
        }
        .bom-outline-table tr.child td {
            background: rgba(8, 39, 36, 0.84);
        }
        .bom-outline-table .empty {
            color: #b2d5cd;
            text-align: center;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) {
            background: rgba(6, 48, 43, 0.66) !important;
            border: 1px solid rgba(87, 178, 165, 0.28);
            border-radius: 8px;
            padding: 0.85rem;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel),
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) * {
            color: #f2fffb;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) input,
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) textarea,
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) [data-baseweb="select"] > div,
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) [data-testid="stDataFrame"],
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) [data-testid="stDataEditor"] {
            background: rgba(5, 38, 34, 0.9) !important;
            border-color: rgba(126, 197, 185, 0.34) !important;
            color: #f2fffb !important;
        }
        div[data-testid="stVerticalBlock"] div[data-testid="stVerticalBlockBorderWrapper"]:has(#bom_editor_panel) button {
            background: linear-gradient(180deg, rgba(11, 83, 75, 0.94), rgba(5, 45, 41, 0.94)) !important;
            border-color: rgba(126, 197, 185, 0.34) !important;
            color: #ffffff !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
