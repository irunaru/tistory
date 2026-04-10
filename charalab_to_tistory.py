import os
import asyncio
import logging
from dotenv import load_dotenv
from supabase import create_client
from playwright_tistory_core import PlaywrightManager, TistoryWriter, Config

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

class CharaLabToTistory:
    def __init__(self):
        self.supabase = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        # 비공개 저장 여부 설정 (환경변수)
        self.save_as_private = os.getenv("SAVE_AS_PRIVATE", "false").lower() == "true"

    async def get_ready_posts(self):
        """Supabase에서 게시 대기 중인 기사 가져오기"""
        # save_as_private 모드면 'draft'인 것도 가져오고, 아니면 'ready'인 것만 가져옴
        status_to_fetch = 'draft' if self.save_as_private else 'ready'
        
        logger.info(f"Supabase에서 '{status_to_fetch}' 상태 기사 조회 중...")
        try:
            response = self.supabase.table('characters').select('*').eq('status', status_to_fetch).execute()
            return response.data
        except Exception as e:
            logger.error(f"Supabase 조회 실패: {e}")
            return []

    async def update_status(self, post_id, status):
        """게시 완료 후 상태 업데이트"""
        try:
            self.supabase.table('characters').update({'status': status}).eq('id', post_id).execute()
        except Exception as e:
            logger.error(f"상태 업데이트 실패 (ID: {post_id}): {e}")

    async def run(self):
        posts = await self.get_ready_posts()
        if not posts:
            logger.info("게시할 기사가 없습니다.")
            return

        logger.info(f"{len(posts)}개의 기사를 처리합니다.")

        async with PlaywrightManager.create_browser_with_stealth() as (browser, context):
            writer = TistoryWriter(context)
            
            for post in posts:
                logger.info(f"게시 시작: {post['title']}")
                
                # TistoryWriter.write_post는 visibility 인자를 받도록 업데이트됨
                success = await writer.write_post(
                    title=post['title'],
                    content_html=post['content_html'],
                    category=post.get('category', 'Character News'),
                    tags=post.get('tags', []),
                    visibility='private' if self.save_as_private else 'public'
                )
                
                if success:
                    # 비공개 저장이었으면 'draft' 유지, 아니면 'posted'로 변경
                    new_status = 'posted' if not self.save_as_private else 'draft'
                    await self.update_status(post['id'], new_status)
                    logger.info(f"✅ 기사 처리 성공: {post['title']} (상태: {new_status})")
                else:
                    logger.error(f"❌ 기사 게시 실패: {post['title']}")
                
                # 연속 게시 사이의 딜레이
                await asyncio.sleep(5)

if __name__ == "__main__":
    app = CharaLabToTistory()
    asyncio.run(app.run())
