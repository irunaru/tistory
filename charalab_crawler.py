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

class CharaLabCrawlerV6:
    def __init__(self):
        # 환경 변수를 통한 설정 (수동 트리거 지원)
        self.target_url = os.getenv("TARGET_URL")
        self.force_update = os.getenv("FORCE_UPDATE", "false").lower() == "true"
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-pro")
        
        # 기본 경로 설정
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
                logger.error(f"이력 파일 로드 실패: {e}")
                return {}
        return {}

    def _save_history(self):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.posted_articles, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"이력 파일 저장 실패: {e}")

    async def get_feeds(self) -> List[Dict]:
        """기사 목록 확보 (수동 URL 지원)"""
        if self.target_url:
            logger.info(f"🎯 수동 타겟 URL 처리 모드: {self.target_url}")
            return [{'title': 'Manual Target', 'link': self.target_url, 'content_raw': 'Manual Processing Required'}]
            
        logger.info(f"RSS 피드 분석 시작 (V6): {CharaLabConfig.FEED_URL}")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)
        articles = []
        
        for entry in feed.entries[:CharaLabConfig.MAX_ARTICLES]:
            # RSS에서 본문이나 요약 가져오기
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
        """Gemini를 사용하여 한국어 번역 및 요약"""
        prompt = f"""
        당신은 인기 애니메이션 및 캐릭터 굿즈 전문 블로거입니다. 
        다음 일본어 캐릭터 뉴스 내용을 바탕으로 한국 독자들이 좋아할 만한 매력적인 블로그 포스팅을 작성해 주세요.

        [조건]
        1. 제목은 뉴스 내용을 한눈에 알 수 있게 매력적으로 작성 (한국어)
        2. 본문은 정보를 친절하게 설명하는 말투 (~해요, ~입니다)
        3. 굿즈 정보나 이벤트 날짜가 있다면 명확하게 포함
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

    async def run(self):
        """메인 실행 루틴"""
        articles = await self.get_feeds()
        logger.info(f"뉴스 {len(articles)}개 발견")
        
        for article in articles:
            url = article['link']
            
            # 중복 체크 (JSON 이력 또는 Supabase 조회)
            if not self.force_update:
                if url in self.posted_articles:
                    logger.info(f"이미 처리된 기사 (JSON 캐시): {article['title']}")
                    continue
                
                # Supabase 이중 체크
                existing = self.supabase.table('characters').select('id').filter('metadata->>original_url', 'eq', url).execute()
                if existing.data:
                    logger.info(f"이미 처리된 기사 (Supabase 중복): {article['title']}")
                    # 캐시 업데이트
                    self.posted_articles[url] = datetime.now().isoformat()
                    self._save_history()
                    continue

            logger.info(f"🚀 기사 분석 중 ({self.model_name}): {article['title']}")
            post_data = await self.process_with_gemini(article)
            
            if post_data:
                # Supabase 저장
                try:
                    self.supabase.table('characters').insert({
                        'title': post_data['title'],
                        'content_html': post_data['content'],
                        'status': 'ready',
                        'category': CharaLabConfig.CATEGORY_NAME,
                        'tags': article['tags'] + CharaLabConfig.DEFAULT_TAGS,
                        'metadata': {'original_url': url}
                    }).execute()
                    
                    # 이력 관리
                    self.posted_articles[url] = datetime.now().isoformat()
                    self._save_history()
                    logger.info(f"✅ 게시 준비 완료: {post_data['title']}")
                except Exception as e:
                    logger.error(f"저장 실패: {e}")
            
            # API 제한 및 사이트 부하 방지
            await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(CharaLabCrawlerV6().run())
