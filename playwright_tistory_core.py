"""
Tistory 자동 글쓰기 - Playwright Core Module
- GitHub Actions 환경 최적화
- Session 유지 (auth.json)
- Stealth mode + Anti-bot 회피
- Supabase 상태 관리
"""

import asyncio
import json
import os
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv
load_dotenv()

from playwright.async_api import async_playwright, Browser, Page, BrowserContext
from supabase import create_client, Client

# ============================================================================
# 1. 환경 변수 & 설정
# ============================================================================

class Config:
    """GitHub Actions 환경 변수 관리"""
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    TISTORY_LOGIN_ID = os.getenv("TISTORY_LOGIN_ID")     # 수정
    TISTORY_PASSWORD = os.getenv("TISTORY_PASSWORD")
    TISTORY_BLOG_NAME = os.getenv("TISTORY_BLOG_NAME")   # 추가
    AUTH_JSON_PATH = Path("auth.json")
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # Tistory URL 설정
    TISTORY_LOGIN_URL = "https://www.tistory.com/auth/login"
    
    # BLOG_NAME을 사용하여 글쓰기 URL 생성
    @property
    def TISTORY_WRITE_URL(self):
        return f"https://{self.TISTORY_BLOG_NAME}.tistory.com/manage/post"
    
    # 게시 정책
    MIN_POST_INTERVAL_HOURS = 2
    MAX_POST_INTERVAL_HOURS = 6
    MAX_POSTS_PER_DAY = 3
    SCHEDULE_HOUR_RANGE = (9, 22)  # 오전 9시~밤 10시 범위


# ============================================================================
# 2. 로깅
# ============================================================================

import logging

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=getattr(logging, Config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)


# ============================================================================
# 3. Supabase 클라이언트
# ============================================================================

class SupabaseManager:
    """Supabase 상태 관리"""
    
    def __init__(self):
        self.client: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
    
    async def get_ready_posts(self, limit: int = 1) -> list:
        """status='ready'인 포스트 조회"""
        try:
            response = self.client.table('characters').select('*').eq('status', 'ready').limit(limit).execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Supabase 조회 실패: {e}")
            return []
    
    async def update_post_status(self, post_id: str, status: str, metadata: Optional[Dict[str, Any]] = None):
        """포스트 상태 업데이트"""
        try:
            update_data = {'status': status, 'updated_at': datetime.utcnow().isoformat()}
            if metadata:
                update_data.update(metadata)
            
            self.client.table('characters').update(update_data).eq('id', post_id).execute()
            logger.info(f"포스트 {post_id} 상태: {status}")
        except Exception as e:
            logger.error(f"상태 업데이트 실패 {post_id}: {e}")
    
    async def get_today_post_count(self) -> int:
        """오늘 게시된 포스트 수"""
        try:
            today = datetime.utcnow().date().isoformat()
            response = self.client.table('characters').select('id').eq('status', 'posted').gte('posted_at', today).execute()
            return len(response.data or [])
        except Exception as e:
            logger.error(f"일일 포스트 수 조회 실패: {e}")
            return 0


# ============================================================================
# 4. 세션 관리 (auth.json)
# ============================================================================

class SessionManager:
    """Playwright 세션 유지"""
    
    @staticmethod
    async def save_session(context: BrowserContext, auth_json_path: Path = Config.AUTH_JSON_PATH):
        """세션 저장 (Playwright context storage)"""
        try:
            # BrowserContext의 쿠키와 로컬스토리지 저장
            storage = await context.storage_state()
            with open(auth_json_path, 'w') as f:
                json.dump(storage, f, indent=2)
            logger.info(f"세션 저장: {auth_json_path}")
        except Exception as e:
            logger.error(f"세션 저장 실패: {e}")
    
    @staticmethod
    async def load_session(browser: Browser, auth_json_path: Path = Config.AUTH_JSON_PATH) -> Optional[BrowserContext]:
        """저장된 세션 로드"""
        try:
            if not auth_json_path.exists():
                logger.warning(f"세션 파일 없음: {auth_json_path}")
                return None
            
            with open(auth_json_path, 'r') as f:
                storage_state = json.load(f)
            
            context = await browser.new_context(storage_state=storage_state)
            logger.info(f"세션 로드: {auth_json_path}")
            return context
        except Exception as e:
            logger.error(f"세션 로드 실패: {e}")
            return None


# ============================================================================
# 5. Playwright 초기화 (Stealth Mode)
# ============================================================================

class PlaywrightManager:
    """Playwright + Stealth Mode 설정"""
    
    STEALTH_JS = """
    // webdriver 속성 숨기기
    Object.defineProperty(navigator, 'webdriver', {
        get: () => false,
    });
    
    // plugins 속성 위장
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
    });
    
    // languages 설정
    Object.defineProperty(navigator, 'languages', {
        get: () => ['ko-KR', 'ko', 'en-US'],
    });
    
    // chrome 객체 위장
    window.chrome = {
        runtime: {}
    };
    
    // permissions 오버라이드
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
    """
    
    @staticmethod
    async def create_browser_with_stealth():
        """Stealth 모드가 적용된 브라우저 생성"""
        playwright = await async_playwright().start()
        
        browser = await playwright.chromium.launch(
            headless=False,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',  # GitHub Actions 호환
                '--disable-gpu',  # 헤드리스 모드 안정성
                '--disable-web-resources',
                '--disable-default-apps',
                '--disable-sync',
                '--metrics-recording-only',
                '--disable-component-extensions-with-background-pages',
            ]
        )
        
        logger.info("Stealth 모드 브라우저 생성 완료")
        return playwright, browser
    
    @staticmethod
    async def create_page_with_stealth(context: BrowserContext) -> Page:
        """Stealth 모드가 적용된 페이지 생성"""
        page = await context.new_page()
        
        # User-Agent 설정 (자연스러운 한국 사용자)
        await page.set_extra_http_headers({
            'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
        })
        
        # Stealth JS 주입
        await page.add_init_script(PlaywrightManager.STEALTH_JS)
        
        # 콘솔 메시지 억제 (봇 탐지 회피)
        page.on('console', lambda msg: None)
        
        logger.info("Stealth 모드 페이지 생성 완료")
        return page


# ============================================================================
# 6. 게시 간격 관리
# ============================================================================

class PostScheduler:
    """게시 간격 및 예약 시간 관리"""
    
    @staticmethod
    def calculate_next_post_time() -> datetime:
        """다음 게시 시간 계산 (불규칙 간격)"""
        now = datetime.now()
        
        # 금지 시간대 (00:00 ~ 06:00)
        if 0 <= now.hour < 6:
            next_time = now.replace(hour=6, minute=0, second=0, microsecond=0)
            logger.info(f"금지 시간대: {next_time}에 재스케줄")
            return next_time
        
        # 활동 시간대 (06:00 ~ 23:59)
        interval = random.randint(
            Config.MIN_POST_INTERVAL_HOURS * 60,
            Config.MAX_POST_INTERVAL_HOURS * 60
        )
        next_time = now + timedelta(minutes=interval)
        
        # 스케줄 시간이 활동 시간대를 벗어나면 조정
        if next_time.hour >= 22:
            next_time = next_time.replace(hour=9, minute=0, second=0, microsecond=0)
            if next_time.date() == now.date():
                next_time += timedelta(days=1)
        
        logger.info(f"다음 게시 예정: {next_time.isoformat()}")
        return next_time
    
    @staticmethod
    async def can_post_today(today_count: int) -> bool:
        """일일 게시 제한 확인"""
        if today_count >= Config.MAX_POSTS_PER_DAY:
            logger.warning(f"일일 게시 제한 도달: {today_count}/{Config.MAX_POSTS_PER_DAY}")
            return False
        return True


# ============================================================================
# 7. 사람처럼 행동하기 (Humanization)
# ============================================================================

class HumanBehavior:
    """자연스러운 사용자 행동 시뮬레이션"""
    
    @staticmethod
    async def type_slowly(page: Page, selector: str, text: str, delay_ms: int = 50):
        """느린 타이핑 시뮬레이션"""
        await page.focus(selector)
        for char in text:
            await page.type(selector, char, delay=random.randint(20, delay_ms))
            # 간헐적 일시 정지 (사람처럼)
            if random.random() < 0.1:
                await asyncio.sleep(random.uniform(0.1, 0.3))
    
    @staticmethod
    async def random_delay(min_ms: int = 500, max_ms: int = 2000):
        """무작위 대기"""
        delay = random.randint(min_ms, max_ms) / 1000
        await asyncio.sleep(delay)
    
    @staticmethod
    async def scroll_page(page: Page):
        """페이지 스크롤 (사람처럼)"""
        scroll_height = await page.evaluate("document.body.scrollHeight")
        current_position = 0
        
        while current_position < scroll_height:
            await page.evaluate(f"window.scrollBy(0, {random.randint(100, 300)})")
            current_position += random.randint(100, 300)
            await HumanBehavior.random_delay(100, 500)


# ============================================================================
# 8. 로그인 프로세스
# ============================================================================

class TistoryAuth:
    """Tistory 인증 처리"""
    
    @staticmethod
    async def is_logged_in(page: Page) -> bool:
        """로그인 상태 확인"""
        try:
            # 로그인 후 접근 가능한 페이지로 이동
            await page.goto(Config().TISTORY_WRITE_URL, wait_until='networkidle', timeout=10000)
            
            # 로그인 페이지로 리디렉트되면 미인증
            if 'login' in page.url:
                logger.info("미인증 상태 감지")
                return False
            
            logger.info("인증 상태 확인 완료")
            return True
        except Exception as e:
            logger.error(f"로그인 상태 확인 실패: {e}")
            return False
    
    @staticmethod
    async def login(page: Page, tistory_id: str, tistory_password: str) -> bool:
        """Tistory 로그인"""
        try:
            logger.info("Tistory 로그인 시작")
            
            await page.goto(Config.TISTORY_LOGIN_URL, wait_until='networkidle')
            await HumanBehavior.random_delay(1000, 2000)
            
            # ID 입력
            await page.fill('input[type="text"]', tistory_id)
            await HumanBehavior.random_delay(500, 1000)
            
            # 비밀번호 입력
            await page.fill('input[type="password"]', tistory_password)
            await HumanBehavior.random_delay(500, 1000)
            
            # 로그인 버튼 클릭
            await page.click('button[type="submit"]')
            
            # 로그인 대기
            await page.wait_for_load_state('networkidle', timeout=15000)
            await HumanBehavior.random_delay(2000, 3000)
            
            # 로그인 성공 확인
            if 'login' not in page.url:
                logger.info("Tistory 로그인 성공")
                return True
            else:
                logger.error("Tistory 로그인 실패")
                return False
            
        except Exception as e:
            logger.error(f"로그인 프로세스 실패: {e}")
            return False


# ============================================================================
# 9. 글쓰기 프로세스
# ============================================================================

class TistoryWriter:
    """Tistory 글쓰기"""
    
    @staticmethod
    async def write_post(
        page: Page,
        title: str,
        content_html: str,
        category: str = "",
        tags: list = None,
        scheduled_time: Optional[datetime] = None,
    ) -> bool:
        """Tistory에 글 작성 및 게시 (최종 분석 반영)"""
        try:
            logger.info(f"글쓰기 시작: {title[:50]}...")
            
            await page.goto(Config().TISTORY_WRITE_URL, wait_until='networkidle', timeout=30000)
            await asyncio.sleep(5)  # 로딩 대기
            await page.keyboard.press("Escape")  # 팝업 제거
            await HumanBehavior.random_delay(1000, 2000)

            # 1. 제목 입력
            # 분석 결과: #post-title-inp
            title_field = page.locator('#post-title-inp').first
            if await title_field.count() > 0:
                await title_field.click()
                await title_field.fill(title)
                logger.info("제목 입력 완료 (#post-title-inp)")
            else:
                logger.warning("표준 제목 필드 탐지 실패. JS 주입 시도.")
                await page.evaluate('document.querySelectorAll("textarea, input")[0].value = arguments[0]', title)

            # 2. 카테고리 선택
            if category:
                try:
                    # 분석 결과: #category-btn 클릭 후 목록에서 선택
                    if await page.locator('#category-btn').count() > 0:
                        await page.click('#category-btn')
                        await HumanBehavior.random_delay(800, 1200)
                        # 카테고리 목록에서 텍스트로 선택
                        await page.click(f'text="{category}"')
                        logger.info(f"카테고리 설정 완료: {category}")
                except Exception as e:
                    logger.warning(f"카테고리 선택 실패: {e}")

            # 3. 본문 입력 (Editor 탐색)
            editor_found = False
            # iframe 에디터 ("editor-tistory_ifr" 분석됨)
            frame = page.frame(name="editor-tistory_ifr")
            if frame:
                editable = frame.locator('[contenteditable="true"]').first
                if await editable.count() > 0:
                    await editable.click()
                    await editable.fill(content_html)
                    editor_found = True
                    logger.info("본문 입력 완료 (iframe: editor-tistory_ifr)")
            
            if not editor_found:
                # 일반적인 frame 탐색
                for f in page.frames:
                    if 'editor' in f.name.lower():
                        editable = f.locator('[contenteditable="true"]').first
                        if await editable.count() > 0:
                            await editable.click()
                            await editable.fill(content_html)
                            editor_found = True
                            break
            
            if not editor_found:
                logger.error("에디터를 찾을 수 없음")
                return False

            await HumanBehavior.random_delay(1000, 2000)
            
            # 4. 태그 입력
            if tags:
                # 분석 결과: #tagText
                tag_field = page.locator('#tagText').first
                if await tag_field.count() > 0:
                    tags_str = ','.join(tags)
                    await tag_field.fill(tags_str)
                    await page.keyboard.press("Enter")
                    logger.info("태그 입력 완료")
            
            # 5. 예약 게시 설정
            if scheduled_time:
                await TistoryWriter._set_schedule(page, scheduled_time)
            
            # 6. 발행 버튼 클릭
            # 분석 결과: '완료' 버튼 (#publish-layer-btn) 클릭 후 '발행' 버튼 클릭 필요
            if await page.locator('#publish-layer-btn').count() > 0:
                await page.click('#publish-layer-btn')
                await HumanBehavior.random_delay(1500, 2500)
                
                # 최종 발행 버튼 탐색 (여러 후보)
                final_publish_selectors = [
                    'button:has-text("발행")', 
                    'button.btn_publish',
                    '#publish-btn'
                ]
                for s in final_publish_selectors:
                    if await page.locator(s).count() > 0:
                        await page.click(s)
                        logger.info("최종 발행 버튼 클릭 완료")
                        break
            
            await page.wait_for_load_state('networkidle', timeout=15000)
            await asyncio.sleep(3)
            
            if 'view' in page.url or 'post' in page.url:
                logger.info("✅ 글 게시 성공")
                return True
            else:
                logger.warning(f"게시 후 URL 확인 불확실: {page.url}")
                return True
                
        except Exception as e:
            logger.error(f"❌ 글쓰기 프로세스 실패: {e}")
            return False

            # 2. 카테고리 선택 (있으면)

    @staticmethod
    async def _set_schedule(page: Page, scheduled_time: datetime):
        """예약 발행 시간 설정"""
        try:
            # 예약 발행 체크박스
            await page.click('input[type="checkbox"][name*="schedule"]')
            await HumanBehavior.random_delay(500, 1000)
            
            # 날짜 입력
            date_str = scheduled_time.strftime('%Y-%m-%d')
            await page.fill('input[type="date"]', date_str)
            
            # 시간 입력
            time_str = scheduled_time.strftime('%H:%M')
            await page.fill('input[type="time"]', time_str)
            
            logger.info(f"예약 발행: {date_str} {time_str}")
        except Exception as e:
            logger.warning(f"예약 발행 설정 실패: {e}")


# ============================================================================
# 10. 메인 오케스트레이션
# ============================================================================

class TistoryAutoPosting:
    """Tistory 자동 글쓰기 오케스트레이션"""
    
    def __init__(self):
        self.supabase = SupabaseManager()
        self.playwright = None
        self.browser = None
    
    async def run(self):
        """메인 실행 루틴"""
        try:
            logger.info("=" * 80)
            logger.info("Tistory 자동 글쓰기 시작")
            logger.info("=" * 80)
            
            # 1. 일일 게시 제한 확인
            today_count = await self.supabase.get_today_post_count()
            can_post = await PostScheduler.can_post_today(today_count)
            if not can_post:
                logger.info("일일 게시 제한 도달. 작업 종료.")
                return
            
            # 2. ready 포스트 조회
            ready_posts = await self.supabase.get_ready_posts(limit=1)
            if not ready_posts:
                logger.info("게시 준비 포스트 없음. 작업 종료.")
                return
            
            post = ready_posts[0]
            post_id = post.get('id')
            
            # 3. Playwright 초기화
            self.playwright, self.browser = await PlaywrightManager.create_browser_with_stealth()
            
            # 4. 세션 로드 (기존 auth.json)
            context = await SessionManager.load_session(self.browser)
            if not context:
                context = await self.browser.new_context()
            
            # 5. 페이지 생성
            page = await PlaywrightManager.create_page_with_stealth(context)
            
            # 6. 로그인 확인
            is_logged_in = await TistoryAuth.is_logged_in(page)
            if not is_logged_in:
                logger.info("새로 로그인 필요")
                login_success = await TistoryAuth.login(page, Config.TISTORY_LOGIN_ID, Config.TISTORY_PASSWORD)
                if not login_success:
                    await self.supabase.update_post_status(post_id, 'failed', {'error': 'login_failed'})
                    return
            
            # 7. 세션 저장
            await SessionManager.save_session(context)
            
            # 8. 글 작성
            title = post.get('title', 'Untitled')
            content_html = post.get('content_html', '<p></p>')
            category = post.get('category', '')
            tags = post.get('tags', [])
            
            # 예약 시간 계산
            scheduled_time = PostScheduler.calculate_next_post_time()
            
            write_success = await TistoryWriter.write_post(
                page=page,
                title=title,
                content_html=content_html,
                category=category,
                tags=tags,
                scheduled_time=scheduled_time
            )
            
            if write_success:
                # 9. Supabase 상태 업데이트
                await self.supabase.update_post_status(
                    post_id,
                    'posted',
                    {
                        'posted_at': datetime.utcnow().isoformat(),
                        'scheduled_time': scheduled_time.isoformat()
                    }
                )
                logger.info("=" * 80)
                logger.info("글 발행 완료 및 상태 업데이트")
                logger.info("=" * 80)
            else:
                await self.supabase.update_post_status(post_id, 'failed', {'error': 'write_failed'})
        
        except Exception as e:
            logger.error(f"메인 프로세스 실패: {e}", exc_info=True)
        
        finally:
            # 리소스 정리
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("Playwright 종료")


# ============================================================================
# 11. 실행
# ============================================================================

if __name__ == "__main__":
    async def main():
        auto_posting = TistoryAutoPosting()
        await auto_posting.run()
    
    asyncio.run(main())
