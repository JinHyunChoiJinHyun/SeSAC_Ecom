from dotenv import load_dotenv
import os
from openai import OpenAI
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

# OpenAI 기본 클라이언트 생성 함수
def get_gpt_client():
    try:
        load_dotenv()
        client = OpenAI(api_key = os.getenv("OPENAI_API_KEY"))
    except:
        client = None
    return client

# LangChain 채팅 모델 클라이언트 생성 함수
def get_openai_chat_client(model='gpt-4o-mini'):
    try:
        load_dotenv()
        client = ChatOpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                            model=model)
    except:
        client = None
    return client

# OpenAI 임베딩 모델 클라이언트 생성 함수
def get_openai_embedding_client():
    try:
        load_dotenv()
        client = OpenAIEmbeddings(api_key = os.getenv("OPENAI_API_KEY"),
                                  model = 'text-embedding-3-small')
    except:
        client = None
    return client