import os
import asyncio
import feedparser
import logging
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai
from playwright.async_api import async_playwright

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 환경 변수 로드
load_dotenv()

class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    CHARALAB_FEED_URL = "https://charalab.com/feed/"

class CharaLabCrawler:
    def __init__(self):
        self.supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        genai.configure(api_key=Config.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def get_latest_feeds(self) -> List[Dict]:
        """RSS 피드에서 최신 기사 목록 가져오기"""
        logger.info(f"RSS 피드 읽기 시작: {Config.CHARALAB_FEED_URL}")
        feed = feedparser.parse(Config.CHARALAB_FEED_URL)
        articles = []
        
        for entry in feed.entries[:10]:  # 최신 10개만 확인
            articles.append({
                'title': entry.title,
                'link': entry.link,
                'published': entry.get('published', '')
            })
        
        return articles

    async def extract_content(self, url: str) -> Dict:
        """Playwright를 사용하여 기사 본문 및 이미지 추출"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
            page = await context.new_page()
            
            try:
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # 제목 및 본문 추출
                title = await page.inner_text('h1.entry-title')
                content_html = await page.inner_html('div.entry-content')
                
                # 이미지 추출 (첫 번째 이미지)
                image_url = await page.get_attribute('div.entry-content img', 'src')
                
                # 태그 추출 (작품명 등)
                tags = await page.locator('div.entry-tag a').all_inner_texts()
                
                return {
                    'title': title,
                    'content_html': content_html,
                    'image_url': image_url,
                    'tags': tags
                }
            except Exception as e:
                logger.error(f"콘텐츠 추출 실패 ({url}): {e}")
                return None
            finally:
                await browser.close()

    async def process_with_gemini(self, data: Dict) -> Dict:
        """Gemini를 사용하여 한국어 번역 및 요약"""
        prompt = f"""
        당신은 인기 애니메이션 및 캐릭터 굿즈 전문 블로거입니다. 
        다음 일본어 캐릭터 뉴스 내용을 바탕으로 한국 독자들이 좋아할 만한 블로그 포스팅을 작성해 주세요.

        [조건]
        1. 제목은 뉴스 내용을 한눈에 알 수 있게 매력적으로 작성 (한국어)
        2. 본문은 정보를 친절하게 설명하는 말투 (~해요, ~입니다)
        3. 굿즈 정보나 이벤트 날짜가 있다면 명확하게 포함
        4. HTML 형식으로 작성 (h2, p, ul, li 태그 사용)
        5. 원문 링크를 참고했다는 표시 포함

        [데이터]
        일본어 제목: {data['title']}
        본문 요약: {data['content_html'][:2000]}
        
        [출력 양식]
        제목: [여기에 제목 작성]
        본문: [여기에 HTML 본문 작성]
        """
        
        try:
            response = self.model.generate_content(prompt)
            text = response.text
            
            # 간단한 파싱 (제목: / 본문: 구분)
            title = text.split("제목:")[1].split("본문:")[0].strip()
            content = text.split("본문:")[1].strip()
            
            return {
                'title': title,
                'content': content
            }
        except Exception as e:
            logger.error(f"Gemini 처리 실패: {e}")
            return None

    async def save_to_supabase(self, post_data: Dict, original_url: str, tags: list):
        """Supabase에 'ready' 상태로 저장"""
        try:
            self.supabase.table('characters').insert({
                'title': post_data['title'],
                'content_html': post_data['content'],
                'status': 'ready',
                'category': 'Character News',
                'tags': tags + ['CharaLab', '캐릭터뉴스'],
                'metadata': {'original_url': original_url}
            }).execute()
            logger.info(f"✅ Supabase 저장 완료: {post_data['title']}")
        except Exception as e:
            logger.error(f"Supabase 저장 실패: {e}")

    async def run(self):
        """메인 실행 루틴"""
        articles = await self.get_latest_feeds()
        
        for article in articles:
            # 이미 처리된 기사인지 확인 (URL 기준)
            existing = self.supabase.table('characters').select('id').filter('metadata->>original_url', 'eq', article['link']).execute()
            if existing.data:
                logger.info(f"이미 처리된 기사 건너뜀: {article['title']}")
                continue
            
            # 기사 내용 추출
            logger.info(f"기사 분석 중: {article['link']}")
            raw_data = await self.extract_content(article['link'])
            if not raw_data: continue
            
            # Gemini 변환
            post_data = await self.process_with_gemini(raw_data)
            if not post_data: continue
            
            # Supabase 저장
            await self.save_to_supabase(post_data, article['link'], raw_data['tags'])
            
            # API 제한 및 사이트 부하 방지를 위한 간격
            await asyncio.sleep(5)

if __name__ == "__main__":
    crawler = CharaLabCrawler()
    asyncio.run(crawler.run())
