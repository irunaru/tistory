import os
import json
import asyncio
import feedparser
import logging
import requests
import re
import base64
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai
from playwright.async_api import async_playwright, Page, BrowserContext
from charalab_config import CharaLabConfig

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

# 저작권/출처 문구 후처리 제거
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


class CharaLabSystemFinal:
    def __init__(self):
        self.supabase = create_client(CharaLabConfig.SUPABASE_URL, CharaLabConfig.SUPABASE_KEY)
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        self.model = genai.GenerativeModel(os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"))
        self.blog_name = os.getenv("TISTORY_BLOG_NAME", "irunaru")
        self.tistory_id = os.getenv("TISTORY_LOGIN_ID", "")
        self.tistory_pw = os.getenv("TISTORY_PASSWORD", "")
        self.history_file = "posted_articles_charalab.json"
        if os.path.exists(self.history_file):
            with open(self.history_file, 'r', encoding='utf-8') as f:
                self.posted_articles = json.load(f)
        else:
            self.posted_articles = {}

    # -------------------------------------------------------------------------
    # 로그인 확인 + 재로그인 (playwright_tistory_core.py 방식 그대로)
    # -------------------------------------------------------------------------
    async def ensure_logged_in(self, context: BrowserContext) -> bool:
        """로그인 상태 확인 후 필요시 재로그인"""
        page = await context.new_page()
        try:
            write_url = f"https://{self.blog_name}.tistory.com/manage/post"
            await page.goto(write_url, wait_until='networkidle', timeout=15000)
            await asyncio.sleep(2)

            if 'login' in page.url or 'auth' in page.url:
                logger.info("세션 만료 → 재로그인 시도")
                await page.close()
                return await self._login(context)
            else:
                logger.info("✓ 로그인 상태 정상")
                await page.close()
                return True
        except Exception as e:
            logger.error(f"로그인 확인 실패: {e}")
            await page.close()
            return False

    async def _login(self, context: BrowserContext) -> bool:
        """Tistory 카카오 로그인 - 2단계 입력 + #selectEmail 처리"""
        page = await context.new_page()
        try:
            # 1. Tistory 로그인 페이지
            await page.goto("https://www.tistory.com/auth/login", wait_until='networkidle', timeout=15000)
            await asyncio.sleep(2)
            logger.info(f"로그인 페이지: {page.url}")

            # 2. 카카오 로그인 버튼 클릭
            await page.click("text=카카오계정으로 로그인")
            await page.wait_for_load_state('networkidle', timeout=15000)
            await asyncio.sleep(2)
            logger.info(f"카카오 페이지: {page.url}")

            # 3. 카카오 로그인 - 이메일만 있는 경우 (한 화면에 이메일+비밀번호)
            await asyncio.sleep(1)
            has_pw = await page.locator('input[name="password"], input[type="password"]').count() > 0

            if has_pw:
                # 한 화면에 이메일+비밀번호 모두 있는 경우
                for id_sel in ['input[name="loginId"]', '#id_email_2', 'input[type="email"]']:
                    el = page.locator(id_sel).first
                    if await el.count() > 0:
                        await el.fill(self.tistory_id)
                        logger.info(f"이메일 입력 완료 ({id_sel})")
                        break
                await asyncio.sleep(0.3)
                for pw_sel in ['input[name="password"]', '#id_password_3', 'input[type="password"]']:
                    el = page.locator(pw_sel).first
                    if await el.count() > 0:
                        await el.fill(self.tistory_pw)
                        logger.info(f"비밀번호 입력 완료 ({pw_sel})")
                        break
                await asyncio.sleep(0.3)
                for submit_sel in ['button[type="submit"]', "button:has-text('로그인')'"]:
                    el = page.locator(submit_sel).first
                    if await el.count() > 0:
                        await el.click()
                        logger.info("로그인 버튼 클릭")
                        break
            else:
                # 이메일만 먼저 입력하는 경우
                for id_sel in ['input[name="loginId"]', '#id_email_2', 'input[type="email"]']:
                    el = page.locator(id_sel).first
                    if await el.count() > 0:
                        await el.fill(self.tistory_id)
                        logger.info(f"이메일 입력 완료 ({id_sel})")
                        break
                await asyncio.sleep(0.3)
                for confirm_sel in ["button:has-text('확인')", "button:has-text('다음')", 'button[type="submit"]']:
                    el = page.locator(confirm_sel).first
                    if await el.count() > 0:
                        await el.click()
                        logger.info(f"이메일 확인 클릭")
                        break
                await page.wait_for_load_state('networkidle', timeout=10000)
                await asyncio.sleep(1)

                # 비밀번호 입력
                for pw_sel in ['input[name="password"]', '#id_password_3', 'input[type="password"]']:
                    el = page.locator(pw_sel).first
                    if await el.count() > 0:
                        await el.fill(self.tistory_pw)
                        logger.info(f"비밀번호 입력 완료 ({pw_sel})")
                        break
                await asyncio.sleep(0.3)
                for submit_sel in ['button[type="submit"]', "button:has-text('로그인')"]:
                    el = page.locator(submit_sel).first
                    if await el.count() > 0:
                        await el.click()
                        logger.info("로그인 버튼 클릭")
                        break

            await page.wait_for_load_state('networkidle', timeout=15000)
            await asyncio.sleep(2)
            logger.info(f"로그인 후 URL: {page.url}")

            # 6. #selectEmail 화면 처리 (URL 해시 기반)
            if 'selectEmail' in page.url:
                logger.info("이메일 선택 화면 감지 (#selectEmail)")
                await asyncio.sleep(1)

                # 이메일 링크/버튼 클릭 시도
                clicked = False
                for email_sel in [
                    f'a:has-text("{self.tistory_id}")',
                    f'button:has-text("{self.tistory_id}")',
                    f'a:has-text("{self.tistory_id.split("@")[0]}")',
                    '.list_email li:first-child a',
                    '.list_email li:first-child button',
                    '.kakao_account li:first-child a',
                    'li:has-text("@") a',
                    'li:has-text("@") button',
                    'a.btn_account',
                ]:
                    el = page.locator(email_sel).first
                    if await el.count() > 0:
                        await el.click()
                        clicked = True
                        logger.info(f"이메일 선택 클릭 ({email_sel})")
                        break

                if not clicked:
                    # JS로 첫 번째 이메일 항목 강제 클릭
                    await page.evaluate("""
                        () => {
                            const links = document.querySelectorAll('a[href], button');
                            for (const el of links) {
                                if (el.textContent.includes('@')) {
                                    el.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    logger.info("JS로 이메일 강제 클릭 시도")

                await page.wait_for_load_state('networkidle', timeout=15000)
                await asyncio.sleep(3)
                logger.info(f"이메일 선택 후 URL: {page.url}")

            # 7. 로그인 성공 확인
            if 'tistory.com' in page.url and 'login' not in page.url:
                logger.info("✓ 재로그인 성공")
                storage = await context.storage_state()
                with open("auth.json", "w") as f:
                    json.dump(storage, f, indent=2)
                logger.info("✓ auth.json 갱신 완료")
                await page.close()
                return True
            else:
                logger.error(f"❌ 재로그인 실패. URL: {page.url}")
                await page.close()
                return False

        except Exception as e:
            logger.error(f"로그인 오류: {e}")
            await page.close()
            return False

    # -------------------------------------------------------------------------
    # 기사 본문 크롤링
    # -------------------------------------------------------------------------
    async def fetch_article(self, url: str) -> Optional[Dict]:
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
    # 이미지 다운로드 후 Tistory 업로드 (핫링킹 차단 우회)
    # -------------------------------------------------------------------------
    async def upload_image_to_tistory(self, page: Page, img_url: str) -> Optional[str]:
        try:
            headers = {
                'User-Agent': CharaLabConfig.USER_AGENT,
                'Referer': 'https://charalab.com/',
            }
            r = requests.get(img_url, headers=headers, timeout=10)
            if r.status_code != 200 or len(r.content) < 500:
                logger.warning(f"이미지 다운로드 실패 (status={r.status_code}, size={len(r.content)}): {img_url}")
                return None

            ext = img_url.split('.')[-1].split('?')[0].lower()
            if ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
                ext = 'jpg'
            mime = 'image/jpeg' if ext in ['jpg', 'jpeg'] else f'image/{ext}'

            img_b64 = base64.b64encode(r.content).decode()
            logger.info(f"이미지 다운로드 완료 ({len(r.content)} bytes)")

            result = await page.evaluate(f"""
                async () => {{
                    try {{
                        const byteString = atob('{img_b64}');
                        const arr = new Uint8Array(byteString.length);
                        for (let i = 0; i < byteString.length; i++) {{
                            arr[i] = byteString.charCodeAt(i);
                        }}
                        const blob = new Blob([arr], {{type: '{mime}'}});
                        const formData = new FormData();
                        formData.append('file', blob, 'image.{ext}');
                        const res = await fetch('/manage/post/image/upload', {{
                            method: 'POST',
                            body: formData,
                            credentials: 'include'
                        }});
                        const text = await res.text();
                        const match = text.match(/"url"\\s*:\\s*"([^"]+)"/);
                        return match ? match[1].replace(/\\\\/g, '') : null;
                    }} catch(e) {{
                        return null;
                    }}
                }}
            """)

            if result:
                logger.info(f"✅ Tistory 이미지 업로드 완료: {result}")
                return result
            else:
                logger.warning("Tistory 이미지 업로드 응답 없음 → 원본 URL 사용")
                return None

        except Exception as e:
            logger.error(f"이미지 업로드 오류: {e}")
            return None

    # -------------------------------------------------------------------------
    # Gemini 번역
    # -------------------------------------------------------------------------
    async def translate_article(self, title: str, text: str) -> Optional[Dict]:
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
    # Tistory 글쓰기
    # -------------------------------------------------------------------------
    async def write_to_tistory(
        self,
        page: Page,
        title: str,
        content_html: str,
        img_url: str = '',
    ) -> bool:
        try:
            write_url = f"https://{self.blog_name}.tistory.com/manage/post"
            await page.goto(write_url, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(5)
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)

            # ── 1. 제목 입력
            title_field = page.locator('#post-title-inp').first
            if await title_field.count() > 0:
                await title_field.click()
                await title_field.fill(title)
                logger.info("✓ 제목 입력 완료")
            else:
                logger.error("❌ 제목 필드 없음 (로그인 페이지로 리다이렉트 의심)")
                return False

            await asyncio.sleep(1)

            # ── 2. 이미지 Tistory 업로드
            tistory_img_url = ''
            if img_url:
                tistory_img_url = await self.upload_image_to_tistory(page, img_url) or img_url

            if tistory_img_url:
                img_tag = (
                    f'<p style="text-align:center;">'
                    f'<img src="{tistory_img_url}" '
                    f'style="max-width:100%; height:auto; display:block; margin:0 auto;">'
                    f'</p>\n'
                )
                content_html = img_tag + content_html

            # ── 3. HTML 모드 전환
            html_mode = False
            for mode_sel in [
                'button:has-text("기본모드")',
                'button.editor-mode-btn',
                'button[data-type="editor-mode"]',
            ]:
                mode_btn = page.locator(mode_sel).first
                if await mode_btn.count() > 0:
                    await mode_btn.click()
                    await asyncio.sleep(0.8)
                    for html_sel in [
                        'button:has-text("HTML")',
                        'a:has-text("HTML")',
                        'li:has-text("HTML")',
                    ]:
                        html_opt = page.locator(html_sel).first
                        if await html_opt.count() > 0:
                            await html_opt.click()
                            await asyncio.sleep(1)
                            html_mode = True
                            logger.info("✓ HTML 모드 전환 완료")
                            break
                if html_mode:
                    break

            if not html_mode:
                logger.warning("⚠️ HTML 모드 전환 실패 → 기본모드로 진행")

            await asyncio.sleep(1)

            # ── 4. 본문 입력
            editor_found = False

            if html_mode:
                injected = await page.evaluate(f"""
                    () => {{
                        const cm = document.querySelector('.CodeMirror');
                        if (cm && cm.CodeMirror) {{
                            cm.CodeMirror.setValue({json.dumps(content_html)});
                            return true;
                        }}
                        return false;
                    }}
                """)
                if injected:
                    editor_found = True
                    logger.info("✓ 본문 입력 완료 (HTML CodeMirror)")

                if not editor_found:
                    for sel in ['textarea.CodeMirror-code', 'textarea[name="content"]', 'textarea']:
                        el = page.locator(sel).first
                        if await el.count() > 0:
                            await el.click()
                            await page.keyboard.press('Control+a')
                            await page.keyboard.type(content_html, delay=0)
                            editor_found = True
                            logger.info(f"✓ 본문 입력 완료 (HTML textarea)")
                            break

            if not editor_found:
                frame = page.frame(name="editor-tistory_ifr")
                if frame:
                    editable = frame.locator('[contenteditable="true"]').first
                    if await editable.count() > 0:
                        await editable.click()
                        await editable.evaluate(
                            '(node, html) => { node.innerHTML = html; node.dispatchEvent(new Event("input", {bubbles: true})); }',
                            content_html
                        )
                        editor_found = True
                        logger.info("✓ 본문 입력 완료 (iframe fallback)")

                if not editor_found:
                    for f in page.frames:
                        if 'editor' in f.name.lower():
                            editable = f.locator('[contenteditable="true"]').first
                            if await editable.count() > 0:
                                await editable.click()
                                await editable.evaluate(
                                    '(node, html) => { node.innerHTML = html; node.dispatchEvent(new Event("input", {bubbles: true})); }',
                                    content_html
                                )
                                editor_found = True
                                logger.info(f"✓ 본문 입력 완료 ({f.name})")
                                break

            if not editor_found:
                logger.error("❌ 에디터를 찾을 수 없음")
                return False

            await asyncio.sleep(2)

            # ── 5. 대표이미지 설정
            if tistory_img_url:
                await self._set_thumbnail(page, tistory_img_url)

            # ── 6. 발행 (비공개)
            if await page.locator('#publish-layer-btn').count() > 0:
                await page.click('#publish-layer-btn')
                logger.info("✓ 발행 패널 열기")
                await asyncio.sleep(2)

                try:
                    priv_label = page.locator('label:has-text("비공개")').first
                    if await priv_label.count() > 0:
                        await priv_label.dispatch_event("click")
                        logger.info("✓ 비공개 설정")
                except Exception:
                    pass

                for s in ['button:has-text("발행")', 'button.btn_publish', '#publish-btn']:
                    if await page.locator(s).count() > 0:
                        await page.click(s)
                        logger.info(f"✓ 발행 클릭: {s}")
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

    async def _set_thumbnail(self, page: Page, img_url: str):
        try:
            for sel in [
                'button:has-text("대표이미지")',
                'button.btn-thumbnail',
                'label:has-text("대표이미지")',
                '[data-thumbnail]',
            ]:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    await asyncio.sleep(1)
                    for input_sel in ['input[placeholder*="URL"]', 'input[placeholder*="url"]', 'input.thumbnail-url']:
                        inp = page.locator(input_sel).first
                        if await inp.count() > 0:
                            await inp.fill(img_url)
                            await inp.press('Enter')
                            await asyncio.sleep(1)
                            logger.info("✓ 대표이미지 설정 완료")
                            break
                    for confirm_sel in ['button:has-text("확인")', 'button:has-text("저장")', 'button:has-text("적용")']:
                        confirm = page.locator(confirm_sel).first
                        if await confirm.count() > 0:
                            await confirm.click()
                            await asyncio.sleep(1)
                            break
                    return
            logger.warning("⚠️ 대표이미지 버튼 없음 → 스킵")
        except Exception as e:
            logger.warning(f"대표이미지 설정 실패 (스킵): {e}")

    # -------------------------------------------------------------------------
    # 메인 실행
    # -------------------------------------------------------------------------
    async def run(self):
        logger.info("CharaLab Final 가동")
        feed = feedparser.parse(CharaLabConfig.FEED_URL)

        allowed_tags = {"グッズ", "ニュース"}
        filtered = []
        for e in feed.entries:
            if e.link in self.posted_articles:
                continue
            tags = {t.term for t in e.tags} if hasattr(e, 'tags') else set()
            if tags & allowed_tags:
                filtered.append(e)
        articles = filtered[:5]

        if not articles:
            logger.info("새로운 기사 없음")
            return

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=['--disable-blink-features=AutomationControlled']
            )

            # auth.json 로드 (playwright_tistory_core.py 방식)
            if os.path.exists("auth.json"):
                with open("auth.json", "r") as f:
                    auth_data = json.load(f)
                context = await browser.new_context()
                await context.add_cookies(auth_data.get("cookies", []))
                logger.info("✓ auth.json 쿠키 로드 완료")
            else:
                context = await browser.new_context()
                logger.warning("auth.json 없음 → 빈 컨텍스트로 시작")

            # 로그인 확인 + 필요시 재로그인
            logged_in = await self.ensure_logged_in(context)
            if not logged_in:
                logger.error("❌ 로그인 실패 → 종료")
                await browser.close()
                return

            for entry in articles:
                logger.info(f"▶ {entry.title}")

                data = await self.fetch_article(entry.link)
                if not data:
                    logger.warning(f"본문 크롤링 실패: {entry.link}")
                    continue

                translated = await self.translate_article(entry.title, data['text'])
                if not translated:
                    continue

                page = await context.new_page()
                success = await self.write_to_tistory(
                    page,
                    translated['title'],
                    translated['content'],
                    img_url=data['img_url'],
                )
                await page.close()

                if success:
                    try:
                        self.supabase.table('charalab_articles').insert({
                            'title': translated['title'],
                            'content_html': translated['content'],
                            'original_url': entry.link,
                            'status': 'draft',
                        }).execute()
                    except Exception as e:
                        logger.warning(f"Supabase 저장 실패 (무시): {e}")

                    self.posted_articles[entry.link] = datetime.now().isoformat()
                    with open(self.history_file, 'w', encoding='utf-8') as f:
                        json.dump(self.posted_articles, f, ensure_ascii=False, indent=2)
                    logger.info(f"✅ 완료: {translated['title'][:40]}")

                await asyncio.sleep(5)

            await browser.close()


if __name__ == "__main__":
    asyncio.run(CharaLabSystemFinal().run())
