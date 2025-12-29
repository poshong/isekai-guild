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

# --- 헬퍼 함수: OCR 분석 (스마트 버전) ---
@st.cache_resource
def load_ocr_reader():
    import easyocr
    return easyocr.Reader(['ko', 'en']) 

def run_ocr_scan(image_file):
    try:
        reader = load_ocr_reader()
        image_bytes = image_file.read()
        result = reader.readtext(image_bytes, detail=0)
        
        # 1. 기부 명단 분석 모드인지 확인 (키워드: '기부')
        full_text = " ".join(result)
        
        if "기부" in full_text and "님이" in full_text:
            # 기부 데이터 저장소: { '닉네임': {'basic': 0, 'inter': 0, ...} }
            donation_counts = {}
            
            # 한 줄씩 읽으면서 분석
            for line in result:
                if "님이" in line and "기부" in line:
                    # 닉네임 추출 ( '님이' 앞의 단어 )
                    parts = line.split("님이")
                    if len(parts) > 0:
                        # 앞부분에서 마지막 단어가 닉네임일 확률이 높음 (시간 00:03 등 제외)
                        name_part = parts[0].strip()
                        name_tokens = name_part.split()
                        detected_name = name_tokens[-1] if name_tokens else ""
                        
                        if not detected_name: continue

                        if detected_name not in donation_counts:
                            donation_counts[detected_name] = {'basic':0, 'inter':0, 'adv':0, 'item':0}
                        
                        # 기부 종류 판별 (횟수 누적)
                        # 보통 로그는 "1회"씩 찍히므로 1씩 더함. (4회 라고 적힌 경우 등은 추가 로직 필요하나 일단 1회 기준)
                        add_val = 1
                        # 만약 "4회" 같은 텍스트가 있으면 추출 시도
                        import re
                        count_match = re.search(r'(\d+)회', line)
                        if count_match:
                            add_val = int(count_match.group(1))

                        if "초급" in line: donation_counts[detected_name]['basic'] += add_val
                        elif "중급" in line: donation_counts[detected_name]['inter'] += add_val
                        elif "고급" in line: donation_counts[detected_name]['adv'] += add_val
                        elif "아이템" in line: donation_counts[detected_name]['item'] += add_val
            
            return "donation", donation_counts, "기부 내역 분석 완료"

        else:
            # 2. 현자 도전 (기존 로직)
            found_dmg = 0.0
            found_kill = 0
            
            import re
            numbers = re.findall(r"[\d]+[.,]?[\d]*", full_text)
            
            for num in numbers:
                clean_num = num.replace(',', '')
                try:
                    val = float(clean_num)
                    if val > found_dmg and '.' in num: found_dmg = val
                    if val > found_kill and '.' not in num and val < 100: found_kill = int(val)
                except: continue
                    
            return "sage", {"dmg": found_dmg, "kill": found_kill}, "현자 도전 분석 완료"
            
    except Exception as e:
        return "error", {}, f"오류 발생: {e}"

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

#테마 설정 상관없이 무조건 밝은색 화면으로 고정
# CSS 스타일 강제 적용
    st.markdown("""
        <style>
        .stApp, [data-testid="stAppViewContainer"] {
            background-color: white !important;
            color: black !important;
        }
        div[data-testid="stMetric"] {
            background-color: #F0F2F6 !important;
            border: 1px solid #D6D6D6 !important;
            padding: 15px !important;
            border-radius: 10px !important;
            color: black !important;
        }
        div[data-testid="stMetricLabel"] > label, [data-testid="stMetricLabel"] {
            color: #31333F !important;
        }
        div[data-testid="stMetricValue"] > div, [data-testid="stMetricValue"] {
            color: #31333F !important;
        }
        </style>
    """, unsafe_allow_html=True)

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

    # --- TAB 2: 멤버 관리 (수정 및 삭제) ---
    with tab2:
        st.header("👥 길드원 명부 관리")
        
        # 1. 신규 등록 (접기/펼치기)
        with st.expander("➕ 신규 멤버 등록하기 (클릭)", expanded=False):
            with st.form("add_member_form"):
                c1, c2, c3 = st.columns(3)
                new_name = c1.text_input("닉네임")
                new_cp = c2.number_input("전투력 (단위: 억)", min_value=0.0, step=0.1, format="%.1f") 
                role_options = ["(선택 안 함)", "길드장", "부길드장", "정예"]
                new_role = c3.selectbox("직책", role_options)
                
                if st.form_submit_button("신규 등록"):
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

        st.divider()

        # 2. 조회 및 빠른 수정 (핵심 기능!)
        st.subheader("📋 멤버 목록 (엑셀처럼 수정 가능)")
        
        if not df.empty:
            st.info("💡 닉네임, 전투력, 직책을 더블클릭해서 수정한 뒤, 아래 [저장] 버튼을 꼭 눌러주세요!")
            
            # 데이터 에디터 (수정 모드)
            edited_df = st.data_editor(
                df[['name', 'cp', 'role', 'id']],
                column_config={
                    "name": "닉네임",
                    "cp": st.column_config.NumberColumn("전투력 (억)", format="%.1f억", min_value=0.0),
                    "role": st.column_config.SelectboxColumn("직책", options=["길드장", "부길드장", "정예", "일반"], required=False),
                    "id": st.column_config.TextColumn("ID (시스템용)", disabled=True) # ID는 수정 불가
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed", # 행 추가/삭제는 위아래 별도 버튼으로 관리
                key="member_editor"
            )

            # [핵심] 수정사항 일괄 저장 버튼
            col_save, col_del = st.columns([1, 1])
            
            with col_save:
                if st.button("💾 수정사항 저장", type="primary", use_container_width=True):
                    with st.spinner("데이터베이스 업데이트 중..."):
                        # 변경된 데이터프레임을 한 줄씩 읽어서 DB 업데이트
                        for index, row in edited_df.iterrows():
                            # ID를 찾아가서 내용 덮어쓰기
                            db.collection('guilds').document(st.session_state['guild_id']).collection('members').document(row['id']).update({
                                'name': row['name'],
                                'cp': row['cp'],
                                'role': row['role'],
                                'updated_at': firestore.SERVER_TIMESTAMP
                            })
                        st.success("✅ 모든 수정사항이 저장되었습니다!")
                        time.sleep(1)
                        st.rerun()

            # 3. 삭제 기능
            with col_del:
                with st.popover("🗑️ 멤버 삭제하기", use_container_width=True):
                    st.write("삭제할 멤버를 선택하세요 (복구 불가)")
                    del_target = st.selectbox("삭제 대상", df['name'].tolist(), key="del_select")
                    
                    if st.button("🚨 영구 삭제", type="primary"):
                        mem_id = df[df['name'] == del_target]['id'].values[0]
                        delete_member(st.session_state['guild_id'], mem_id)
                        st.warning(f"{del_target} 님이 삭제되었습니다.")
                        time.sleep(1)
                        st.rerun()
        else:
            st.info("등록된 길드원이 없습니다. 위에서 등록해주세요.")
 # --- TAB 3: 일일 숙제 & 분석 (자동 입력 기능 강화) ---
    with tab3:
        st.header("📝 일일 활동 기록")
        
        col_date, col_upload = st.columns([1, 2])
        selected_date = col_date.date_input("날짜 선택", datetime.now())
        date_str = selected_date.strftime("%Y-%m-%d")
        
        # 스캔 데이터 세션 초기화
        if 'scan_data' not in st.session_state: st.session_state['scan_data'] = {}
        if 'scan_mode' not in st.session_state: st.session_state['scan_mode'] = None
        
        with col_upload:
            uploaded_file = st.file_uploader("📸 스크린샷 (기부로그 / 현자도전)", type=['png', 'jpg', 'jpeg'])
            
            if uploaded_file:
                if st.button("🔍 스크린샷 스마트 분석", type="primary"):
                    with st.spinner("이미지를 분석 중입니다..."):
                        mode, result_data, msg = run_ocr_scan(uploaded_file)
                        st.session_state['scan_mode'] = mode
                        st.session_state['scan_data'] = result_data
                        
                        if mode == "donation":
                            st.success(f"📜 기부 명단 인식 성공! ({len(result_data)}명 감지)")
                        elif mode == "sage":
                            st.success(f"🔥 현자 도전 인식 성공! (피해량: {result_data['dmg']}억)")
                        else:
                            st.error(msg)
                        uploaded_file.seek(0)

        st.divider()

        # 1. 데이터 입력 표 (Data Editor)
        members_df = get_guild_members(st.session_state['guild_id'])
        
        if members_df.empty:
            st.warning("먼저 [멤버 관리] 탭에서 길드원을 등록해주세요.")
        else:
            daily_record = get_daily_data(st.session_state['guild_id'], date_str)
            
            # [핵심] 스캔된 데이터를 표에 자동 반영하기 위한 로직
            scanned = st.session_state['scan_data']
            mode = st.session_state['scan_mode']
            
            display_data = []
            for index, row in members_df.iterrows():
                mem_id = row['id']
                mem_name = row['name']
                
                # DB에 저장된 기존 값 가져오기
                d_basic = record.get("don_basic", 0)
                d_inter = record.get("don_inter", 0)
                d_adv = record.get("don_adv", 0)
                d_item = record.get("don_item", 0)
                s_dmg = record.get("sage_dmg", 0.0)
                s_kill = record.get("sage_kill", 0)
                
                # 🔄 [자동 입력] 스캔 데이터가 있고, 닉네임이 일치하면 덮어쓰기!
                if mode == "donation" and mem_name in scanned:
                    user_scan = scanned[mem_name]
                    # 기존 값에 더할지, 덮어쓸지 결정 (여기선 덮어쓰기 적용)
                    if user_scan['basic'] > 0: d_basic = user_scan['basic']
                    if user_scan['inter'] > 0: d_inter = user_scan['inter']
                    if user_scan['adv'] > 0: d_adv = user_scan['adv']
                    if user_scan['item'] > 0: d_item = user_scan['item']
                
                # 현자 도전은 '현재 접속자' 또는 '단일 대상'이라고 가정할 경우 (선택사항)
                # 여기서는 자동 매핑이 어려우므로 상단 메시지로 보여주고 수동 입력을 유도하거나
                # 만약 이미지에 닉네임까지 있다면 매핑 가능 (현재 로직은 값만 가져옴)
                
                display_data.append({
                    "id": mem_id,
                    "name": mem_name,
                    "don_basic": d_basic,
                    "don_inter": d_inter,
                    "don_adv": d_adv,
                    "don_item": d_item,
                    "sage_dmg": s_dmg,
                    "sage_kill": s_kill
                })
            
            # 현자 도전 스캔 결과는 닉네임 매칭이 어려우니 힌트로 띄워줌
            if mode == "sage":
                st.info(f"💡 현자 스캔 결과: 피해량 **{scanned['dmg']}억** / 격퇴 **{scanned['kill']}회** (해당하는 멤버에게 입력해주세요)")
            elif mode == "donation":
                st.info("💡 기부 내역이 닉네임에 맞춰 자동으로 입력되었습니다. (맞는지 확인 후 저장하세요)")

            record_df = pd.DataFrame(display_data)
            
            # 표 출력
            edited_record = st.data_editor(
                record_df,
                column_config={
                    "id": None,
                    "name": st.column_config.TextColumn("닉네임", disabled=True),
                    "don_basic": st.column_config.NumberColumn("기부(초급)", min_value=0, max_value=10, step=1), # 스캔 누적을 위해 max 상향
                    "don_inter": st.column_config.NumberColumn("기부(중급)", min_value=0, max_value=5, step=1),
                    "don_adv": st.column_config.NumberColumn("기부(고급)", min_value=0, max_value=5, step=1),
                    "don_item": st.column_config.NumberColumn("기부(템)", min_value=0, max_value=10, step=1),
                    "sage_dmg": st.column_config.NumberColumn("🔥 피해량(억)", format="%.1f"),
                    "sage_kill": st.column_config.NumberColumn("☠️ 격퇴", step=1),
                },
                hide_index=True,
                use_container_width=True,
                height=500
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
        # (아래 그래프 코드는 그대로 유지)
        
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