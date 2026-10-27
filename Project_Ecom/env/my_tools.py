from dotenv import load_dotenv
from langchain_tavily import TavilySearch
from langchain.tools import tool
import re
from env.db_client import get_duckdb_connection

# TavilySearch는 AI Agent가 인터넷에서 실시간으로 정보를 검색할 수 있도록 해주는 웹 검색 도구
def get_tavily():
    load_dotenv()
    return TavilySearch(max_results=5)

def make_system_propmt(suffix:str) -> str:
    return f"""
        당신은 다른 어시스턴트들과 협력하는 AI Agent 입니다.

        Task:
        1. 제공된 도구들을 사용하여 질문에 답하시오.
        2. 다른 어시스턴츠들을 최대한 활용하여 해당 전문 어시스턴트에게 명확히 위임하시오.
        3. 작업을 부분적으로 완료했다면, 완료된 부분을 설명하고 남은 작업을 다른 어시스턴트에게 전달하시오.
        4. 작업이 완료되었다면, '최종 답변'으로 시작하는 답변을 만드시오.

        단, '최종 답변으로 시작하여 대화를 완료할 경우:
        - 단순한 인사말이나 일반적인 대화만 있는 경우
        - 정보 제공만으로 모든 요청이 완료되고, 추가 작업이 필요하지 않은 경우

        {suffix}
    """