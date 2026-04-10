# GitHub 빠른 설치 가이드 (Quick Install)

이 가이드는 로컬에서 성공한 Tistory 자동화 시스템을 GitHub Actions 환경으로 배포하는 가장 빠른 방법을 설명합니다.

## 1. 파일 준비
현재 `files/` 디렉토리에 있는 다음 파일들이 핵심입니다:
- `playwright_tistory_core.py`: 자동화 엔진
- `requirements.txt`: 의존성 목록
- `.github/workflows/tistory_auto_post.yml`: 워크플로 설정

## 2. 세션 암호화 (필수)
인증 세션(`auth.json`)을 보안을 위해 GPG로 암호화합니다.
```bash
gpg --symmetric --cipher-algo AES256 auth.json
# 암호를 입력하세요. 이 암호는 GitHub Secrets의 AUTH_JSON_PASSPHRASE에 저장합니다.
```

## 3. GitHub에 푸시
```bash
git add .
git commit -m "feat: implement tistory auto-posting system"
git push origin main
```

## 4. GitHub Actions Secrets 설정
저장소 **Settings > Secrets and variables > Actions**에서 다음을 추가하세요:
- `SUPABASE_URL`: Supabase 프로젝트 URL
- `SUPABASE_KEY`: Supabase anon 키
- `TISTORY_ID`: 블로그 서브도메인 (예: irunaru)
- `TISTORY_PASSWORD`: 티스토리/카카오 비밀번호
- `AUTH_JSON_PASSPHRASE`: GPG 암호화 시 사용한 비밀번호

이제 모든 준비가 끝났습니다! 🚀
