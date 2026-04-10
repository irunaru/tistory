"""
Tistory auth.json 초기 생성 및 로컬 테스트
- 첫 로그인 세션 저장
- 로컬 환경에서 테스트 가능
- GitHub Actions로 배포 전 검증
"""

import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
import os
import sys

# 로컬 모듈 import
sys.path.insert(0, str(Path(__file__).parent))
from playwright_tistory_core import (
    PlaywrightManager,
    SessionManager,
    TistoryAuth,
    TistoryWriter,
    PostScheduler,
    HumanBehavior,
    Config,
    logger
)


# ============================================================================
# 1. 초기 인증 설정 (첫 실행)
# ============================================================================

async def setup_initial_auth(tistory_id: str, tistory_password: str):
    """
    첫 로그인을 수행하고 세션을 auth.json으로 저장
    이후 GitHub Actions 실행 시 이 세션 재사용
    """
    logger.info("=" * 80)
    logger.info("Tistory 초기 인증 설정 시작")
    logger.info("=" * 80)
    
    playwright, browser = None, None
    
    try:
        # 1. Stealth 모드 브라우저 생성
        playwright, browser = await PlaywrightManager.create_browser_with_stealth()
        
        # 2. 빈 컨텍스트 생성 (새 로그인)
        context = await browser.new_context()
        
        # 3. Stealth 페이지 생성
        page = await PlaywrightManager.create_page_with_stealth(context)
        
        # 4. Tistory 로그인
        login_success = await TistoryAuth.login(page, tistory_id, tistory_password)
        
        if not login_success:
            logger.error("로그인 실패")
            return False
        
        # 5. 세션 저장 (auth.json)
        await SessionManager.save_session(context, Config.AUTH_JSON_PATH)
        
        logger.info("=" * 80)
        logger.info("초기 인증 완료. auth.json 생성됨")
        logger.info(f"경로: {Config.AUTH_JSON_PATH}")
        logger.info("=" * 80)
        
        await browser.close()
        await playwright.stop()
        return True
    
    except Exception as e:
        logger.error(f"초기 인증 설정 실패: {e}", exc_info=True)
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        return False


# ============================================================================
# 2. 세션 검증
# ============================================================================

async def validate_session():
    """저장된 auth.json이 유효한지 확인"""
    logger.info("=" * 80)
    logger.info("세션 검증 시작")
    logger.info("=" * 80)
    
    if not Config.AUTH_JSON_PATH.exists():
        logger.error(f"auth.json 파일 없음: {Config.AUTH_JSON_PATH}")
        logger.info("아래 명령으로 초기 인증을 수행하세요:")
        logger.info("python setup_tistory_auth.py --init")
        return False
    
    playwright, browser = None, None
    
    try:
        # 1. Stealth 모드 브라우저 생성
        playwright, browser = await PlaywrightManager.create_browser_with_stealth()
        
        # 2. 저장된 세션 로드
        context = await SessionManager.load_session(browser, Config.AUTH_JSON_PATH)
        if not context:
            logger.error("세션 로드 실패")
            return False
        
        # 3. Stealth 페이지 생성
        page = await PlaywrightManager.create_page_with_stealth(context)
        
        # 4. 로그인 상태 확인
        is_logged_in = await TistoryAuth.is_logged_in(page)
        
        if is_logged_in:
            logger.info("=" * 80)
            logger.info("✓ 세션 유효함. 자동 글쓰기 준비 완료")
            logger.info("=" * 80)
            await browser.close()
            await playwright.stop()
            return True
        else:
            logger.warning("세션 만료. 재인증 필요")
            await browser.close()
            await playwright.stop()
            return False
    
    except Exception as e:
        logger.error(f"세션 검증 실패: {e}", exc_info=True)
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        return False


# ============================================================================
# 3. 테스트 글쓰기 (드래프트)
# ============================================================================

async def test_write_post():
    """
    세션을 사용해 테스트 글쓰기 (드래프트로 저장, 발행 안 함)
    """
    logger.info("=" * 80)
    logger.info("테스트 글쓰기 시작")
    logger.info("=" * 80)
    
    if not Config.AUTH_JSON_PATH.exists():
        logger.error("auth.json 파일 없음. 먼저 초기 인증을 수행하세요.")
        return False
    
    playwright, browser = None, None
    
    try:
        # 1. 브라우저 및 세션 설정
        playwright, browser = await PlaywrightManager.create_browser_with_stealth()
        context = await SessionManager.load_session(browser, Config.AUTH_JSON_PATH)
        page = await PlaywrightManager.create_page_with_stealth(context)
        
        # 2. 로그인 상태 확인
        is_logged_in = await TistoryAuth.is_logged_in(page)
        if not is_logged_in:
            logger.error("로그인 상태 아님")
            return False
        
        # 3. 테스트 글 데이터
        test_title = "[테스트] Playwright 자동 글쓰기 시스템"
        test_content = """
        <h2>Playwright 자동 글쓰기 테스트</h2>
        <p>이 글은 Playwright 자동화 시스템으로 작성되었습니다.</p>
        <ul>
            <li>Stealth 모드: ✓ 활성화</li>
            <li>세션 유지: ✓ auth.json 사용</li>
            <li>GitHub Actions 호환: ✓ 준비 완료</li>
        </ul>
        <p>테스트 작성 시간: {}</p>
        """.format(__import__('datetime').datetime.now().isoformat())
        
        # 4. 글 작성 (예약 발행 없이)
        test_tags = ['테스트', 'playwright', '자동화']
        
        write_success = await TistoryWriter.write_post(
            page=page,
            title=test_title,
            content_html=test_content,
            category="테스트",
            tags=test_tags,
            scheduled_time=None  # 예약 발행 안 함
        )
        
        if write_success:
            logger.info("=" * 80)
            logger.info("✓ 테스트 글쓰기 성공")
            logger.info("=" * 80)
        else:
            logger.error("테스트 글쓰기 실패")
        
        await browser.close()
        await playwright.stop()
        return write_success
    
    except Exception as e:
        logger.error(f"테스트 글쓰기 실패: {e}", exc_info=True)
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        return False


# ============================================================================
# 4. 스케줄 계산 테스트
# ============================================================================

def test_schedule():
    """게시 스케줄 계산 테스트"""
    logger.info("=" * 80)
    logger.info("스케줄 계산 테스트")
    logger.info("=" * 80)
    
    for i in range(5):
        next_time = PostScheduler.calculate_next_post_time()
        logger.info(f"  {i+1}. {next_time.isoformat()}")
    
    logger.info("=" * 80)


# ============================================================================
# 5. CLI 인터페이스
# ============================================================================

async def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='Tistory 자동 글쓰기 설정 및 테스트')
    parser.add_argument(
        '--init',
        action='store_true',
        help='첫 로그인으로 auth.json 생성'
    )
    parser.add_argument(
        '--validate',
        action='store_true',
        help='저장된 세션(auth.json) 검증'
    )
    parser.add_argument(
        '--test-write',
        action='store_true',
        help='테스트 글쓰기 (드래프트)'
    )
    parser.add_argument(
        '--test-schedule',
        action='store_true',
        help='스케줄 계산 테스트'
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help='전체 테스트 실행 (init → validate → test-write)'
    )
    
    args = parser.parse_args()
    
    # 환경 변수 로드
    load_dotenv()
    
    if args.init or args.all:
        success = await setup_initial_auth(Config.TISTORY_LOGIN_ID, Config.TISTORY_PASSWORD)
        if not success:
            logger.error("초기 인증 실패")
            return 1
        
        if not args.all:
            return 0
    
    if args.validate or args.all:
        success = await validate_session()
        if not success:
            logger.error("세션 검증 실패")
            return 1
        
        if not args.all:
            return 0
    
    if args.test_write or args.all:
        success = await test_write_post()
        if not success:
            logger.error("테스트 글쓰기 실패")
            return 1
        
        if not args.all:
            return 0
    
    if args.test_schedule:
        test_schedule()
        return 0
    
    # 기본 동작: --all과 동일
    if not any([args.init, args.validate, args.test_write, args.test_schedule, args.all]):
        logger.info("사용법: python setup_tistory_auth.py [옵션]")
        logger.info("  --init           첫 로그인으로 auth.json 생성")
        logger.info("  --validate       저장된 세션 검증")
        logger.info("  --test-write     테스트 글쓰기")
        logger.info("  --test-schedule  스케줄 계산 테스트")
        logger.info("  --all            전체 테스트 (권장)")
        return 0
    
    logger.info("모든 테스트 완료")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
