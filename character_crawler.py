"""
character_crawler.py
GitHub Actions 전용: 포켓몬(ポケモンWiki 일본어) + 산리오(Fandom 영문) 캐릭터 도감 크롤링
→ Gemini 번역 (한국어 도감 형식) → Supabase(charalab_articles) 저장
포켓몬 이미지: PokeAPI 공식 아트워크 (도감번호 기반)
"""

import os
import json
import logging
import requests
import re
import time
from bs4 import BeautifulSoup
from datetime import datetime
from typing import Dict, Optional, List
from dotenv import load_dotenv
from supabase import create_client
import google.generativeai as genai

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
load_dotenv()

# -------------------------------------------------------------------------
# 설정
# -------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TABLE_NAME = "charalab_articles"
HISTORY_FILE = "posted_articles_character.json"

POKEMON_LIST_URL = "https://wiki.xn--rckteqa2e.com/wiki/%E3%83%9D%E3%82%B1%E3%83%A2%E3%83%B3%E4%B8%80%E8%A6%A7"
POKEMON_BASE_URL = "https://wiki.xn--rckteqa2e.com"
POKEAPI_IMG_URL = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/other/official-artwork/{dex_num}.png"

SANRIO_LIST_URL = "https://thesanrio.fandom.com/wiki/Category:Characters"
SANRIO_BASE_URL = "https://thesanrio.fandom.com"

POKEMON_PER_DAY = 3
SANRIO_PER_DAY = 2

# -------------------------------------------------------------------------
# 저작권 문구 제거
# -------------------------------------------------------------------------
COPYRIGHT_PATTERNS = [
    r'©[^\n<]*',
    r'&copy;[^\n<]*',
    r'All rights reserved[^\n<]*',
]

def remove_copyright(html: str) -> str:
    for pattern in COPYRIGHT_PATTERNS:
        html = re.sub(pattern, '', html, flags=re.DOTALL)
    return html.strip()


class CharacterCrawler:
    def __init__(self):
        self.supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        genai.configure(api_key=GEMINI_API_KEY)
        self.model = genai.GenerativeModel(GEMINI_MODEL)
        self.headers = {'User-Agent': USER_AGENT}

        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                self.history = json.load(f)
        else:
            self.history = {}

    def save_history(self):
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, ensure_ascii=False, indent=2)

    def is_in_supabase(self, url: str) -> bool:
        try:
            res = self.supabase.table(TABLE_NAME) \
                .select('id') \
                .eq('original_url', url) \
                .execute()
            return bool(res.data)
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # 포켓몬 URL 목록 수집
    # -------------------------------------------------------------------------
    def get_pokemon_urls(self) -> List[str]:
        # 포켓몬 타입명, 제외할 단어
        EXCLUDE_WORDS = {
            'ぜんこくずかん','タイプ','くさ','どく','ほのお','ひこう','ドラゴン','みず',
            'むし','ノーマル','でんき','こおり','かくとう','じめん','エスパー','いわ',
            'ゴースト','はがね','あく','フェアリー','リージョンフォーム',
        }
        try:
            from urllib.parse import unquote
            r = requests.get(POKEMON_LIST_URL, headers=self.headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            urls = []
            seen = set()

            for a in soup.select('table a[href]'):
                href = a.get('href', '')
                if not href.startswith('/wiki/') or ':' in href:
                    continue
                decoded = unquote(href).replace('/wiki/', '')
                if decoded in EXCLUDE_WORDS or len(decoded) <= 2:
                    continue
                full_url = POKEMON_BASE_URL + href
                if full_url not in seen and full_url not in self.history:
                    seen.add(full_url)
                    urls.append(full_url)

            logger.info(f"포켓몬 미수집 URL: {len(urls)}개")
            return urls[:POKEMON_PER_DAY]
        except Exception as e:
            logger.error(f"포켓몬 URL 수집 실패: {e}")
            return []

    # -------------------------------------------------------------------------
    # 산리오 URL 목록 수집
    # -------------------------------------------------------------------------
    def get_sanrio_urls(self) -> List[str]:
        try:
            r = requests.get(SANRIO_LIST_URL, headers=self.headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')
            urls = []
            seen = set()
            for a in soup.select('a[href*="/wiki/"]'):
                href = a.get('href', '')
                if (href.startswith('/wiki/') and
                        'Category' not in href and
                        'Special' not in href and
                        'File' not in href and
                        'Talk' not in href):
                    full_url = SANRIO_BASE_URL + href
                    if full_url not in seen and full_url not in self.history:
                        seen.add(full_url)
                        urls.append(full_url)
            logger.info(f"산리오 미수집 URL: {len(urls)}개")
            return urls[:SANRIO_PER_DAY]
        except Exception as e:
            logger.error(f"산리오 URL 수집 실패: {e}")
            return []

    # -------------------------------------------------------------------------
    # 포켓몬 페이지 크롤링
    # -------------------------------------------------------------------------
    def fetch_pokemon(self, url: str) -> Optional[Dict]:
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')

            content = soup.select_one('#mw-content-text')
            if not content:
                return None

            # 도감번호 추출 (全国図鑑 #0025 형태)
            dex_num = None
            text_all = content.get_text()
            dex_match = re.search(r'全国図鑑\s*#(\d+)', text_all)
            if dex_match:
                dex_num = int(dex_match.group(1))

            # PokeAPI 공식 아트워크 이미지
            img_url = ''
            if dex_num:
                img_url = POKEAPI_IMG_URL.format(dex_num=dex_num)
            else:
                # 도감번호 없으면 og:image 폴백
                og_img = soup.select_one('meta[property="og:image"]')
                if og_img:
                    img_url = og_img.get('content', '')

            # 불필요한 태그 제거
            for tag in content.select('.toc, .navbox, script, style, table.navbox'):
                tag.decompose()

            text = content.get_text()[:4000]

            # 일본어 캐릭터명 추출 (페이지 제목)
            title_tag = soup.select_one('h1#firstHeading, h1.firstHeading')
            name_ja = title_tag.get_text().strip() if title_tag else url.split('/wiki/')[-1]

            return {
                'text': text,
                'img_url': img_url,
                'name': name_ja,
                'source': 'pokemon',
            }
        except Exception as e:
            logger.error(f"포켓몬 크롤링 실패: {e}")
            return None

    # -------------------------------------------------------------------------
    # 산리오 페이지 크롤링
    # -------------------------------------------------------------------------
    def fetch_sanrio(self, url: str) -> Optional[Dict]:
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            soup = BeautifulSoup(r.text, 'html.parser')

            # 이미지: og:image 우선
            img_url = ''
            og_img = soup.select_one('meta[property="og:image"]')
            if og_img:
                img_url = og_img.get('content', '')

            # og:image 없으면 data-src (lazy load) 시도
            if not img_url:
                for img in soup.select('.pi-image img, .infobox img, figure img'):
                    src = img.get('data-src', '') or img.get('src', '')
                    if src and src.startswith('http') and 'data:image' not in src:
                        img_url = src
                        break

            content = soup.select_one('.mw-parser-output')
            if not content:
                return None

            for tag in content.select('.navbox, script, style, .toc'):
                tag.decompose()

            text = content.get_text()[:4000]
            name_en = url.split('/wiki/')[-1].replace('_', ' ')

            return {
                'text': text,
                'img_url': img_url,
                'name': name_en,
                'source': 'sanrio',
            }
        except Exception as e:
            logger.error(f"산리오 크롤링 실패: {e}")
            return None

    # -------------------------------------------------------------------------
    # Gemini 번역 (한국어 도감 형식)
    # -------------------------------------------------------------------------
    def translate_character(self, data: Dict) -> Optional[Dict]:
        source = data['source']
        name = data['name']
        text = data['text']

        if source == 'pokemon':
            format_guide = (
                "포켓몬 도감 형식으로 작성하세요:\n"
                "- 기본 정보 (타입, 분류, 특성, 도감번호)\n"
                "- 외형 특징\n"
                "- 능력과 기술\n"
                "- 진화 정보\n"
                "- 게임/애니에서의 역할\n"
                "- 흥미로운 사실"
            )
            lang = "일본어"
        else:  # sanrio
            format_guide = (
                "산리오 캐릭터 도감 형식으로 작성하세요:\n"
                "- 기본 프로필 (생일, 성격, 좋아하는 것)\n"
                "- 외형 특징\n"
                "- 탄생 배경과 역사\n"
                "- 주요 친구 캐릭터\n"
                "- 인기 굿즈와 콜라보\n"
                "- 한국에서의 인기"
            )
            lang = "영어"

        prompt = (
            f"다음 {lang} 캐릭터 정보를 한국어 캐릭터 도감으로 변환하세요.\n"
            f"캐릭터명: {name}\n\n"
            "【핵심 원칙】\n"
            "원문의 정보를 100% 충실하게 전달하는 것이 최우선입니다.\n"
            "창작이나 추측을 추가하지 마세요.\n\n"
            f"{format_guide}\n\n"
            "아래 규칙을 반드시 지켜서 작성하세요:\n"
            "1. 친근하고 읽기 쉬운 한국어로 작성할 것.\n"
            "2. 도입부는 이 캐릭터를 처음 접하는 독자도 바로 이해할 수 있게 작성할 것.\n"
            "3. h2 소제목을 3~4개 포함하여 구조화할 것.\n"
            "4. 글자수 1000자 이상으로 충분한 정보를 담을 것.\n"
            "5. img 태그는 절대 포함하지 말 것.\n"
            "6. 저작권 표시(©, ®, ™) 제거.\n\n"
            "반드시 아래 형식으로만 답하세요 (다른 설명 없이):\n"
            "[TITLE]한국어 제목\n"
            "[CONTENT]<p>도입부</p><h2>소제목</h2><p>내용</p>\n\n"
            f"원문:\n{text}"
        )
        try:
            logger.info(f"Gemini 번역 중: {name}...")
            response = self.model.generate_content(prompt)
            raw = response.text

            t_match = re.search(r'\[TITLE\]\s*(.*?)\n', raw + '\n', re.IGNORECASE)
            c_match = re.search(r'\[CONTENT\]\s*(.*)', raw, re.DOTALL | re.IGNORECASE)

            t = t_match.group(1).strip() if t_match else f"{name} 캐릭터 도감"
            c = c_match.group(1).strip() if c_match else raw
            c = re.sub(r'```html|```', '', c).strip()
            c = re.sub(r'<img[^>]*/?>', '', c)
            c = remove_copyright(c)

            # 2차 검수
            logger.info("2차 검수 중... (7초 대기)")
            time.sleep(7)
            c, t = self.review_article(t, c, source)

            return {'title': t, 'content': c}
        except Exception as e:
            logger.error(f"❌ 번역 에러: {e}")
            return None

    def review_article(self, title: str, content: str, source: str):
        source_label = "포켓몬 도감" if source == 'pokemon' else "산리오 캐릭터 도감"
        review_prompt = (
            f"아래 한국어 {source_label} 글을 검토하고 부족한 부분만 보완하세요.\n"
            "체크 항목:\n"
            "- 도입부가 캐릭터를 명확하게 소개하는가\n"
            "- h2 소제목이 3개 이상인가\n"
            "- 글자수가 1000자 이상인가\n"
            "- 핵심 정보(타입/프로필/능력 등)가 빠짐없이 포함됐는가\n"
            "- 자연스러운 한국어인가\n"
            "부족한 부분만 보완해서 완성본을 반환하세요.\n\n"
            "반드시 아래 형식으로만 답하세요:\n"
            "[TITLE]제목\n"
            "[CONTENT]본문 HTML\n\n"
            f"[TITLE]{title}\n"
            f"[CONTENT]{content}"
        )
        try:
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
    def save_to_supabase(self, url: str, title: str, content: str, img_url: str, source: str) -> bool:
        try:
            res = self.supabase.table(TABLE_NAME) \
                .select('id') \
                .eq('original_url', url) \
                .execute()
            if res.data:
                logger.info(f"이미 저장됨 (스킵): {url}")
                return False

            self.supabase.table(TABLE_NAME).insert({
                'title':        title,
                'content_html': content,
                'original_url': url,
                'img_url':      img_url,
                'status':       'draft',
                'source':       source,
                'created_at':   datetime.utcnow().isoformat(),
            }).execute()
            logger.info(f"✅ Supabase 저장: {title[:40]}")
            return True
        except Exception as e:
            logger.error(f"❌ Supabase 저장 실패: {e}")
            return False

    # -------------------------------------------------------------------------
    # 메인 실행
    # -------------------------------------------------------------------------
    def run(self):
        logger.info("캐릭터 도감 크롤러 시작")
        saved = 0

        # 포켓몬 수집
        pokemon_urls = self.get_pokemon_urls()
        logger.info(f"포켓몬 수집 대상: {len(pokemon_urls)}개")
        for url in pokemon_urls:
            logger.info(f"▶ [포켓몬] {url.split('/wiki/')[-1]}")
            data = self.fetch_pokemon(url)
            if not data:
                continue
            translated = self.translate_character(data)
            if not translated:
                continue
            if self.save_to_supabase(url, translated['title'], translated['content'], data['img_url'], 'pokemon'):
                self.history[url] = datetime.now().isoformat()
                self.save_history()
                saved += 1
            time.sleep(3)

        # 산리오 수집
        sanrio_urls = self.get_sanrio_urls()
        logger.info(f"산리오 수집 대상: {len(sanrio_urls)}개")
        for url in sanrio_urls:
            logger.info(f"▶ [산리오] {url.split('/wiki/')[-1]}")
            data = self.fetch_sanrio(url)
            if not data:
                continue
            translated = self.translate_character(data)
            if not translated:
                continue
            if self.save_to_supabase(url, translated['title'], translated['content'], data['img_url'], 'sanrio'):
                self.history[url] = datetime.now().isoformat()
                self.save_history()
                saved += 1
            time.sleep(3)

        logger.info(f"완료: {saved}개 저장")


if __name__ == "__main__":
    CharacterCrawler().run()
