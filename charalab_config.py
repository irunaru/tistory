import os
from dotenv import load_dotenv

load_dotenv()

class CharaLabConfig:
    # API 및 DB 설정
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    
    # 크롤링 설정
    FEED_URL = "https://charalab.com/feed/"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    
    # 블로그 포스팅 설정
    CATEGORY_NAME = "Character News"
    DEFAULT_TAGS = ["CharaLab", "캐릭터뉴스", "애니메이션"]
    
    # 크롤링 제한 (최근 10개 기사)
    MAX_ARTICLES = 10
