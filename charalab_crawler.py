import os
import asyncio
import feedparser
import logging
from datetime import datetime
from typing import List, Dict
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai
from charalab_config import CharaLabConfig

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 환경 변수 로드
load_dotenv()

class CharaLabCrawlerV2:
    def __init__(self):
        self.supabase = create_client(CharaLabConfig.SUPABASE_URL, CharaLabConfig.SUPABASE_KEY)
        genai.configure(api_key=CharaLabConfig.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def get_latest_feeds(self) -> List[Dict]:
        """RSS 피드에서 기사 정보 추출 (Playwright 불필요)"""
        logger.info(f"RSS 피드 분석 시작 (V2): {CharaLabConfig.FEED_URL}")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)
        articles = []
        
        for entry in feed.entries[:CharaLabConfig.MAX_ARTICLES]:
            # RSS에서 본문(content) 또는 요약(summary) 가져오기
            content = ""
            if 'content' in entry:
                content = entry.content[0].value
            elif 'summary' in entry:
                content = entry.summary
            
            articles.append({
                'title': entry.title,
                'link': entry.link,
                'content_raw': content,
                'tags': [t.term for t in entry.get('tags', [])]
            })
        
        return articles

    async def process_with_gemini(self, data: Dict) -> Dict:
        """Gemini를 사용하여 한국어 번역 및 요약"""
        prompt = f"""
        당신은 인기 애니메이션 및 캐릭터 굿즈 전문 블로거입니다. 
        다음 일본어 캐릭터 뉴스 내용을 바탕으로 한국 독자들이 좋아할 만한 매력적인 블로그 포스팅을 작성해 주세요.

        [조건]
        1. 제목은 뉴스 내용을 한눈에 알 수 있게 매력적으로 작성 (한국어)
        2. 본문은 친절하고 생생한 말투 (~해요, ~입니다)
        3. 굿즈 종류, 예약 기간, 가격 등 핵심 정보를 정확히 포함
        4. HTML 형식으로 작성 (h2, p, ul, li 태그 사용)
        5. 원문 링크를 참고했다는 표시 포함

        [데이터]
        일본어 제목: {data['title']}
        본문 원문: {data['content_raw'][:4000]} 
        
        [출력 양식]
        제목: [여기에 제목 작성]
        본문: [여기에 HTML 본문 작성]
        """
        
        try:
            response = self.model.generate_content(prompt)
            text = response.text
            
            # 파싱 로직
            if "제목:" in text and "본문:" in text:
                title_part = text.split("제목:")[1].split("본문:")[0].strip()
                content_part = text.split("본문:")[1].strip()
            else:
                title_part = data['title']
                content_part = text

            return {
                'title': title_part,
                'content': content_part
            }
        except Exception as e:
            logger.error(f"Gemini 처리 실패: {e}")
            return None

    async def save_to_supabase(self, post_data: Dict, original_url: str, tags: list):
        """Supabase에 저장"""
        try:
            self.supabase.table('characters').insert({
                'title': post_data['title'],
                'content_html': post_data['content'],
                'status': 'ready',
                'category': CharaLabConfig.CATEGORY_NAME,
                'tags': tags + CharaLabConfig.DEFAULT_TAGS,
                'metadata': {'original_url': original_url}
            }).execute()
            logger.info(f"✅ Supabase 저장 완료: {post_data['title']}")
        except Exception as e:
            logger.error(f"Supabase 저장 실패: {e}")

    async def run(self):
        """메인 실행 루틴"""
        articles = await self.get_latest_feeds()
        logger.info(f"뉴스 {len(articles)}개 발견")
        
        for article in articles:
            # 중복 확인
            existing = self.supabase.table('characters').select('id').filter('metadata->>original_url', 'eq', article['link']).execute()
            if existing.data:
                logger.info(f"이미 처리된 기사: {article['title']}")
                continue
            
            # Gemini 변환
            logger.info(f"Gemini 변환 중: {article['title']}")
            post_data = await self.process_with_gemini(article)
            if not post_data: continue
            
            # Supabase 저장
            await self.save_to_supabase(post_data, article['link'], article['tags'])
            
            # API 제한 방지
            await asyncio.sleep(2)

if __name__ == "__main__":
    crawler = CharaLabCrawlerV2()
    asyncio.run(crawler.run())
