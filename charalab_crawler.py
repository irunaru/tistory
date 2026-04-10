import os
import json
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

class CharaLabCrawlerV6_1:
    def __init__(self):
        # 환경 변수 명시적 로드 및 기본값 설정
        # 환경변수 주입 오류를 막기 위해 철저하게 gemini-pro를 기본값으로 사용
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-pro") 
        self.force_update = os.getenv("FORCE_UPDATE", "false").lower() == "true"
        self.target_url = os.getenv("TARGET_URL", "")
        
        logger.info(f"=== [CharaLab V6.1] 설정 로드 완료 ===")
        logger.info(f"실제 사용 모델: {self.model_name}")
        logger.info(f"강제 업데이트 여부: {self.force_update}")
        logger.info(f"타겟 URL: {self.target_url or '사용 안 함(RSS 모드)'}")
        logger.info(f"======================================")
        
        # 파일 경로
        self.history_file = "posted_articles_charalab.json"
        
        # 핵심 매니저 초기화
        self.supabase = create_client(CharaLabConfig.SUPABASE_URL, CharaLabConfig.SUPABASE_KEY)
        genai.configure(api_key=CharaLabConfig.GEMINI_API_KEY)
        self.model = genai.GenerativeModel(self.model_name)
        
        # 처리 이력 로드
        self.posted_articles = self._load_history()

    def _load_history(self) -> dict:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"이력 로드 실패: {e}")
                return {}
        return {}

    def _save_history(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.posted_articles, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"이력 저장 실패: {e}")

    async def get_feeds(self) -> List[Dict]:
        if self.target_url:
            logger.info(f"🎯 수동 입력 URL 처리 중: {self.target_url}")
            return [{'title': 'Manual Process', 'link': self.target_url, 'content_raw': 'Manual content fetch required'}]
            
        logger.info(f"RSS 피드 분석 중... ({CharaLabConfig.FEED_URL})")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)
        articles = []
        for entry in feed.entries[:CharaLabConfig.MAX_ARTICLES]:
            content = ""
            if 'content' in entry:
                content = entry.content[0].value
            elif 'summary' in entry:
                content = entry.summary
                
            articles.append({
                'title': entry.title,
                'link': entry.link,
                'content_raw': content,
                'tags': [t.term for t in entry.get('tags', [])] if 'tags' in entry else []
            })
        return articles

    async def process_with_gemini(self, data: Dict) -> Dict:
        prompt = f"""
        당신은 캐릭터 굿즈 뉴스 전문 블로거입니다. 
        다음 일본어 기사를 번역하여 한국 마니아들을 위한 풍성한 블로그 포스팅으로 만들어 주세요.
        
        [조건]
        1. 제목은 매력적인 한국어 문장으로 작성
        2. 본문은 HTML 형식(h2, p, ul, li)으로 작성
        3. 예약 기간, 가격, 판매처 정보를 정확히 포함
        
        제목 원문: {data['title']}
        본문 원문: {data['content_raw'][:4000]}
        
        출력 형식:
        제목: [여기에 작성]
        본문: [여기에 작성]
        """
        
        try:
            logger.info(f"Gemini 모델 연결 중: {self.model_name}")
            response = self.model.generate_content(prompt)
            text = response.text
            
            # 파싱
            if "제목:" in text and "본문:" in text:
                title_part = text.split("제목:")[1].split("본문:")[0].strip()
                content_part = text.split("본문:")[1].strip()
            else:
                title_part = data['title']
                content_part = text

            return {'title': title_part, 'content': content_part}
        except Exception as e:
            logger.error(f"Gemini 변환 중 오류 발생: {e}")
            return None

    async def run(self):
        articles = await self.get_feeds()
        logger.info(f"뉴스 {len(articles)}개 대기 중")
        
        for article in articles:
            url = article['link']
            
            if not self.force_update:
                if url in self.posted_articles:
                    logger.info(f"스킵 (JSON 이력 있음): {article['title']}")
                    continue
                
                # DB 이중 체크
                existing = self.supabase.table('characters').select('id').filter('metadata->>original_url', 'eq', url).execute()
                if existing.data:
                    logger.info(f"스킵 (DB 중복됨): {article['title']}")
                    self.posted_articles[url] = datetime.now().isoformat()
                    self._save_history()
                    continue

            logger.info(f"🚀 처리 시작: {article['title']}")
            post_data = await self.process_with_gemini(article)
            
            if post_data:
                try:
                    self.supabase.table('characters').insert({
                        'title': post_data['title'],
                        'content_html': post_data['content'],
                        'status': 'ready',
                        'category': CharaLabConfig.CATEGORY_NAME,
                        'tags': article['tags'] + CharaLabConfig.DEFAULT_TAGS,
                        'metadata': {'original_url': url}
                    }).execute()
                    
                    self.posted_articles[url] = datetime.now().isoformat()
                    self._save_history()
                    logger.info(f"✅ Supabase 저장 성공!")
                except Exception as e:
                    logger.error(f"DB 저장 오류: {e}")
            
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(CharaLabCrawlerV6_1().run())
