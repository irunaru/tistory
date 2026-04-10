# Tistory 자동 글쓰기 시스템 구현 체크리스트

## Phase 0: 사전 준비 (30분)

### 저장소 준비
- [ ] GitHub 저장소 생성 또는 선택
- [ ] 로컬 저장소 클론
  ```bash
  git clone https://github.com/your-user/your-repo.git
  cd your-repo
  ```

### 파이썬 환경 설정
- [ ] Python 3.11 이상 설치 확인
  ```bash
  python --version
  ```
- [ ] 가상 환경 생성
  ```bash
  python -m venv venv
  source venv/bin/activate  # Windows: venv\Scripts\activate
  ```

### 의존성 설치
- [ ] 패키지 설치
  ```bash
  pip install -r requirements.txt
  ```
- [ ] Playwright 브라우저 설치
  ```bash
  playwright install chromium
  playwright install-deps chromium
  ```

---

## Phase 1: 로컬 개발 환경 설정 (1시간)

### 1.1 환경 변수 설정
- [ ] `.env` 파일 생성
  ```bash
  cat > .env << 'EOF'
  SUPABASE_URL=https://your-project.supabase.co
  SUPABASE_KEY=eyJhbGciOiJIUzI1NiIs...
  TISTORY_ID=your-blog-name
  TISTORY_PASSWORD=your-password
  LOG_LEVEL=INFO
  EOF
  ```

- [ ] `.gitignore` 업데이트
  ```bash
  echo ".env" >> .gitignore
  echo "auth.json" >> .gitignore
  echo "auth.json.gpg" >> .gitignore
  echo "venv/" >> .gitignore
  ```

### 1.2 Supabase 설정

#### Supabase 대시보드 접속
- [ ] https://app.supabase.com 로그인 (또는 회원가입)
- [ ] 프로젝트 생성 (또는 기존 프로젝트 사용)

#### API 키 확인
- [ ] Settings → API 탭 접속
- [ ] **Project URL** 복사 → `SUPABASE_URL`
- [ ] **anon (public)** 키 복사 → `SUPABASE_KEY`

#### 테이블 생성
- [ ] SQL Editor 탭 접속
- [ ] 아래 SQL 실행 (TISTORY_SETUP_GUIDE.md의 "1. 테이블 생성" 참조)
  ```sql
  CREATE TABLE IF NOT EXISTS characters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    ...
  );
  ```

- [ ] 테이블 생성 확인
  - Table Editor → `characters` 테이블 확인

### 1.3 로컬 테스트

#### 단계 1: 초기 인증 (첫 로그인)
```bash
python setup_tistory_auth.py --init
```

**예상 결과**:
- 브라우저 창이 열리고 Tistory 로그인 페이지 표시
- 자동으로 로그인 수행
- `auth.json` 파일 생성
- 터미널에 "초기 인증 완료" 메시지

**체크리스트**:
- [ ] 로그인 성공
- [ ] `auth.json` 파일 생성됨

---

#### 단계 2: 세션 검증
```bash
python setup_tistory_auth.py --validate
```

**예상 결과**:
```
✓ 세션 유효함. 자동 글쓰기 준비 완료
```

**체크리스트**:
- [ ] 세션 검증 성공

---

#### 단계 3: 테스트 글쓰기 (드래프트)
```bash
python setup_tistory_auth.py --test-write
```

**예상 결과**:
- Playwright 자동화로 Tistory에 접속
- 테스트 글("[테스트] Playwright...") 작성
- 터미널에 "글 발행 완료" 메시지

**체크리스트**:
- [ ] 테스트 글쓰기 성공
- [ ] Tistory 대시보드에서 테스트 글 확인
- [ ] 드래프트 상태인지 확인 (아직 발행 안 됨)

---

#### 단계 4: 전체 테스트 (권장)
```bash
python setup_tistory_auth.py --all
```

**체크리스트**:
- [ ] 초기 인증 → 세션 검증 → 테스트 글쓰기 모두 성공

---

## Phase 2: Supabase 연동 테스트 (30분)

### 2.1 테스트 포스트 생성
```python
from supabase import create_client

supabase = create_client(
    'https://your-project.supabase.co',
    'your-api-key'
)

# status='ready'인 테스트 포스트 생성
result = supabase.table('characters').insert({
    'title': '[테스트] Supabase 자동 글쓰기',
    'content_html': '<h2>Supabase 연동 테스트</h2><p>자동 글쓰기 시스템이 정상 작동합니다.</p>',
    'category': '테스트',
    'tags': ['테스트', 'supabase'],
    'status': 'ready'
}).execute()

print(f"포스트 생성: {result.data[0]['id']}")
```

**체크리스트**:
- [ ] 포스트 생성 성공
- [ ] Supabase 대시보드에서 `characters` 테이블에 새 행 확인

### 2.2 자동 글쓰기 실행
```bash
python playwright_tistory_core.py
```

**예상 결과**:
```
[INFO] ========================================================================================
[INFO] Tistory 자동 글쓰기 시작
[INFO] ========================================================================================
[INFO] 게시 준비 포스트 1개 조회
[INFO] Stealth 모드 브라우저 생성 완료
[INFO] 인증 상태 확인 완료
[INFO] 글쓰기 시작: [테스트] Supabase 자동 글쓰기...
[INFO] 본문 HTML 주입 완료
[INFO] 글 발행 완료
[INFO] 포스트 [...] 상태: posted
[INFO] ========================================================================================
```

**체크리스트**:
- [ ] 자동 글쓰기 완료
- [ ] Tistory 대시보드에서 새 글 확인 (발행됨)
- [ ] Supabase에서 포스트 `status` → `posted`로 변경 확인
- [ ] `posted_at`, `scheduled_time` 필드 확인

---

## Phase 3: GitHub Actions 설정 (1시간)

### 3.1 GitHub Secrets 설정

1. GitHub 저장소 접속
2. **Settings** → **Secrets and variables** → **Actions**
3. **New repository secret** 버튼 클릭
4. 다음 secrets 추가:

| Secret 이름 | 값 | 예시 |
|-------------|---|------|
| `SUPABASE_URL` | Supabase Project URL | `https://abc123.supabase.co` |
| `SUPABASE_KEY` | Supabase anon key | `eyJhbGciOiJIUzI1NiIs...` |
| `TISTORY_ID` | Tistory 블로그 ID | `my-blog-name` |
| `TISTORY_PASSWORD` | Tistory 비밀번호 | `secure_password_123` |
| `AUTH_JSON_PASSPHRASE` | auth.json 암호화 패스 | `random_passphrase_abc123` |

**체크리스트**:
- [ ] 모든 secrets 추가 완료
- [ ] 각 secret 값이 정확한지 확인

### 3.2 워크플로 파일 배치

```bash
# 디렉토리 생성
mkdir -p .github/workflows

# 워크플로 파일 배치 (이미 생성됨)
cp tistory_auto_post.yml .github/workflows/

# 파이썬 모듈 배치
cp playwright_tistory_core.py .
cp setup_tistory_auth.py .
cp requirements.txt .
```

**체크리스트**:
- [ ] `.github/workflows/tistory_auto_post.yml` 생성
- [ ] `playwright_tistory_core.py` 배치
- [ ] `setup_tistory_auth.py` 배치
- [ ] `requirements.txt` 배치

### 3.3 초기 auth.json 준비

```bash
# 로컬에서 생성된 auth.json을 암호화
gpg --symmetric --cipher-algo AES256 auth.json
# → auth.json.gpg 생성
# → 암호 입력 (AUTH_JSON_PASSPHRASE와 동일)

# 원본 auth.json 삭제 (보안)
rm auth.json

# GitHub에 커밋
git add auth.json.gpg
git add .github/workflows/tistory_auto_post.yml
git add playwright_tistory_core.py
git add setup_tistory_auth.py
git add requirements.txt
git commit -m "feat: add tistory auto-posting workflow with github actions"
git push
```

**체크리스트**:
- [ ] `auth.json.gpg` 생성
- [ ] `auth.json` 삭제됨 (평문 파일 없음)
- [ ] GitHub에 푸시 완료

### 3.4 워크플로 테스트

1. GitHub 저장소 → **Actions** 탭
2. "Tistory 자동 글쓰기" 워크플로 선택
3. **Run workflow** → **Run workflow** 클릭
4. 실행 완료 대기

**실행 로그 확인**:
1. 실행 결과 클릭
2. "Run Tistory auto-posting" 작업 선택
3. 로그 확인

**체크리스트**:
- [ ] 워크플로 실행 성공
- [ ] 로그에 "글 발행 완료" 메시지 있음
- [ ] Tistory에 새 글 게시됨
- [ ] Supabase에서 포스트 status → `posted` 확인

---

## Phase 4: 실제 운영 (지속적)

### 4.1 포스트 생성 자동화

현재는 수동으로 Supabase에 포스트를 삽입해야 함.
다음 단계: Gemini API 연동으로 완전 자동화

```python
# 예시: Gemini API로 콘텐츠 생성 후 Supabase에 삽입
from google.generativeai import client
from supabase import create_client

# 1. Gemini로 콘텐츠 생성
# 2. 생성된 콘텐츠를 Supabase에 status='ready'로 삽입
# 3. GitHub Actions가 자동으로 Tistory에 게시
```

**체크리스트**:
- [ ] 포스트 생성 파이프라인 준비 (prodrone.kr의 `translate_and_post_prodrone.py` 참고)

### 4.2 모니터링 및 유지보수

**주간 체크**:
- [ ] Supabase `characters` 테이블 상태 확인
  - `status='posted'` 포스트 수
  - `status='failed'` 포스트 (에러 확인)
- [ ] Tistory 대시보드
  - 새 글 게시 확인
  - 발행 간격 확인 (불규칙하게 2~6시간 간격)
- [ ] Google AdSense
  - 수익 추적
  - 이상 활동 알림 확인

**월간 체크**:
- [ ] GitHub Actions 실행 로그
  - 실패 작업 확인
  - 재시도 횟수 추적
- [ ] Tistory 트래픽 분석
  - 유입 경로 확인
  - 사용자 행동 분석

### 4.3 문제 대응

**로그인 실패 시**:
```bash
# 세션 갱신
rm auth.json
python setup_tistory_auth.py --init
```

**UI 변경으로 인한 글쓰기 실패 시**:
1. Playwright 디버거로 현재 선택자 확인
   ```bash
   PWDEBUG=1 python setup_tistory_auth.py --test-write
   ```
2. `TistoryWriter` 클래스의 선택자 업데이트
3. GitHub에 커밋 및 푸시

**AdSense 승인 대기 중**:
- 자동화는 **1일 1~2개 포스트** 수준으로 제한
- 게시 시간은 **예약 발행**으로 불규칙하게 분산
- 콘텐츠 품질 유지 (Gemini 프롬프트 개선)

---

## Phase 5: 최적화 및 확장 (장기)

### sim9 가격 비교와의 통합

sim9 프로젝트가 완성되면:
1. sim9 가격 변동 감지
2. "에어포트 버디" 관련 콘텐츠 자동 생성
3. Tistory에 자동 게시
4. 트래픽 → eSIM 판매로 전환

**체크리스트**:
- [ ] sim9 가격 스크래핑 시스템 완성
- [ ] Gemini와의 통합 (콘텐츠 생성)
- [ ] Tistory 자동 글쓰기와 병렬 운영

---

## 체크리스트 완료 시 기대효과

✅ **모든 Phase 완료 시**:
- Tistory 자동 글쓰기 **완전 자동화** ✓
- GitHub Actions로 **서버리스 스케줄링** ✓
- Supabase로 **중앙 상태 관리** ✓
- **예약 발행**으로 AdSense 정책 준수 ✓
- **6개월 내 수익화** 목표 달성 가능 ✓

---

## 참고 자료

- 📖 [TISTORY_SETUP_GUIDE.md](./TISTORY_SETUP_GUIDE.md) - 상세 설정 가이드
- 🔧 [playwright_tistory_core.py](./playwright_tistory_core.py) - 핵심 모듈
- 🧪 [setup_tistory_auth.py](./setup_tistory_auth.py) - 테스트 및 초기화 도구
- 📋 [tistory_auto_post.yml](./tistory_auto_post.yml) - GitHub Actions 워크플로

---

**작성일**: 2025-04-10  
**마지막 업데이트**: 2025-04-10  
**상태**: ✅ 구현 완료, 테스트 대기
