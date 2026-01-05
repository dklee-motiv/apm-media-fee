# apm_media_fee.py
import streamlit as st
import pandas as pd
import numpy as np
from io import BytesIO
from datetime import date
import calendar
import re
from decimal import Decimal, ROUND_HALF_UP, getcontext

# Decimal 정밀도 여유 있게
getcontext().prec = 28

# =====================================================
# 페이지 설정
# =====================================================
st.set_page_config(page_title="APM CTV 정산 자동화", layout="wide")
st.title("📊 APM CTV 매체비 정산 자동화")

# =====================================================
# 유틸 함수
# =====================================================
def parse_sec(v):
    if pd.isna(v):
        return None
    s = str(v).strip()
    m = re.search(r"(\d+)", s)  # '15초' 같은 형태 대응
    return int(m.group(1)) if m else None


def normalize_advertiser(v):
    if pd.isna(v):
        return v
    return str(v).replace("비용무료", "").strip()


def normalize_carrier(v):
    s = str(v).upper()
    if "SKB" in s:
        return "SKB"
    if "UPLUS" in s or "LGU" in s or "U+" in s:
        return "LGU"
    if "KT" in s:
        return "KT"
    return None


def period_text(year, month):
    last = calendar.monthrange(year, month)[1]
    return f"{str(year)[2:]}.{month:02d}.01~{str(year)[2:]}.{month:02d}.{last:02d}"


def excel_won_from_price_view(price, view):
    """
    매체비 = 단가 * 재생완료수 를 엑셀 반올림(1원 단위 HALF_UP)으로 계산
    """
    if price is None:
        price = 0
    if view is None or (isinstance(view, float) and np.isnan(view)):
        view = 0
    cost = Decimal(str(price)) * Decimal(str(view))
    return int(cost.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# =====================================================
# 사이드바 설정
# =====================================================
st.sidebar.header("⚙ 정산 설정")

# [1] DX 어워드 설정
st.sidebar.subheader("[1] DX 어워드 설정")
dx_enabled = st.sidebar.checkbox("DX 어워드 적용", value=False, key="dx_enabled")

dx_sec = st.sidebar.radio(
    "DX 어워드 적용 초수",
    options=[15, 30],
    format_func=lambda x: f"{x}초",
    disabled=not dx_enabled,
    key="dx_sec"
)

DX_FREE_VIEWS = 300_000 if dx_sec == 15 else 150_000
st.sidebar.caption(f"무상 View: {DX_FREE_VIEWS:,} (QTONE 전용)")

# [2] DX 대상 광고주
st.sidebar.subheader("[2] DX 어워드 대상 광고주")
if "qtone_advs" not in st.session_state:
    dx_adv = st.sidebar.selectbox(
        "광고주 선택",
        options=["(RAW 업로드 필요)"],
        disabled=True,
        key="dx_adv_disabled"
    )
else:
    dx_adv = st.sidebar.selectbox(
        "광고주 선택",
        options=["(선택 안 함)"] + st.session_state["qtone_advs"],
        disabled=not dx_enabled,
        key="dx_adv"
    )
    if dx_adv == "(선택 안 함)":
        dx_adv = None

# [3] QTONE 단가
st.sidebar.subheader("[3] QTONE 단가")
qt_price = {
    15: st.sidebar.number_input("QTONE 15초", value=2.0, min_value=0.0, step=0.1),
    30: st.sidebar.number_input("QTONE 30초", value=4.0, min_value=0.0, step=0.1),
    60: st.sidebar.number_input("QTONE 60초", value=16.0, min_value=0.0, step=0.1),
}

# [4] 어드레서블 단가
st.sidebar.subheader("[4] 어드레서블 단가")
ad_price = {
    15: st.sidebar.number_input("ADDR 15초", value=5.0, min_value=0.0, step=0.1),
    30: st.sidebar.number_input("ADDR 30초", value=10.0, min_value=0.0, step=0.1),
    60: st.sidebar.number_input("ADDR 60초", value=20.0, min_value=0.0, step=0.1),
}

# =====================================================
# 메인 UI
# =====================================================
year = st.number_input("정산 연도", value=date.today().year)
month = st.selectbox("정산 월", list(range(1, 13)), index=date.today().month - 1)
PERIOD = period_text(year, month)

uploaded = st.file_uploader("RAW 엑셀 업로드", type=["xlsx"])
if not uploaded:
    st.stop()

# =====================================================
# RAW 로드 & 전처리
# =====================================================
raw = pd.read_excel(uploaded)

raw.columns = raw.columns.str.strip()
raw["광고주"] = raw["광고주"].apply(normalize_advertiser)
raw["통신사"] = raw["서비스"].apply(normalize_carrier)
raw["초수"] = raw["재생시간"].apply(parse_sec)

raw = raw[
    raw["상품"].isin(["QTONE", "ADDR"])
    & raw["통신사"].notna()
    & raw["초수"].notna()
].copy()

raw["노출수"] = pd.to_numeric(raw["노출수"], errors="coerce").fillna(0)
raw["재생완료수"] = pd.to_numeric(raw["재생완료수"], errors="coerce").fillna(0)

raw["캠페인명"] = raw.apply(lambda r: f"{r['광고주']} {int(r['초수'])}초", axis=1)

# QTONE 광고주 목록 → 사이드바 활성화
st.session_state["qtone_advs"] = (
    raw[raw["상품"] == "QTONE"]["광고주"]
    .sort_values()
    .unique()
    .tolist()
)

# =====================================================
# 10% 할증 캠페인
# =====================================================
st.sidebar.subheader("➕ CPV 10% 할증 캠페인")
premium_campaigns = st.sidebar.multiselect(
    "단가 10% 할증 적용 캠페인 선택",
    options=sorted(raw["캠페인명"].unique().tolist()),
    default=[],
    key="premium_campaigns"
)

# =====================================================
# 집계
# =====================================================
grp = (
    raw.groupby(["상품", "통신사", "광고주", "초수", "캠페인명"], as_index=False)
    .agg({"노출수": "sum", "재생완료수": "sum"})
)
grp["기간"] = PERIOD

def unit_price(r):
    sec = int(r["초수"])
    if r["상품"] == "QTONE":
        price = qt_price.get(sec, 0)
    else:
        price = ad_price.get(sec, 0)

    if r["캠페인명"] in premium_campaigns:
        price = price * 1.1

    return float(price)

grp["단가"] = grp.apply(unit_price, axis=1)
grp["매체비"] = grp.apply(lambda r: excel_won_from_price_view(r["단가"], r["재생완료수"]), axis=1)

# =====================================================
# DX 어워드 적용 (QTONE만)
# =====================================================
rows = []

for (prod, carrier), g in grp.groupby(["상품", "통신사"]):
    if prod != "QTONE" or not dx_enabled or not dx_adv:
        rows.append(g)
        continue

    g = g.copy()
    total_imp = float(g["노출수"].sum())
    total_view = float(g["재생완료수"].sum())
    vtr = (total_view / total_imp) if total_imp else 0

    mask = (g["광고주"] == dx_adv) & (g["초수"] == dx_sec)
    if not mask.any():
        rows.append(g)
        continue

    target = g[mask].iloc[0].copy()

    free_view = min(int(DX_FREE_VIEWS), int(target["재생완료수"]))
    free_imp = (free_view / vtr) if vtr else 0

    free = target.copy()
    free["캠페인명"] = str(free["캠페인명"]) + " (DX 무상)"
    free["재생완료수"] = free_view
    free["노출수"] = free_imp
    free["매체비"] = 0

    paid = target.copy()
    paid["캠페인명"] = str(paid["캠페인명"]) + " (DX 유상)"
    paid["재생완료수"] = int(target["재생완료수"]) - free_view
    paid["노출수"] = float(target["노출수"]) - float(free_imp)
    paid["매체비"] = excel_won_from_price_view(paid["단가"], paid["재생완료수"])

    others = g[~mask]
    rows.append(pd.concat([free.to_frame().T, paid.to_frame().T, others], ignore_index=True))

final = pd.concat(rows, ignore_index=True)

# =====================================================
# eCPM 계산 (✅ 반올림 없이 원값 유지)
# =====================================================
def calc_ecpm_raw(r):
    imp = Decimal(str(float(r["노출수"])))
    if imp <= 0:
        return 0.0
    cost = Decimal(str(int(r["매체비"])))
    ecpm = (cost / imp) * Decimal("1000")
    # ✅ 원값 그대로 (float로만 변환)
    return float(ecpm)

final["eCPM_raw"] = final.apply(calc_ecpm_raw, axis=1)

# 화면 표시용: 보기 좋게만(원하면 1자리/2자리/6자리 선택)
display_digits = st.selectbox("eCPM 표시 자릿수", [1, 2, 6], index=2)
final["eCPM"] = final["eCPM_raw"].map(lambda x: float(Decimal(str(x)).quantize(
    Decimal("0." + "0"*(display_digits-1) + "1") if display_digits > 0 else Decimal("1"),
    rounding=ROUND_HALF_UP
)))

# =====================================================
# 최종 정리 (요청한 컬럼 순서 + 정렬)
# =====================================================
final = final[
    ["상품", "통신사", "광고주", "캠페인명", "기간", "초수", "노출수", "재생완료수", "eCPM", "매체비", "eCPM_raw"]
].sort_values(["상품", "통신사", "광고주", "초수", "캠페인명"])

# =====================================================
# 출력
# =====================================================
st.subheader("📄 정산 결과")
st.dataframe(
    final[["상품", "통신사", "광고주", "캠페인명", "기간", "초수", "노출수", "재생완료수", "eCPM"]],
    use_container_width=True
)

# =====================================================
# 다운로드 (엑셀에는 eCPM_raw도 같이 넣어서 엑셀과 직접 대조 가능)
# =====================================================
buf = BytesIO()
with pd.ExcelWriter(buf, engine="openpyxl") as w:
    final.to_excel(w, index=False, sheet_name="정산결과")

st.download_button(
    "📥 엑셀 다운로드",
    data=buf.getvalue(),
    file_name=f"APM_정산결과_{year}{month:02d}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
