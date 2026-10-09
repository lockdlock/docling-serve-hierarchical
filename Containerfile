ARG BASE_IMAGE=ghcr.io/docling-project/docling-serve:latest

FROM ${BASE_IMAGE}

ARG BAKE_MODELS=0

USER 0

# Install docling-hierarchical-pdf into the existing uv environment
RUN /opt/app-root/bin/pip install --quiet \
    git+https://github.com/krrome/docling-hierarchical-pdf.git

# Patch manager.py to add hierarchical post-processing.
# The script searches for unique anchor strings; it fails loudly if any anchor
# is missing (e.g. after an upstream restructure) and validates syntax before writing.
#
# custom_hierarchy.py は DOCLING_SERVE_HEADING_RULES_ENABLED=true と
# DOCLING_SERVE_HEADING_RULES_CSV=<CSVパス> でルール表CSVを直接読み込み、変換結果をキャッシュして
# 見出しレベルを付け直す(csv_to_hierarchy.py がCSV変換を担当)。
# 無効/未設定の場合は素のdocling出力のまま。HIERARCHY_MODE=auto で旧来の自動推定を使う。
COPY patch_manager.py /tmp/patch_manager.py
COPY custom_hierarchy.py /tmp/custom_hierarchy.py
COPY csv_to_hierarchy.py /tmp/csv_to_hierarchy.py
RUN DOCLING_JOBKIT_CONVERT_PATH=$(/opt/app-root/bin/python -c "import docling_jobkit.convert; print(docling_jobkit.convert.__path__[0])") && \
    cp /tmp/custom_hierarchy.py "$DOCLING_JOBKIT_CONVERT_PATH/custom_hierarchy.py" && \
    cp /tmp/csv_to_hierarchy.py "$DOCLING_JOBKIT_CONVERT_PATH/csv_to_hierarchy.py" && \
    /opt/app-root/bin/python /tmp/patch_manager.py \
        "$DOCLING_JOBKIT_CONVERT_PATH/manager.py"

USER 1001

# Pre-download docling's default models so the image is ready to use immediately.
# Skipped in the base variant (BAKE_MODELS=0).
RUN if [ "${BAKE_MODELS}" = "1" ]; then \
    /opt/app-root/bin/python -c \
      "from docling.document_converter import DocumentConverter; DocumentConverter()"; \
fi
