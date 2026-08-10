# Spreadsheet Recovery Audit

This audit is based on git history searches for `st.data_editor`, `data_editor`,
`editable`, `AgGrid`, `spreadsheet`, and current page code.

| Page | Feature | SQLite Editable Table Existed | Current Editable Table Exists | Current Normal | Supabase Connected | Restore Needed |
| --- | --- | --- | --- | --- | --- | --- |
| 회의자료 | 생산요청 리스트 편집 | Yes | Yes | Partial: visibility/save failures reported | Yes, via SQLAlchemy PostgreSQL compatibility | Yes |
| 회의자료 | 행사 일정 편집 | Yes | Yes | Partial: visibility/save failures reported | Yes, via SQLAlchemy PostgreSQL compatibility | Yes |
| 회의자료 | 진행사항 편집 | Yes | Yes | Partial: visibility/save failures reported | Yes, via SQLAlchemy PostgreSQL compatibility | Yes |
| 일정관리 | 이번 주 핵심 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy PostgreSQL compatibility | Watch |
| 일정관리 | 시간대별 일정 편집 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy PostgreSQL compatibility | Watch |
| 재고관리 | MRP PR 생성 선택표 | Yes | Yes | Mostly read/select workflow | Yes, via SQLAlchemy ORM | No direct restore |
| 재고관리 | 발주추천 PR 생성 선택표 | Yes | Yes | Mostly read/select workflow | Yes, via SQLAlchemy ORM | No direct restore |
| 재고관리 | 자재/반제품 관리 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 재고관리 | 입고내역 편집 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 재고관리 | 생산계획 편집 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 구매관리 | 구매요청 목록 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 구매관리 | 견적 비교 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 구매관리 | PO 확인 리스트 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 구매관리 | 예산/평가기준 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| BOM 관리 | BOM 등록/수정 | Yes | Yes | Needs live verification | Yes, via SQLAlchemy ORM | Watch |
| 품목 마스터 | 단일 행 편집 forms | No spreadsheet in current history hits | No | Form workflow | Yes, via SQLAlchemy ORM | No |
| 3D 창고관리 | 배치 편집 | Custom canvas editor | Custom canvas editor | Manual save only | Yes, server-side save path | Watch |

## Current Fix Batch

- Added a final CSS override to keep `st.data_editor` canvas/table content visible.
- Kept form submit button text visible, including disabled submit buttons.
- Changed meeting history Excel export to lazy generation so meeting page initial render no longer builds the full history workbook.
- Added page-level SQL query profiling in session state:
  - `db_query_profile_events`
  - `db_query_profile_summary`
- Stopped global `st.cache_data.clear()` on generic save success.
- Kept meeting editor values on save failure instead of allowing a full render exception to wipe the workflow.

## Remaining Live Verification

The local shell cannot run Python in this environment, so Streamlit runtime verification must be performed in the app runtime.
Use the new `db_query_profile_summary` and logs to compare API calls and render time per page after live navigation.
