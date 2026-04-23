"""
charalab_crawler.py
GitHub Actions 전용: CharaLab RSS 크롤링 → Gemini 번역 → Supabase 저장
Playwright 없음. 카카오 로그인 없음. 100% 안정.
대상 사이트: irunaru.com (애드센스 승인 최적화)
"""

import os
import json
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

# -------------------------------------------------------------------------
# 저작권/출처 문구 제거
# -------------------------------------------------------------------------
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
    # Gemini 번역 (애드센스 승인 최적화)
    # -------------------------------------------------------------------------
    def translate_article(self, title: str, text: str) -> Optional[Dict]:
        prompt = (
            "다음 일본어 기사를 한국어 블로그 포스팅으로 변환하세요.\n"
            "이 글은 캐릭터 굿즈/위키 정보 블로그에 올라갑니다.\n"
            "현재 구글 애드센스 승인을 목표로 하고 있으므로 콘텐츠 품질이 최우선입니다.\n\n"
            "아래 규칙을 반드시 지켜서 작성하세요:\n"
            "1. 친근한 존댓말로 자연스럽게 작성할 것.\n"
            "2. 도입부 첫 2문장은 독자의 관심을 끄는 질문형 또는 공감형으로 작성할 것.\n"
            "3. h2 소제목을 2~3개 포함하여 글을 구조화할 것.\n"
            "4. 글자수 800자 이상으로 충분한 정보를 담을 것.\n"
            "5. 단순 번역이 아닌 한국 독자 관점으로 재창작할 것.\n"
            "6. 상투적이거나 뻔한 반복 문구 사용 금지.\n"
            "7. 저자 이름, 저작권 표시(©, (C), ※ 등), 출처 표기는 모두 제거할 것.\n"
            "8. '※본 페이지에 게재된 내용은' 같은 면책 문구는 절대 포함하지 말 것.\n"
            "9. 10대도 쉽게 이해할 수 있는 쉬운 어휘로 작성할 것.\n"
            "10. img 태그는 절대 포함하지 말 것.\n"
            "11. 글 마지막에 짧은 편집자 코멘트를 1문장 추가할 것 (매번 다르게).\n\n"
            "반드시 아래 형식으로만 답하세요 (다른 설명 없이):\n"
            "[TITLE]한국어 제목 (한 줄, 태그 없이 텍스트만)\n"
            "[CONTENT]<p>도입부</p><h2>소제목</h2><p>본문 HTML 내용</p>\n\n"
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

            # 2차 검수
            c, t = self.review_article(t, c)

            return {'title': t, 'content': c}
        except Exception as e:
            logger.error(f"❌ 번역 에러: {e}")
            return None

    # -------------------------------------------------------------------------
    # 2차 검수 (애드센스 승인 기준)
    # -------------------------------------------------------------------------
    def review_article(self, title: str, content: str):
        review_prompt = (
            "아래 한국어 블로그 글을 구글 애드센스 승인 관점에서 검토하고 부족한 부분만 보완하세요.\n"
            "체크 항목:\n"
            "- 도입부가 독자를 잡는 질문형/공감형인가\n"
            "- h2 소제목이 2개 이상인가\n"
            "- 글자수가 800자 이상인가\n"
            "- 단순 번역 티가 나지 않고 자연스러운 한국어인가\n"
            "- 저작권/면책 문구가 완전히 제거되었는가\n"
            "부족한 부분만 보완해서 완성본을 반환하세요. 잘 된 부분은 그대로 두세요.\n\n"
            "반드시 아래 형식으로만 답하세요:\n"
            "[TITLE]제목\n"
            "[CONTENT]본문 HTML\n\n"
            f"[TITLE]{title}\n"
            f"[CONTENT]{content}"
        )
        try:
            logger.info("2차 검수 중...")
            response = self.model.generate_content(review_prompt)
            raw = response.text

            t_match = re.search(r'\[TITLE\]\s*(.*?)\n', raw + '\n', re.IGNORECASE)
            c_match = re.search(r'\[CONTENT\]\s*(.*)', raw, re.DOTALL | re.IGNORECASE)

            t = t_match.group(1).strip() if t_match else title
            c = c_match.group(1).strip() if c_match else content
            c = re.sub(r'```html|```', '', c).strip()
            c = re.sub(r'<img[^>]*/?>', '', c)
            return c, t
        except Exception as e:
            logger.warning(f"⚠️ 2차 검수 실패 (원본 사용): {e}")
            return content, title

    # -------------------------------------------------------------------------
    # Supabase 저장
    # -------------------------------------------------------------------------
    def save_to_supabase(self, article_data: Dict) -> bool:
        try:
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
