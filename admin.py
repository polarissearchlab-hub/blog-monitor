import streamlit as st
import monitor
import sys
import io
import time

# 페이지 설정
st.set_page_config(page_title="블로그 모니터링 관리자", page_icon="🤖")

st.title("🤖 블로그 신고 모니터링 시스템")
st.markdown("---")

st.info("이 페이지에서는 버튼 하나로 **'비공개/삭제된 글'**을 찾아내고 **자동으로 종결 처리**할 수 있습니다.")

# 실행 버튼
if st.button("🚀 지금 검사 시작하기", type="primary", use_container_width=True):
    st.write("작업을 시작합니다...")
    
    # 로그를 보여줄 공간
    log_container = st.container()
    log_text = log_container.empty()
    logs = []

    def gui_logger(message):
        # 로그 추가
        logs.append(message)
        # 화면 업데이트 (최신 20줄만 보여주거나 전체 보여주기)
        log_area_content = "\n".join(logs)
        log_text.code(log_area_content, language="text")

    # 모니터링 실행
    try:
        with st.spinner("로봇이 열심히 일하는 중입니다... 뚝딱뚝딱"):
            # monitor.py의 함수 호출 (커스텀 로거 전달)
            monitor.run_all_tasks(log_func=gui_logger)
            
        st.success("✅ 작업이 완료되었습니다!")
        
    except Exception as e:
        st.error(f"오류가 발생했습니다: {e}")

st.markdown("---")
st.caption("Developed for simple & easy blog monitoring.")
