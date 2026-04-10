# Tistory 자동 글쓰기 시스템 구현 가이드

## 목차
1. [아키텍처 개요](#아키텍처-개요)
2. [Supabase 설정](#supabase-설정)
3. [환경 변수 설정](#환경-변수-설정)
4. [GitHub Actions 설정](#github-actions-설정)
5. [로컬 테스트 및 검증](#로컬-테스트-및-검증)
6. [운영 및 모니터링](#운영-및-모니터링)
7. [트러블슈팅](#트러블슈팅)

---

## 아키텍처 개요

### 전체 흐름

```
┌─────────────────┐
│   Supabase      │
│  (characters    │
│   table)        │
└────────┬────────┘
         │ status='ready'
         ▼
┌─────────────────────────────┐
│   GitHub Actions            │
│  (매 4시간마다 실행)          │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│   Playwright Core           │
│  - Stealth Mode             │
│  - Session (auth.json)      │
│  - Anti-bot Protection      │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────────────────┐
│   Tistory.com               │
│  - 로그인                     │
│  - 글 작성 & 예약 발행         │
└────────┬────────────────────┘
         │
         ▼
┌─────────────────┐
│   Supabase      │
│  (status→posted)│
└─────────────────┘
```

### 핵심 기능

- **Stealth Mode**: Playwright 자동화 감지 회피
- **세션 유지** (auth.json): 반복 로그인 불필요
- **예약 발행**: 게시 시간을 불규칙하게 분산
- **상태 관리**: Supabase에서 포스트 생명주기 추적
- **GitHub Actions**: 서버리스 정기 실행

---

## Supabase 설정

### 1. 테이블 생성: `characters`

```sql
-- 포스트 데이터 및 상태 관리
CREATE TABLE IF NOT EXISTS characters (
  -- 기본 필드
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
  
  -- 포스트 콘텐츠
  title TEXT NOT NULL,
  content_html TEXT NOT NULL,
  category VARCHAR(100),
  tags TEXT[] DEFAULT ARRAY[]::text[],
  
  -- 상태 추적
  status VARCHAR(20) DEFAULT 'draft',
  -- 상태값: draft → ready → posted / failed
  
  -- 게시 정보
  posted_at TIMESTAMP WITH TIME ZONE,
  scheduled_time TIMESTAMP WITH TIME ZONE,
  
  -- 에러 로깅
  error TEXT,
  retry_count INTEGER DEFAULT 0,
  last_error_at TIMESTAMP WITH TIME ZONE,
  
  -- 메타데이터
  metadata JSONB DEFAULT '{}'::jsonb
);

-- 인덱스 (조회 성능)
CREATE INDEX idx_characters_status ON characters(status);
CREATE INDEX idx_characters_posted_at ON characters(posted_at DESC);
CREATE INDEX idx_characters_created_at ON characters(created_at DESC);

-- Row Level Security (RLS) 설정
ALTER TABLE characters ENABLE ROW LEVEL SECURITY;

-- 정책: 읽기는 모두 허용, 쓰기는 인증된 사용자만
CREATE POLICY "Enable read access for all users" ON characters
  FOR SELECT USING (true);

CREATE POLICY "Enable insert for authenticated users only" ON characters
  FOR INSERT WITH CHECK (auth.role() = 'authenticated');

CREATE POLICY "Enable update for authenticated users only" ON characters
  FOR UPDATE USING (auth.role() = 'authenticated');

-- 자동 updated_at 업데이트 (트리거)
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_characters_updated_at
  BEFORE UPDATE ON characters
  FOR EACH ROW
  EXECUTE FUNCTION update_updated_at_column();
```

### 2. API 키 생성

1. Supabase 대시보드 → Settings → API
2. **anon (public)** 키 복사 → `SUPABASE_KEY`
3. **Project URL** 복사 → `SUPABASE_URL`

---

## 환경 변수 설정

### 1. 로컬 개발 환경 (.env 파일)

```bash
# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...

# Tistory
TISTORY_ID=your-tistory-blog-name
TISTORY_PASSWORD=your-tistory-password

# 로그 레벨
LOG_LEVEL=INFO
```

**주의**: `.env` 파일은 절대 git에 커밋하지 말 것!

```bash
# .gitignore에 추가
echo ".env" >> .gitignore
echo "auth.json" >> .gitignore
```

### 2. GitHub Actions Secrets 설정

GitHub 저장소 → Settings → Secrets and variables → Actions

다음 secrets 추가:

| Secret 이름 | 값 |
|---------|---|
| `SUPABASE_URL` | https://your-project.supabase.co |
| `SUPABASE_KEY` | eyJhbGciOiJIUzI1NiIs... |
| `TISTORY_ID` | your-blog-name |
| `TISTORY_PASSWORD` | your-password |
| `AUTH_JSON_PASSPHRASE` | 임의 문자열 (auth.json 암호화용) |

**암호화 방식**:
```bash
# 로컬에서 auth.json 암호화 (처음 한 번)
gpg --symmetric --cipher-algo AES256 auth.json
# → auth.json.gpg 생성

# GitHub에 auth.json.gpg만 커밋
git add auth.json.gpg
git commit -m "chore: add encrypted auth.json"
git push
```

---

## GitHub Actions 설정

### 1. 워크플로 파일 배치

```
your-repo/
├── .github/
│   └── workflows/
│       └── tistory_auto_post.yml    # ← 여기에 배치
├── playwright_tistory_core.py        # ← 핵심 모듈
├── setup_tistory_auth.py            # ← 초기화 및 테스트
└── .gitignore
```

### 2. 파일 배치 및 커밋

```bash
# 워크플로 파일 생성
mkdir -p .github/workflows
cp tistory_auto_post.yml .github/workflows/

# 파이썬 모듈 배치
cp playwright_tistory_core.py .
cp setup_tistory_auth.py .

# Secrets 설정 후 커밋
git add .github/workflows/tistory_auto_post.yml
git add playwright_tistory_core.py
git add setup_tistory_auth.py
git commit -m "feat: add tistory auto-posting workflow"
git push
```

### 3. 수동 테스트 (GitHub Actions UI)

1. GitHub 저장소 → Actions
2. "Tistory 자동 글쓰기" 워크플로 선택
3. "Run workflow" → "Run workflow" 클릭
4. 로그 확인

---

## 로컬 테스트 및 검증

### Phase 1: 초기 인증 (첫 로그인)

```bash
# 의존성 설치
pip install playwright supabase python-dotenv

# Playwright 브라우저 설치
playwright install chromium
playwright install-deps chromium

# 초기 인증 수행 (대화형)
python setup_tistory_auth.py --init
```

**결과**:
- `auth.json` 파일 생성
- Tistory 로그인 성공 확인

### Phase 2: 세션 검증

```bash
# 저장된 세션이 유효한지 확인
python setup_tistory_auth.py --validate
```

**예상 출력**:
```
2025-XX-XX XX:XX:XX - ... - INFO - ✓ 세션 유효함. 자동 글쓰기 준비 완료
```

### Phase 3: 테스트 글쓰기

```bash
# 테스트 글 작성 (드래프트)
python setup_tistory_auth.py --test-write
```

**확인 사항**:
- Tistory 대시보드에서 "[테스트] Playwright..." 글 확인
- 드래프트 상태로 저장되어 있는지 확인

### Phase 4: 전체 테스트 (권장)

```bash
# 초기 인증 → 세션 검증 → 테스트 글쓰기 일괄 실행
python setup_tistory_auth.py --all
```

### Phase 5: Supabase 연동 테스트

```python
# 테스트 포스트 데이터 생성
from supabase import create_client

supabase = create_client(
    'https://your-project.supabase.co',
    'your-api-key'
)

# status='ready'인 테스트 포스트 생성
supabase.table('characters').insert({
    'title': '[테스트] Supabase 연동',
    'content_html': '<p>테스트 콘텐츠</p>',
    'category': '테스트',
    'tags': ['테스트', 'playwright'],
    'status': 'ready'
}).execute()
```

그 후 `python playwright_tistory_core.py` 실행:

```bash
python playwright_tistory_core.py
```

**예상 결과**:
- Supabase의 포스트 `status` → `posted`로 변경
- Tistory에 새 글 게시됨

---

## 운영 및 모니터링

### 1. 일일 포스트 제한

기본 설정 (playwright_tistory_core.py에서):
- 최대 **3개/일** (MAX_POSTS_PER_DAY = 3)
- 게시 간격: **2~6시간** (불규칙)
- 활동 시간: **06:00 ~ 22:00** (금지 시간대 회피)

조정이 필요하면:
```python
class Config:
    MAX_POSTS_PER_DAY = 3          # ← 수정
    MIN_POST_INTERVAL_HOURS = 2
    MAX_POST_INTERVAL_HOURS = 6
```

### 2. 스케줄 확인

```bash
# 게시 스케줄 예시 확인
python setup_tistory_auth.py --test-schedule
```

### 3. Supabase 대시보드 모니터링

1. Supabase → Table Editor → `characters`
2. 다음 열 확인:
   - `status`: draft / ready / posted / failed
   - `posted_at`: 게시 시간
   - `scheduled_time`: 예약된 발행 시간
   - `error`: 실패 이유

### 4. GitHub Actions 로그

1. GitHub 저장소 → Actions
2. 최근 실행 클릭
3. "Tistory auto-posting" 작업 로그 확인

**주요 로그**:
```
[INFO] ========================================================================================
[INFO] Tistory 자동 글쓰기 시작
[INFO] ========================================================================================
[INFO] 게시 준비 포스트 조회: 1개
[INFO] Stealth 모드 브라우저 생성 완료
[INFO] 인증 상태 확인 완료
[INFO] 글쓰기 시작: [제목]...
[INFO] 본문 HTML 주입 완료
[INFO] 글 발행 완료
[INFO] 포스트 [id] 상태: posted
```

---

## 트러블슈팅

### 문제 1: "세션 파일 없음"

**원인**: auth.json이 없음

**해결**:
```bash
python setup_tistory_auth.py --init
```

### 문제 2: "로그인 실패"

**원인**: 
- TISTORY_ID, TISTORY_PASSWORD 오류
- Tistory 2단계 인증 설정되어 있음

**해결**:
- 환경 변수 확인: `echo $TISTORY_ID`
- Tistory 계정 설정에서 2단계 인증 비활성화 (또는 앱 비밀번호 사용)

### 문제 3: "글 발행 실패"

**원인**:
- Tistory UI 변경
- 셀렉터 오류 (id/class 변경)

**해결**:
1. Playwright Inspector로 디버깅:
```bash
PWDEBUG=1 python setup_tistory_auth.py --test-write
```

2. 개발자 도구에서 현재 셀렉터 확인
3. `TistoryWriter` 클래스의 셀렉터 업데이트

### 문제 4: GitHub Actions에서만 실패

**원인**:
- 네트워크 격리 문제
- 타이밍 이슈 (로드 대기 부족)

**해결**:
```python
# 대기 시간 증가
await page.wait_for_load_state('networkidle', timeout=20000)  # 기본 15000ms → 20000ms
```

### 문제 5: "AdSense 승인 거절"

**원인**:
- 자동 생성 콘텐츠 탐지
- 게시 패턴이 비정상 (규칙적, 시간차 없음)

**해결**:
- **예약 발행** 반드시 사용 (불규칙 간격)
- 게시 빈도 줄이기 (1~2개/일)
- 콘텐츠 품질 검증 (Gemini 프롬프트 개선)

---

## 보안 주의사항

### 1. auth.json 관리

- **절대 평문으로 git에 커밋하지 말 것**
- GPG로 암호화: `gpg --symmetric --cipher-algo AES256 auth.json`
- GitHub Secrets의 `AUTH_JSON_PASSPHRASE`는 안전하게 관리

### 2. 환경 변수

- GitHub Secrets에 저장된 값은 로그에 안 나타남
- 로컬 `.env`는 `.gitignore`에 추가

### 3. Tistory 비밀번호

- 전용 계정 사용 권장
- 고유한 비밀번호 설정
- 정기적으로 변경

---

## 다음 단계

1. ✅ 로컬 환경에서 `--all` 테스트 수행
2. ✅ Supabase 테이블 생성 및 API 키 설정
3. ✅ GitHub Secrets 설정
4. ✅ 워크플로 파일 배치 및 푸시
5. ✅ GitHub Actions에서 수동 실행 테스트
6. ✅ 첫 포스트 게시 확인
7. ✅ Tistory & AdSense 정상 작동 확인
8. ✅ 자동 스케줄링 시작

---

## 참고

- Playwright 문서: https://playwright.dev
- Supabase 문서: https://supabase.com/docs
- GitHub Actions: https://docs.github.com/en/actions
