FROM node:22-bookworm-slim

ARG OCR_PACKAGE_VERSION=1.7.14
ARG OPENCODE_PACKAGE_VERSION=1.18.4
ENV APP_ROOT=/app \
    REVIEW_MODE=shell \
    REVIEW_OUTPUT_DIR=review-output \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_AUDIT=false

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      bash \
      ca-certificates \
      curl \
      git \
      python3 \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g \
      "@alibaba-group/open-code-review@${OCR_PACKAGE_VERSION}" \
      "opencode-ai@${OPENCODE_PACKAGE_VERSION}" \
    && npm cache clean --force

WORKDIR /app

COPY .opencode/ .opencode/
COPY docker/ docker/
COPY gitlab-merge-review/ gitlab-merge-review/
COPY scripts/ scripts/
COPY templates/ templates/
COPY opencode.json opencode.json
COPY README.md README.md

RUN chmod +x docker/entrypoint.sh \
    && chmod +x gitlab-merge-review/scripts/run-gitlab-merge-review.sh \
    && python3 -m py_compile \
      scripts/generate_review_docx.py \
      scripts/gitlab_context.py \
      scripts/run_platform_offline_review.py \
      gitlab-merge-review/scripts/evaluate_review.py \
      gitlab-merge-review/scripts/post_gitlab_review_comments.py

WORKDIR /workspace

ENTRYPOINT ["/app/docker/entrypoint.sh"]
