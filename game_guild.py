import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import plotly.express as px
import json
import time
from datetime import datetime, timedelta
import easyocr
import re

# --- 1. 페이지 설정 및 디자인 ---
st.set_page_config(
    page_title="이세계 판타지 라이프 - 길드 매니저",
    page_icon="⚔️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 커스텀 CSS (게임 분위기)
st.markdown("""
    <style>
    .main {background-color: #0e1117;}
    h1, h2, h3 {color: #ffaa00;}
    .stMetric {background-color: #262730; padding: 10px; border-radius: 5px; border: 1px solid #444;}
    </style>
    """, unsafe_allow_html=True)

# --- 2. 하이브리드 Firebase 초기화 (핵심 기능) ---
@st.cache_resource
def init_firestore():
    """
    로컬(json)과 클라우드(secrets) 환경을 모두 지원하는 하이브리드 초기화 함수
    """
    try:
        if not firebase_admin._apps:  # 앱이 초기화되지 않은 경우에만 실행
            try:
                # 1순위: Streamlit Cloud Secrets 확인
                if "firebase" in st.secrets:
                    # secrets.toml의 정보를 dict로 변환
                    firebase_info = dict(st.secrets["firebase"])
                    cred = credentials.Certificate(firebase_info)
                    print("✅ Streamlit Cloud Secrets로 인증 성공")
                
                # 2순위: 로컬 JSON 파일 확인
                else:
                    cred = credentials.Certificate("serviceAccountKey.json")
                    print("✅ 로컬 JSON 파일로 인증 성공")
                
                firebase_admin.initialize_app(cred)
            except Exception as inner_e:
                st.error(f"❌ 인증 파일 로드 실패: {inner_e}")
                st.stop()
                
        db = firestore.client()
        return db
    except Exception as e:
        st.error(f"🔥 Firebase 연결 오류: {e}")
        st.stop()

db = init_firestore()

# --- 3. 세션 상태 관리 ---
if 'is_logged_in' not in st.session_state:
    st.session_state['is_logged_in'] = False
if 'guild_name' not in st.session_state:
    st.session_state['guild_name'] = ""
if 'guild_id' not in st.session_state:
    st.session_state['guild_id'] = ""

# --- 4. 헬퍼 함수 (DB CRUD & OCR) ---
def get_guild_members(guild_id):
    docs = db.collection('guilds').document(guild_id).collection('members').stream()
    data = []
    for doc in docs:
        d = doc.to_dict()
        d['id'] = doc.id
        data.append(d)
    return pd.DataFrame(data)

# --- 헬퍼 함수: OCR 분석 (새로 추가) ---
@st.cache_resource
def load_ocr_reader():
    import easyocr
    return easyocr.Reader(['ko', 'en']) # 한국어, 영어 지원

def run_ocr_scan(image_file):
    try:
        reader = load_ocr_reader()
        image_bytes = image_file.read()
        result = reader.readtext(image_bytes, detail=0) # 텍스트만 추출
        
        text_full = " ".join(result)
        st.toast(f"읽은 내용: {text_full[:30]}...", icon="👀")
        
        # 간단한 파싱 로직 (게임 화면에 따라 수정 필요)
        found_dmg = 0.0
        found_kill = 0
        
        # 숫자 추출 정규식
        import re
        # "1.5억" 또는 "123,456" 같은 숫자 찾기
        numbers = re.findall(r"[\d]+[.,]?[\d]*", text_full)
        
        # (알고리즘: 화면에서 가장 큰 소수점 숫자를 피해량으로, 정수를 킬수로 추정)
        # 실제로는 '피해량' 키워드 뒤의 숫자를 찾는 게 정확합니다.
        # 여기서는 예시로 단순하게 구현합니다.
        
        for num in numbers:
            clean_num = num.replace(',', '')
            try:
                val = float(clean_num)
                # 피해량은 보통 억 단위라 소수점이거나 큼
                if val > found_dmg and '.' in num: 
                    found_dmg = val
                # 격퇴수는 정수이고 보통 100 이하
                if val > found_kill and '.' not in num and val < 100:
                    found_kill = int(val)
            except:
                continue
                
        return found_dmg, found_kill, "분석 완료"
    except Exception as e:
        return 0.0, 0, f"오류 발생: {e}"

def add_update_member(guild_id, name, cp, role, doc_id=None):
    # 1. 현재 길드원 목록을 가져와서 인원 수 체크
    current_members = get_guild_members(guild_id)
    
    # 제한 인원 설정
    limits = {
        "길드장": 1,
        "부길드장": 3,
        "정예": 4
    }
    
    # 신규 등록이거나, 역할을 변경하는 경우 인원 제한 체크
    if role in limits:
        # 해당 직책을 가진 사람 수 계산
        if not current_members.empty and 'role' in current_members.columns:
            count = len(current_members[current_members['role'] == role])
            
            # 수정(Update)일 경우, 자기 자신은 카운트에서 제외해야 함 (이미 그 직책인 경우)
            if doc_id:
                existing_user = current_members[current_members['id'] == doc_id]
                if not existing_user.empty and existing_user.iloc[0].get('role') == role:
                    count -= 1
            
            # 제한 확인
            if count >= limits[role]:
                return False, f"⚠️ '{role}' 정원 초과입니다. (최대 {limits[role]}명)"

    # 2. DB 저장/수정 로직
    collection_ref = db.collection('guilds').document(guild_id).collection('members')
    
    # 직책이 없으면 '일반'으로 저장
    final_role = role if role and role != "(선택 안 함)" else "일반"
    
    data = {
        'name': name,
        'cp': int(cp),
        'role': final_role,  # 'job' 대신 'role' 사용
        'updated_at': firestore.SERVER_TIMESTAMP
    }
    
    if doc_id:
        collection_ref.document(doc_id).update(data)
        return True, "수정 완료"
    else:
        # 이름 중복 체크 (선택 사항)
        collection_ref.add(data)
        return True, "등록 완료"

def delete_member(guild_id, doc_id):
    db.collection('guilds').document(guild_id).collection('members').document(doc_id).delete()

# 간단한 OCR 시뮬레이션 함수 (실제 OCR 라이브러리 연동 위치)
# EasyOCR 등을 사용할 경우 여기에 구현
def simulate_ocr_process(uploaded_file):
    # 실제 구현 시: reader.readtext(image) 사용
    time.sleep(1.5) # 처리 시간 시뮬레이션
    return 15000000, "OCR_User_01" # 가상의 인식된 투력과 이름 반환

# [새로 추가] 날짜별 데이터 가져오기
def get_daily_data(guild_id, date_str):
    doc_ref = db.collection('guilds').document(guild_id).collection('daily_records').document(date_str)
    doc = doc_ref.get()
    if doc.exists:
        return doc.to_dict()
    return {}

# [새로 추가] 날짜별 데이터 저장하기
def save_daily_data(guild_id, date_str, data_dict):
    doc_ref = db.collection('guilds').document(guild_id).collection('daily_records').document(date_str)
    doc_ref.set(data_dict, merge=True)

# [새로 추가] 특정 기간 동안의 모든 기록 가져오기 (그래프용)
def fetch_period_records(guild_id, start_date, end_date):
    # start_date부터 end_date까지 하루씩 반복하며 데이터 수집
    period_data = []
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        daily_doc = db.collection('guilds').document(guild_id).collection('daily_records').document(date_str).get()
        
        if daily_doc.exists:
            records = daily_doc.to_dict()
            for mem_id, data in records.items():
                # 그래프 그리기 편하게 데이터 구조 변경 (Flatten)
                row = {'date': current_date, 'member_id': mem_id}
                row.update(data) # 기존 데이터(기부 내역, 현자 내역) 합치기
                period_data.append(row)
                
        current_date += timedelta(days=1)
    
    return pd.DataFrame(period_data)

# --- 5. 로그인 및 길드 생성 화면 (사이드바) ---
def login_ui():
    st.sidebar.title("🛡️ 이세계 길드 관리자")
    
    # 탭으로 분리: 로그인 vs 회원가입
    tab1, tab2 = st.sidebar.tabs(["🔑 로그인", "✨ 길드 생성"])
    
    # [탭 1] 기존 로그인 기능
    with tab1:
        st.subheader("길드 접속")
        input_guild_id = st.text_input("길드 ID", placeholder="예: my_guild", key="login_id")
        input_password = st.text_input("비밀번호", type="password", key="login_pw")
        
        if st.button("접속하기", key="btn_login"):
            if not input_guild_id or not input_password:
                st.error("ID와 비밀번호를 입력해주세요.")
            else:
                guild_ref = db.collection('guilds').document(input_guild_id)
                guild_doc = guild_ref.get()
                
                if guild_doc.exists:
                    data = guild_doc.to_dict()
                    real_pw = data.get('password', '') # DB에 저장된 비번 가져오기
                    
                    if real_pw == input_password:
                        st.session_state['is_logged_in'] = True
                        st.session_state['guild_id'] = input_guild_id
                        st.session_state['guild_name'] = data.get('name', input_guild_id)
                        st.success("로그인 성공!")
                        time.sleep(0.5)
                        st.rerun()
                    else:
                        st.error("비밀번호가 틀렸습니다.")
                else:
                    st.error("존재하지 않는 길드 ID입니다. [길드 생성] 탭에서 먼저 만들어주세요.")

    # [탭 2] 신규 길드 생성 기능 (새로 추가됨!)
    with tab2:
        st.subheader("신규 등록")
        new_guild_id = st.text_input("사용할 길드 ID (영문)", placeholder="예: dragon_knights", key="new_id")
        new_guild_name = st.text_input("길드 이름 (표시용)", placeholder="예: 드래곤 기사단", key="new_name")
        new_password = st.text_input("설정할 비밀번호", type="password", key="new_pw")
        
        if st.button("길드 만들기", key="btn_create"):
            if new_guild_id and new_guild_name and new_password:
                # 1. 중복 체크
                doc_ref = db.collection('guilds').document(new_guild_id)
                if doc_ref.get().exists:
                    st.error("이미 사용 중인 길드 ID입니다. 다른 ID를 써주세요.")
                else:
                    # 2. DB에 저장
                    doc_ref.set({
                        'name': new_guild_name,
                        'password': new_password,
                        'created_at': firestore.SERVER_TIMESTAMP
                    })
                    st.success(f"🎉 '{new_guild_name}' 생성 완료! [로그인] 탭에서 접속하세요.")
            else:
                st.warning("모든 칸을 입력해주세요.")

def logout():
    st.session_state['is_logged_in'] = False
    st.session_state['guild_id'] = ""
    st.rerun()

# --- 6. 메인 애플리케이션 로직 ---
def main_app():
    st.title(f"🏰 {st.session_state['guild_name']} 관리 시스템")
    
    # 상단 메뉴
    tab1, tab2, tab3 = st.tabs(["📊 대시보드", "👥 멤버 관리", "📅 일일 숙제 & 분석"])

    # --- TAB 1: 대시보드 (기존과 동일) ---
    with tab1:
        st.header("길드 현황판")
        df = get_guild_members(st.session_state['guild_id'])
        if not df.empty:
            col1, col2, col3 = st.columns(3)
            col1.metric("총 길드원", f"{len(df)}명")
            total_cp = df['cp'].sum()
            col2.metric("총 전투력", f"{total_cp:,.0f}억")
            avg_cp = total_cp / len(df)
            col3.metric("평균 전투력", f"{avg_cp:,.1f}억")
            st.divider()
            if 'role' in df.columns:
                role_counts = df['role'].value_counts().reset_index()
                role_counts.columns = ['직책', '인원']
                st.bar_chart(role_counts.set_index('직책'))
        else:
            st.info("아직 등록된 길드원이 없습니다.")

    # --- TAB 2: 멤버 관리 (기존과 동일) ---
    with tab2:
        st.header("길드원 명부 관리")
        # 1. 신규 등록
        with st.expander("➕ 멤버 수동 등록", expanded=False):
            with st.form("add_member_form"):
                c1, c2, c3 = st.columns(3)
                new_name = c1.text_input("닉네임")
                new_cp = c2.number_input("전투력 (단위: 억)", min_value=0.0, step=0.1, format="%.1f") 
                role_options = ["(선택 안 함)", "길드장", "부길드장", "정예"]
                new_role = c3.selectbox("직책", role_options)
                if st.form_submit_button("등록"):
                    if new_name:
                        success, msg = add_update_member(st.session_state['guild_id'], new_name, new_cp, new_role)
                        if success:
                            st.success(f"{new_name} 등록 완료!")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(msg)
                    else:
                        st.warning("닉네임을 입력하세요.")

        # 2. 조회 및 수정
        if not df.empty:
            st.caption("💡 전투력은 '억' 단위입니다.")
            edited_df = st.data_editor(
                df[['name', 'cp', 'role', 'id']],
                column_config={
                    "name": "닉네임",
                    "cp": st.column_config.NumberColumn("전투력 (억)", format="%.1f억"),
                    "role": st.column_config.SelectboxColumn("직책", options=["길드장", "부길드장", "정예", "일반"], required=False),
                    "id": st.column_config.TextColumn("ID", disabled=True)
                },
                hide_index=True,
                use_container_width=True,
                key="member_editor"
            )
            with st.popover("🗑️ 멤버 삭제"):
                del_target = st.selectbox("삭제할 닉네임", df['name'].tolist())
                if st.button("영구 삭제"):
                    mem_id = df[df['name'] == del_target]['id'].values[0]
                    delete_member(st.session_state['guild_id'], mem_id)
                    st.rerun()

    # --- TAB 3: 일일 숙제 & 분석 (OCR + 그래프 통합) ---
    with tab3:
        st.header("📝 일일 활동 기록")
        
        col_date, col_upload = st.columns([1, 2])
        selected_date = col_date.date_input("날짜 선택", datetime.now())
        date_str = selected_date.strftime("%Y-%m-%d")
        
        # 스캔된 값 임시 저장소 초기화
        if 'scan_dmg' not in st.session_state: st.session_state['scan_dmg'] = 0.0
        if 'scan_kill' not in st.session_state: st.session_state['scan_kill'] = 0
        
        with col_upload:
            uploaded_file = st.file_uploader("📸 현자/기부 스크린샷", type=['png', 'jpg'])
            
            # [OCR] 스마트 분석 버튼
            if uploaded_file:
                if st.button("🔍 스크린샷 스마트 분석 (Beta)", type="primary"):
                    with st.spinner("이미지를 분석 중입니다..."):
                        dmg, kill, msg = run_ocr_scan(uploaded_file)
                        st.session_state['scan_dmg'] = dmg
                        st.session_state['scan_kill'] = kill
                        
                        if dmg > 0 or kill > 0:
                            st.success(f"분석 성공! 피해량: {dmg}억 / 격퇴: {kill}회")
                        else:
                            st.warning("숫자를 찾지 못했습니다. 직접 입력해주세요.")
                        uploaded_file.seek(0) # 파일 포인터 초기화

        st.divider()

        # 1. 데이터 입력 표 (Data Editor)
        members_df = get_guild_members(st.session_state['guild_id'])
        
        if members_df.empty:
            st.warning("먼저 [멤버 관리] 탭에서 길드원을 등록해주세요.")
        else:
            daily_record = get_daily_data(st.session_state['guild_id'], date_str)
            
            display_data = []
            for index, row in members_df.iterrows():
                mem_id = row['id']
                record = daily_record.get(mem_id, {})
                
                display_data.append({
                    "id": mem_id,
                    "name": row['name'],
                    "don_basic": record.get("don_basic", 0),
                    "don_inter": record.get("don_inter", 0),
                    "don_adv": record.get("don_adv", 0),
                    "don_item": record.get("don_item", 0),
                    "sage_dmg": record.get("sage_dmg", 0.0),
                    "sage_kill": record.get("sage_kill", 0)
                })
            
            # 스캔 결과 알림
            if st.session_state['scan_dmg'] > 0:
                st.info(f"💡 방금 스캔된 결과: **피해량 {st.session_state['scan_dmg']}억 / 격퇴 {st.session_state['scan_kill']}회** (아래 표에서 해당 멤버에게 입력해주세요)")
            
            record_df = pd.DataFrame(display_data)
            
            st.caption(f"📅 {date_str} 활동 입력")
            edited_record = st.data_editor(
                record_df,
                column_config={
                    "id": None,
                    "name": st.column_config.TextColumn("닉네임", disabled=True),
                    "don_basic": st.column_config.NumberColumn("기부(초급)", min_value=0, max_value=4, step=1),
                    "don_inter": st.column_config.NumberColumn("기부(중급)", min_value=0, max_value=1, step=1),
                    "don_adv": st.column_config.NumberColumn("기부(고급)", min_value=0, max_value=1, step=1),
                    "don_item": st.column_config.NumberColumn("기부(템)", min_value=0, max_value=2, step=1),
                    "sage_dmg": st.column_config.NumberColumn("🔥 피해량(억)", format="%.1f"),
                    "sage_kill": st.column_config.NumberColumn("☠️ 격퇴", step=1),
                },
                hide_index=True,
                use_container_width=True,
                height=400
            )
            
            if st.button("💾 기록 저장", type="primary", use_container_width=True):
                data_to_save = {}
                for index, row in edited_record.iterrows():
                    data_to_save[row['id']] = {
                        "don_basic": row['don_basic'],
                        "don_inter": row['don_inter'],
                        "don_adv": row['don_adv'],
                        "don_item": row['don_item'],
                        "sage_dmg": row['sage_dmg'],
                        "sage_kill": row['sage_kill']
                    }
                save_daily_data(st.session_state['guild_id'], date_str, data_to_save)
                st.toast(f"✅ {date_str} 기록 저장 완료!", icon="💾")

        st.divider()
        
        # 2. 분석 그래프 섹션 (기존 기능 유지)
        st.header("📈 활동 분석 그래프")
        
        analysis_range = st.radio("분석 기간", ["최근 7일 (주간)", "최근 30일 (월간)"], horizontal=True)
        days_to_subtract = 7 if analysis_range == "최근 7일 (주간)" else 30
        
        end_date_anal = datetime.now().date()
        start_date_anal = end_date_anal - timedelta(days=days_to_subtract-1)
        
        period_df = fetch_period_records(st.session_state['guild_id'], start_date_anal, end_date_anal)
        
        if period_df.empty:
            st.info("데이터가 없습니다.")
        else:
            merged_df = pd.merge(period_df, members_df[['id', 'name']], left_on='member_id', right_on='id', how='left')
            
            anal_tab1, anal_tab2 = st.tabs(["🔥 현자 도전", "💰 기부 현황"])
            
            with anal_tab1:
                st.subheader("일별 현자 피해량 추이")
                chart_data = merged_df[['date', 'name', 'sage_dmg']].rename(columns={'sage_dmg': '피해량'})
                st.line_chart(chart_data, x='date', y='피해량', color='name')

            with anal_tab2:
                st.subheader("기간 내 총 기부")
                donation_sum = merged_df.groupby('name')[['don_basic', 'don_inter', 'don_adv', 'don_item']].sum().reset_index()
                donation_melted = donation_sum.melt('name', var_name='기부유형', value_name='횟수')
                
                import altair as alt
                chart = alt.Chart(donation_melted).mark_bar().encode(
                    x='name', y='횟수', color='기부유형', tooltip=['name', '기부유형', '횟수']
                ).interactive()
                st.altair_chart(chart, use_container_width=True)

# --- 실행 흐름 제어 ---
if __name__ == "__main__":
    if st.session_state['is_logged_in']:
        main_app()
    else:
        login_ui()