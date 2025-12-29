import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import pandas as pd
import plotly.express as px
import json
import time

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
    st.sidebar.success(f"접속 중: {st.session_state['guild_name']}")
    if st.sidebar.button("로그아웃"):
        logout()
        
    st.title(f"🏰 {st.session_state['guild_name']} 길드 관리 시스템")
    
    # 데이터 로드
    df = get_guild_members(st.session_state['guild_id'])
    
    if df.empty:
        st.warning("아직 등록된 길드원이 없습니다. 멤버를 추가해주세요!")
        df = pd.DataFrame(columns=['name', 'cp', 'job', 'id']) # 빈 프레임 생성

    # 탭 구성
    tab1, tab2, tab3 = st.tabs(["📊 통계 대시보드", "👥 멤버 관리", "📷 OCR 투력 스캔"])

    # --- TAB 1: 통계 대시보드 ---
    with tab1:
        st.header("길드 전력 분석")
        
        if not df.empty:
            # KPI 지표
            col1, col2, col3 = st.columns(3)
            col1.metric("총 길드원", f"{len(df)}명")
            col1.caption("정예 멤버")
            
            total_cp = df['cp'].sum()
            col2.metric("총 전투력 (Total CP)", f"{total_cp:,.0f}")
            col2.caption("서버 랭킹 도전!")
            
            avg_cp = df['cp'].mean()
            col3.metric("평균 전투력", f"{avg_cp:,.0f}")
            
            st.divider()
            
            # 차트 영역
            c1, c2 = st.columns([2, 1])
            with c1:
                st.subheader("전투력 순위 Top 10")
                top_10 = df.sort_values(by='cp', ascending=False).head(10)
                fig_bar = px.bar(top_10, x='cp', y='name', orientation='h', 
                                 text_auto='.2s', title="상위 랭커", color='cp',
                                 color_continuous_scale='Oranges')
                fig_bar.update_layout(yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_bar, use_container_width=True)
            
            with c2:
                st.subheader("직업 분포")
                if 'job' in df.columns:
                    fig_pie = px.pie(df, names='job', title="클래스 비율", hole=0.4)
                    st.plotly_chart(fig_pie, use_container_width=True)

    # --- TAB 2: 멤버 관리 (CRUD) ---
    with tab2:
        st.header("길드원 명부 관리")
        
        # 1. 멤버 추가 폼
        with st.expander("➕ 신규 멤버 등록하기", expanded=True):
            with st.form("add_member_form"):
                col_a, col_b, col_c = st.columns(3)
                new_name = col_a.text_input("닉네임")
                new_cp = col_b.number_input("전투력", min_value=0, step=1000, format="%d")
                
                # 직업 대신 직책 선택 (선택 안 함 가능)
                role_options = ["(선택 안 함)", "길드장", "부길드장", "정예"]
                new_role = col_c.selectbox("직책 (선택)", role_options)
                
                submitted = st.form_submit_button("등록")
                if submitted:
                    if new_name:
                        # role 값이 "(선택 안 함)"이면 빈 값으로 처리하거나 "일반"으로 처리됨
                        success, msg = add_update_member(st.session_state['guild_id'], new_name, new_cp, new_role)
                        
                        if success:
                            st.success(f"{new_name} {msg}!")
                            time.sleep(1)
                            st.rerun()
                        else:
                            st.error(msg) # 정원 초과 메시지 출력
                    else:
                        st.error("닉네임을 입력하세요.")

        # 2. 데이터 에디터 (빠른 수정)
        st.subheader("멤버 목록 (수정 가능)")
        
        # 표시할 컬럼 정리 (job -> role)
        # 데이터가 없을 때를 대비해 컬럼 확인
        if 'role' not in df.columns:
            df['role'] = '일반' # 기존 데이터 호환성
            
        edited_df = st.data_editor(
            df[['name', 'cp', 'role', 'id']], 
            column_config={
                "name": "닉네임",
                "cp": st.column_config.NumberColumn("전투력", format="%d"),
                "role": st.column_config.SelectboxColumn(
                    "직책",
                    options=["길드장", "부길드장", "정예", "일반"],
                    required=False
                ),
                "id": st.column_config.TextColumn("ID (시스템용)", disabled=True)
            },
            num_rows="dynamic",
            key="member_editor"
        )
        
        st.divider()
        # (삭제 버튼 코드는 기존과 동일하게 유지)
        st.subheader("멤버 삭제")
        if not df.empty:
            target_member = st.selectbox("삭제할 멤버 선택", df['name'].tolist())
            if st.button("선택한 멤버 삭제"):
                member_id = df[df['name'] == target_member]['id'].values[0]
                delete_member(st.session_state['guild_id'], member_id)
                st.warning(f"{target_member} 님이 삭제되었습니다.")
                time.sleep(1)
                st.rerun()

    # --- TAB 3: OCR 투력 스캔 ---
    with tab3:
        st.header("📸 스크린샷 투력 인식")
        st.write("게임 내 '내 정보' 화면을 캡처하여 업로드하면 전투력을 자동으로 읽어옵니다.")
        
        uploaded_file = st.file_uploader("이미지 파일 업로드", type=['png', 'jpg', 'jpeg'])
        
        if uploaded_file is not None:
            st.image(uploaded_file, caption="업로드된 이미지", width=300)
            
            if st.button("투력 추출 시작"):
                with st.spinner("이미지 분석 중... (마법 시전 중 🧙‍♂️)"):
                    # 실제 OCR 연동 시 여기서 easyocr 함수 호출
                    recognized_cp, recognized_name = simulate_ocr_process(uploaded_file)
                
                st.success("분석 완료!")
                
                col_ocr1, col_ocr2 = st.columns(2)
                ocr_name = col_ocr1.text_input("인식된 닉네임", value=recognized_name)
                ocr_cp = col_ocr2.number_input("인식된 투력", value=recognized_cp)
                
                if st.button("이 정보로 업데이트/등록"):
                    # 이름으로 기존 멤버 찾기 (간소화된 로직)
                    existing_member = df[df['name'] == ocr_name]
                    
                    if not existing_member.empty:
                        doc_id = existing_member.iloc[0]['id']
                        # 직업 정보는 기존 유지
                        job = existing_member.iloc[0]['job']
                        add_update_member(st.session_state['guild_id'], ocr_name, ocr_cp, job, doc_id)
                        st.success(f"{ocr_name}님의 투력이 {ocr_cp}로 업데이트되었습니다!")
                    else:
                        st.info("신규 멤버입니다. 직업을 선택해주세요.")
                        job_sel = st.selectbox("직업 선택", ["전사", "마법사", "궁수", "성직자", "기타"], key="ocr_job")
                        if st.button("신규 등록 확정"):
                            add_update_member(st.session_state['guild_id'], ocr_name, ocr_cp, job_sel)
                            st.success("등록 완료!")
                            st.rerun()
                    
                    time.sleep(1.5)
                    st.rerun()

# --- 실행 흐름 제어 ---
if __name__ == "__main__":
    if st.session_state['is_logged_in']:
        main_app()
    else:
        login_ui()