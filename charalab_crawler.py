"""
charalab_crawler.py
GitHub Actions 전용: CharaLab RSS 크롤링 → Gemini 번역 → Supabase 저장
Playwright 없음. 카카오 로그인 없음. 100% 안정.
"""

import os
import json
import asyncio
import feedparser
import logging
import requests
import re
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai
from charalab_config import CharaLabConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

# 저작권/출처 문구 제거
COPYRIGHT_PATTERNS = [
    r'<p[^>]*>©.*?</p>',
    r'<p[^>]*>&copy;.*?</p>',
    r'<p[^>]*>※본 페이지.*?</p>',
    r'<p[^>]*>※.*?</p>',
    r'©[^\n<]*',
    r'&copy;[^\n<]*',
    r'ライター：[^\n<]*',
    r'掲載日：[^\n<]*',
]

def remove_copyright(html: str) -> str:
    for pattern in COPYRIGHT_PATTERNS:
        html = re.sub(pattern, '', html, flags=re.DOTALL)
    return html.strip()


class CharaLabCrawler:
    def __init__(self):
        self.supabase = create_client(CharaLabConfig.SUPABASE_URL, CharaLabConfig.SUPABASE_KEY)
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"))
        self.history_file = "posted_articles_charalab.json"
        if os.path.exists(self.history_file):
            with open(self.history_file, 'r', encoding='utf-8') as f:
                self.posted_articles = json.load(f)
        else:
            self.posted_articles = {}

    # -------------------------------------------------------------------------
    # 기사 본문 크롤링
    # -------------------------------------------------------------------------
    def fetch_article(self, url: str) -> Optional[Dict]:
        try:
            headers = {'User-Agent': CharaLabConfig.USER_AGENT}
            r = requests.get(url, headers=headers, timeout=10)
            r.encoding = 'utf-8'
            soup = BeautifulSoup(r.text, 'html.parser')

            og_img = soup.select_one('meta[property="og:image"]')
            img_url = og_img.get('content', '') if og_img else ''

            content = soup.select_one('article') or soup.select_one('.entry-content')
            if not content:
                return None

            if not img_url:
                first_img = content.select_one('img')
                if first_img:
                    img_url = first_img.get('src', '')

            return {
                'text': content.get_text()[:3000],
                'img_url': img_url,
            }
        except Exception as e:
            logger.error(f"기사 크롤링 실패: {e}")
            return None

    # -------------------------------------------------------------------------
    # Gemini 번역
    # -------------------------------------------------------------------------
    def translate_article(self, title: str, text: str) -> Optional[Dict]:
        prompt = (
            "다음 일본어 기사를 한국어 블로그 포스팅으로 변환하세요.\n"
            "아래 규칙을 반드시 지켜서 작성하세요:\n"
            "1. 명사형으로 자연스럽게 작성할 것 (전반적인 톤은 친근한 존댓말로).\n"
            "2. 상투적이거나 뻔한 반복 문구 사용 금지.\n"
            "3. 기사마다 매번 다른 신선한 표현을 사용할 것.\n"
            "4. 글 마지막에 기사 내용에 대한 짧은 평을 추가할 것 (딱 1문장, 존댓말, 매번 다르게).\n"
            "5. 저자 이름, 저작권 표시(©, (C), ※ 등), 출처 표기는 모두 제거할 것.\n"
            "6. '※본 페이지에 게재된 내용은' 같은 면책 문구는 절대 포함하지 말 것.\n"
            "7. 10대 초반도 쉽게 이해하고 공감할 수 있는 쉬운 어휘로 작성할 것.\n"
            "8. img 태그는 절대 포함하지 말 것 (이미지는 별도로 처리함).\n\n"
            "반드시 아래 형식으로만 답하세요 (다른 설명 없이):\n"
            "[TITLE]한국어 제목 (한 줄, 태그 없이 텍스트만)\n"
            "[CONTENT]<p>본문 HTML 내용</p>\n\n"
            f"원문 제목: {title}\n"
            f"본문: {text}"
        )
        try:
            logger.info(f"Gemini 번역 중: {title[:40]}...")
            response = self.model.generate_content(prompt)
            raw = response.text

            t_match = re.search(r'\[TITLE\]\s*(.*?)\n', raw + '\n', re.IGNORECASE)
            c_match = re.search(r'\[CONTENT\]\s*(.*)', raw, re.DOTALL | re.IGNORECASE)

            t = t_match.group(1).strip() if t_match else title
            c = c_match.group(1).strip() if c_match else raw
            c = re.sub(r'```html|```', '', c).strip()
            c = re.sub(r'<img[^>]*/?>', '', c)
            c = remove_copyright(c)

            return {'title': t, 'content': c}
        except Exception as e:
            logger.error(f"❌ 번역 에러: {e}")
            return None

    # -------------------------------------------------------------------------
    # Supabase 저장
    # -------------------------------------------------------------------------
    def save_to_supabase(self, article_data: Dict) -> bool:
        try:
            # 중복 확인
            res = self.supabase.table('charalab_articles') \
                .select('id') \
                .eq('original_url', article_data['link']) \
                .execute()
            if res.data:
                logger.info(f"이미 저장됨 (스킵): {article_data['link']}")
                return False

            self.supabase.table('charalab_articles').insert({
                'title':        article_data['title_kr'],
                'content_html': article_data['content_kr'],
                'original_url': article_data['link'],
                'img_url':      article_data['img_url'],
                'status':       'draft',
                'source':       'charalab',
                'created_at':   datetime.utcnow().isoformat(),
            }).execute()
            logger.info(f"✅ Supabase 저장: {article_data['title_kr'][:40]}")
            return True
        except Exception as e:
            logger.error(f"❌ Supabase 저장 실패: {e}")
            return False

    # -------------------------------------------------------------------------
    # 메인 실행
    # -------------------------------------------------------------------------
    def run(self):
        logger.info("CharaLab 크롤러 시작")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)

        allowed_tags = {"グッズ", "ニュース"}
        articles = []
        for e in feed.entries:
            if e.link in self.posted_articles:
                continue
            tags = {t.term for t in e.tags} if hasattr(e, 'tags') else set()
            if tags & allowed_tags:
                articles.append(e)

        articles = articles[:5]

        if not articles:
            logger.info("새로운 기사 없음")
            return

        saved = 0
        for entry in articles:
            logger.info(f"▶ {entry.title[:50]}")

            data = self.fetch_article(entry.link)
            if not data:
                continue

            translated = self.translate_article(entry.title, data['text'])
            if not translated:
                continue

            article_data = {
                'title_kr':   translated['title'],
                'content_kr': translated['content'],
                'link':       entry.link,
                'img_url':    data['img_url'],
            }

            if self.save_to_supabase(article_data):
                self.posted_articles[entry.link] = datetime.now().isoformat()
                with open(self.history_file, 'w', encoding='utf-8') as f:
                    json.dump(self.posted_articles, f, ensure_ascii=False, indent=2)
                saved += 1

        logger.info(f"완료: {saved}개 저장")


if __name__ == "__main__":
    CharaLabCrawler().run()
