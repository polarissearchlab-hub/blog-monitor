import gspread
import requests
from bs4 import BeautifulSoup
from google.oauth2.service_account import Credentials
import time
import schedule
from datetime import datetime
import sys

# =====================================================================================
# 설정 (CONFIGURATION)
# =====================================================================================

import os
try:
    import streamlit as st
except ImportError:
    st = None


# Credential file finding logic
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)

possible_paths = [
    os.path.join(current_dir, 'credentials.json'),
    os.path.join(parent_dir, 'credentials.json'),
    'credentials.json'
]

SERVICE_ACCOUNT_FILE = None
SERVICE_ACCOUNT_INFO = None

# 1. Try to load from Streamlit Secrets (for Cloud Deployment)
if st is not None and hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
    # to_dict() is needed because st.secrets objects might be immutable or special types
    SERVICE_ACCOUNT_INFO = dict(st.secrets["gcp_service_account"])
    
    # [CRITICAL FIX] Handle private_key newline escaping issues
    if "private_key" in SERVICE_ACCOUNT_INFO:
        SERVICE_ACCOUNT_INFO["private_key"] = SERVICE_ACCOUNT_INFO["private_key"].replace("\\n", "\n") 

# 2. If not in secrets, try local file
if not SERVICE_ACCOUNT_INFO:
    for path in possible_paths:
        if os.path.exists(path):
            SERVICE_ACCOUNT_FILE = path
            break

if not SERVICE_ACCOUNT_INFO and not SERVICE_ACCOUNT_FILE:
    # 즉시 종료하지 않고, 나중에 함수에서 체크하도록 함
    pass  

def check_credentials_available():
    return (SERVICE_ACCOUNT_INFO is not None) or (SERVICE_ACCOUNT_FILE is not None)
SPREADSHEET_URL = "https://docs.google.com/spreadsheets/d/1DUEL5-7_MBaX95zBVhS4zmkcXqYG8UTV7BEN_g6UKdg/edit?usp=sharing"
SHEET_NAME = "국민신문고 신고내역"

# 4. 열 위치 수정 (사용자 시트 구조 반영)
# 구조: A=날짜, B=신고채널, C=제목, D=블로그카테고리(이름), E=URL, F=상태
COL_URL = 5       # E열
COL_STATUS = 6    # F열

STATUS_PENDING = "접수"
STATUS_CLOSED = "종결"

# 헤더(User-Agent) 설정
HEADERS = {
    'User-Agent': "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
}

def validate_service_account_info(info, log_func=print):
    """
    인증 정보가 올바른 형식인지 미리 검사합니다.
    """
    if not info or not isinstance(info, dict):
        log_func("❌ 인증 정보가 딕셔너리 형태가 아닙니다.")
        return False
        
    required_keys = ["project_id", "private_key", "client_email"]
    missing = [k for k in required_keys if k not in info]
    if missing:
        log_func(f"❌ 인증 정보에 필수 항목이 빠져 있습니다: {missing}")
        return False
        
    p_key = info.get("private_key", "")
    
    # [방어 로직] 키 내용을 좀 더 자세히 로그로 남겨서(일부만) 원인 파악
    log_func(f"🔎 키 검사 중... 길이: {len(p_key)}자")
    log_func(f"   앞부분: {repr(p_key[:50])}")
    log_func(f"   뒷부분: {repr(p_key[-50:])}")

    if "-----BEGIN PRIVATE KEY-----" not in p_key:
        log_func("❌ [중요] 'private_key' 형식이 잘못되었습니다.")
        log_func("   이유: '-----BEGIN PRIVATE KEY-----' 로 시작하지 않습니다.")
        log_func("   해결: credentials.json 안에 있는 private_key 전체를 정확히 복사했는지 확인해주세요.")
        return False
        
    if len(p_key) < 100:
        log_func("❌ [중요] 키 길이가 너무 짧습니다. 잘린 것 같습니다.")
        return False
        
    return True


# =====================================================================================
# 핵심 로직 (LOGIC)
# =====================================================================================

def get_sheet_service(log_func=print):
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    try:
        if SERVICE_ACCOUNT_INFO:
            # Load from dictionary (Secrets)
            if not validate_service_account_info(SERVICE_ACCOUNT_INFO, log_func):
                return None
            creds = Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=scopes)
        else:
            # Load from file (Local)
            creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
            
        client = gspread.authorize(creds)
        return client
    except Exception as e:
        log_func(f"인증 오류: {e}")
        return None



def check_blog_visibility(url):
    """비공개 또는 삭제 여부 확인"""
    if not url: return False
    
    # 종결로 판단할 키워드 목록
    closed_keywords = [
        "비공개 블로그입니다",
        "삭제된 게시글입니다",
        "존재하지 않는 게시글입니다",
        "비공개로 설정",
        "조치", # 기존 의료법 위반 등
        "접근이 제한",
        "게시물이 삭제되었거나" # 팝업 메시지 내용
    ]

    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        
        # 1. 텍스트에서 키워드 찾기
        for keyword in closed_keywords:
            if keyword in res.text:
                return True
        
        # 2. iframe 내부 체크 (네이버 블로그 구조상 필요)
        soup = BeautifulSoup(res.text, 'html.parser')
        iframe = soup.select_one('#mainFrame')
        if iframe:
            src = iframe.get('src')
            real_url = "https://blog.naver.com" + src
            res_sub = requests.get(real_url, headers=HEADERS, timeout=10)
            
            for keyword in closed_keywords:
                if keyword in res_sub.text:
                    return True
                    
        return False
    except:
        # 접속 에러나면 일단 패스 (나중에 다시 체크)
        return False




def task_check_status(sheet, log_func=print):
    """
    '접수' 상태인 항목을 검사하여 비공개면 '종결'로 바꿉니다.
    """
    log_func("\n[기능 2] 신고 상태 확인 시작...")
    rows = sheet.get_all_values()
    updates = []
    still_pending_urls = []
    
    count_chk = 0
    for i, row in enumerate(rows):
        if i < 3: continue
        row_num = i + 1
        
        url = row[COL_URL - 1] if len(row) >= COL_URL else ""
        status = row[COL_STATUS - 1] if len(row) >= COL_STATUS else ""
        
        if status == STATUS_PENDING and url:
            count_chk += 1
            is_hidden = check_blog_visibility(url)
            if is_hidden:
                # 전체 URL 표시 (잘림 없음)
                log_func(f"  - [비공개 처리됨] {url} -> 종결")
                updates.append((row_num, COL_STATUS, STATUS_CLOSED))
            else:
                # 살아있는(종결 안된) URL 수집
                still_pending_urls.append(url)
            time.sleep(0.5)
            
    if updates:
        log_func(f"  - 총 {len(updates)}건을 '종결' 처리합니다.")
        cells = [gspread.Cell(r, c, v) for r, c, v in updates]
        sheet.update_cells(cells)
        log_func("  - 저장 완료!")
    else:
        log_func(f"  - {count_chk}건 확인 완료. 변동 사항 없음.")

    # 마지막에 종결 안 된 목록 출력
    if still_pending_urls:
        log_func("\n" + "="*30)
        log_func("종결처리가 안된 URL 목록")
        log_func("="*30)
        for p_url in still_pending_urls:
            log_func(p_url)
        log_func("="*30 + "\n")

    return still_pending_urls

def run_all_tasks(log_func=print):
    log_func(f"\n>>> 작업 실행: {datetime.now()}")
    client = get_sheet_service(log_func) # Pass logger to service getter
    if not client: return
    
    try:
        doc = client.open_by_url(SPREADSHEET_URL)
        sheet = doc.worksheet(SHEET_NAME)
    except Exception as e:
        log_func(f"시트 접속 실패: {e}")
        return
    # 상태 확인하기
    task_check_status(sheet, log_func)
    log_func(">>> 작업 완료\n")

# =====================================================================================
# 메인 실행
# =====================================================================================

if __name__ == "__main__":
    print("="*50)
    print("네이버 블로그 모니터링 시스템 (업그레이드 버전)")
    print("="*50)
    
    menu = input("\n메뉴를 선택하세요:\n1. 지금 신고 상태 확인 (비공개/삭제 확인)\n2. 스케줄러 모드 (월/목 자동 실행 대기)\n입력: ")
    
    if menu == "1":
        run_all_tasks()
        
    elif menu == "2":
        print("\n[스케줄러 모드] 프로그램을 끄지 말고 켜두세요.")
        print("매주 월요일, 목요일 오전 10시에 자동으로 작동합니다.")
        
        # 테스트용: schedule.every(10).seconds.do(run_all_tasks)
        schedule.every().monday.at("10:00").do(run_all_tasks)
        schedule.every().thursday.at("10:00").do(run_all_tasks)
        
        # 최초 1회 실행 여부 묻기
        first_run = input("기다리기 전에 지금 한번 돌릴까요? (y/n): ")
        if first_run.lower() == 'y':
            run_all_tasks()
            
        while True:
            schedule.run_pending()
            time.sleep(60)
            
    else:
        print("잘못된 입력입니다.")
