import os
import json
import asyncio
import feedparser
import logging
import requests
import re
import random
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai
from playwright.async_api import async_playwright, Page
from charalab_config import CharaLabConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

class CharaLabSystemFinal:
    def __init__(self):
        self.supabase = create_client(CharaLabConfig.SUPABASE_URL, CharaLabConfig.SUPABASE_KEY)
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"))
        self.blog_name = os.getenv("TISTORY_BLOG_NAME", "irunaru")
        self.history_file = "posted_articles_charalab.json"
        if os.path.exists(self.history_file):
            with open(self.history_file, 'r', encoding='utf-8') as f:
                self.posted_articles = json.load(f)
        else:
            self.posted_articles = {}

    async def fetch_article(self, url: str) -> Dict:
        try:
            r = requests.get(url, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            content = soup.select_one('article') or soup.select_one('.entry-content')
            if not content: return None
            img = soup.select_one('meta[property="og:image"]') or content.select_one('img')
            img_url = img.get('content') or img.get('src') if img else ""
            return {'text': content.get_text()[:3000], 'img_url': img_url}
        except: return None

    async def translate_article(self, title, text, img_url):
        prompt = (
            "다음 일본어 기사를 한국어 블로그 포스팅으로 변환하세요.\n"
            "아래 6가지 규칙을 반드시 지켜서 작성하세요:\n"
            "1. 명사형으로 자연스럽게 작성할 것 (전반적인 톤은 친근한 존댓말로).\n"
            "2. 상투적이거나 뻔한 반복 문구 사용 금지.\n"
            "3. 기사마다 매번 다른 신선한 표현을 사용할 것.\n"
            "4. 글 마지막에 기사 내용에 대한 짧은 평을 추가할 것(딱 1문장, 존댓말, 매번 다르게).\n"
            "5. 원문에 있는 저자 이름이나 저작권 표시(© 등)는 모두 제거할 것.\n"
            "6. 10대 초반도 쉽게 이해하고 공감할 수 있는 쉬운 어휘로 작성할 것.\n\n"
            "반드시 아래 형식으로만 답하세요:\n"
            "[TITLE]한국어 제목 (한 줄, 태그 없이 텍스트만)\n"
            "[CONTENT]<p>본문 HTML 내용</p>\n\n"
            f"원문 제목: {title}\n"
            f"이미지: {img_url}\n"
            f"본문: {text}"
        )
        try:
            logger.info("Gemini 번역 중...")
            response = self.model.generate_content(prompt)
            raw = response.text

            # [TITLE] 뒤에 올 수 있는 공백이나 줄바꿈 처리
            t_match = re.search(r'\[TITLE\]\s*(.*?)\n', raw + '\n', re.IGNORECASE)
            c_match = re.search(r'\[CONTENT\]\s*(.*)', raw, re.DOTALL | re.IGNORECASE)

            t = t_match.group(1).strip() if t_match else title
            c = c_match.group(1).strip() if c_match else raw
            c = re.sub(r'```html|```', '', c).strip()

            # 이미지 강제 삽입
            if img_url and '<img' not in c:
                c = f'<p><img src="{img_url}" style="max-width:100%;"></p>\n' + c

            return {'title': t, 'content': c}
        except Exception as e:
            logger.error(f"❌ 번역 에러: {e}")
            return None

    async def write_to_tistory(self, page: Page, title: str, content_html: str) -> bool:
        """playwright_tistory_core.py의 TistoryWriter.write_post 로직 그대로 사용"""
        try:
            write_url = f"https://{self.blog_name}.tistory.com/manage/posts/write"
            await page.goto(write_url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(5)
            await page.keyboard.press("Escape")  # 팝업 닫기
            await asyncio.sleep(1)

            # 1. 제목 입력 — 핵심 선택자: #post-title-inp
            title_field = page.locator('#post-title-inp').first
            if await title_field.count() > 0:
                await title_field.click()
                await title_field.fill(title)
                logger.info("✓ 제목 입력 완료 (#post-title-inp)")
            else:
                logger.error("❌ #post-title-inp 없음. 실패")
                return False

            # 2. 본문 입력 — iframe 혹은 일반 frame
            # HTML 코드가 텍스트로 보이지 않고 실제로 렌더링 되도록 JS 주입 사용
            editor_found = False
            frame = page.frame(name="editor-tistory_ifr")
            if frame:
                editable = frame.locator('[contenteditable="true"]').first
                if await editable.count() > 0:
                    await editable.click()
                    await editable.evaluate('(node, html) => { node.innerHTML = html; node.dispatchEvent(new Event("input", {bubbles: true})); }', content_html)
                    editor_found = True
                    logger.info("✓ 본문 입력 완료 (editor-tistory_ifr)")

            if not editor_found:
                for f in page.frames:
                    if 'editor' in f.name.lower():
                        editable = f.locator('[contenteditable="true"]').first
                        if await editable.count() > 0:
                            await editable.click()
                            await editable.evaluate('(node, html) => { node.innerHTML = html; node.dispatchEvent(new Event("input", {bubbles: true})); }', content_html)
                            editor_found = True
                            logger.info(f"✓ 본문 입력 완료 ({f.name})")
                            break

            if not editor_found:
                logger.error("❌ 에디터를 찾을 수 없음")
                return False

            await asyncio.sleep(2)

            # 3. 발행 — #publish-layer-btn 클릭 후 비공개 설정 후 최종 발행
            if await page.locator('#publish-layer-btn').count() > 0:
                await page.click('#publish-layer-btn')
                logger.info("✓ #publish-layer-btn 클릭")
                await asyncio.sleep(2)

                try:
                    # 비공개 라벨이 DOM에 있지만 hidden 상태일 수 있어 dispatch_event로 강제 클릭
                    priv_label = page.locator('label:has-text("비공개")').first
                    if await priv_label.count() > 0:
                        await priv_label.dispatch_event("click")
                        logger.info("✓ 비공개 설정")
                except:
                    pass

                # 최종 발행 버튼
                for s in ['button:has-text("발행")', 'button.btn_publish', '#publish-btn']:
                    if await page.locator(s).count() > 0:
                        await page.click(s)
                        logger.info(f"✓ 최종 발행 클릭: {s}")
                        break
            else:
                logger.error("❌ #publish-layer-btn 없음")
                return False

            await page.wait_for_load_state('networkidle', timeout=15000)
            await asyncio.sleep(3)
            logger.info(f"✨ 완료. URL: {page.url}")
            return True

        except Exception as e:
            logger.error(f"❌ write_to_tistory 실패: {e}")
            return False

    async def run(self):
        logger.info("CharaLab Final (core 로직 직접 이식) 가동")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)
        articles = [e for e in feed.entries if e.link not in self.posted_articles][:5]

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )
            context = await browser.new_context(storage_state="auth.json")

            for entry in articles:
                logger.info(f"▶ {entry.title}")
                data = await self.fetch_article(entry.link)
                if not data: continue

                translated = await self.translate_article(entry.title, data['text'], data['img_url'])
                if not translated: continue

                page = await context.new_page()
                success = await self.write_to_tistory(page, translated['title'], translated['content'])
                await page.close()

                if success:
                    try:
                        self.supabase.table('charalab_articles').insert({
                            'title': translated['title'],
                            'content_html': translated['content'],
                            'original_url': entry.link,
                            'status': 'draft'
                        }).execute()
                    except: pass
                    self.posted_articles[entry.link] = datetime.now().isoformat()
                    with open(self.history_file, 'w', encoding='utf-8') as f:
                        json.dump(self.posted_articles, f, ensure_ascii=False, indent=2)

                await asyncio.sleep(5)
            await browser.close()

if __name__ == "__main__":
    asyncio.run(CharaLabSystemFinal().run())
