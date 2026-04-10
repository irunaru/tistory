#!/bin/bash

echo "🚀 GitHub 푸시 준비를 시작합니다..."

# 1. 아카이브 및 디렉토리 정리
if [ ! -d ".github/workflows" ]; then
    mkdir -p .github/workflows
fi

if [ -f "tistory_auto_post.yml" ]; then
    mv tistory_auto_post.yml .github/workflows/
fi

# 2. 필수 파일 확인
FILES=("playwright_tistory_core.py" "requirements.txt" ".github/workflows/tistory_auto_post.yml")
for FILE in "${FILES[@]}"; do
    if [ ! -f "$FILE" ]; then
        echo "❌ 오류: $FILE 이 누락되었습니다."
        exit 1
    fi
done

# 3. auth.json 암호화 안내
if [ -f "auth.json" ]; then
    echo "🔐 auth.json 파일이 발견되었습니다."
    echo "보안을 위해 GPG 암호화(auth.json.gpg 생성)를 권장합니다."
    echo "명령어: gpg --symmetric --cipher-algo AES256 auth.json"
fi

# 4. Git 처리
echo "📝 Git 변경사항 추가 중..."
git add playwright_tistory_core.py requirements.txt setup_tistory_auth.py .github/workflows/tistory_auto_post.yml .gitignore README.md TISTORY_SETUP_GUIDE.md GITHUB_INSTALL_QUICK.md

echo "💾 커밋 중..."
git commit -m "feat: tistory automation system implementation"

echo "📤 푸시 중 (인증 필요 시 토큰을 입력하세요)..."
git push

echo "✅ 완료! 이제 GitHub 저장소 환경설정(Secrets)을 마무리하세요."
